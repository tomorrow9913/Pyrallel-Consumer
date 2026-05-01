# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_worker_runtime.py
# Role: Runs child-process worker loops, task decoding, retries, and completion emission.
# Extend here for worker-process runtime behavior; keep parent orchestration in process_engine.py.
from __future__ import annotations

import logging
import os
import random
import signal
import time
from collections.abc import Callable
from multiprocessing import Queue
from typing import Any, Optional

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_to_dict as _completion_event_to_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    decode_incoming_payloads as _decode_incoming_payloads,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_from_dict as _work_item_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_identity_payload as _work_item_identity_payload,
)
from pyrallel_consumer.logger import LogManager

_SENTINEL = None
_PIPE_SENTINEL = b"__pyrallel_consumer_pipe_sentinel__"


def _receive_task_payload(task_source: Any) -> Any:
    """Handle receive task payload within process worker runtime.

    Args:
        task_source: Queue or pipe endpoint from which the worker receives tasks.

    Returns:
        Any result produced by this function.

    """
    recv_bytes = getattr(task_source, "recv_bytes", None)
    if callable(recv_bytes):
        return recv_bytes()
    return task_source.get()


def _calculate_backoff(
    attempt: int,
    retry_backoff_ms: int,
    exponential_backoff: bool,
    max_retry_backoff_ms: int,
    retry_jitter_ms: int,
) -> float:
    """Calculate backoff delay in seconds with optional exponential scaling and jitter.

    Args:
        attempt: Current retry attempt number.
        retry_backoff_ms: Base retry backoff in milliseconds.
        exponential_backoff: Whether to apply exponential retry backoff.
        max_retry_backoff_ms: Maximum retry backoff in milliseconds.
        retry_jitter_ms: Maximum retry jitter in milliseconds.

    Returns:
        Computed floating-point value.

    """
    if exponential_backoff:
        backoff_ms = retry_backoff_ms * (2 ** (attempt - 1))
    else:
        backoff_ms = retry_backoff_ms

    backoff_ms = min(backoff_ms, max_retry_backoff_ms)
    jitter_ms = random.randint(0, retry_jitter_ms) if retry_jitter_ms > 0 else 0
    total_delay_ms = backoff_ms + jitter_ms
    return total_delay_ms / 1000.0


def _worker_loop(
    task_source: Any,
    completion_queue: Queue,
    registry_event_queue: Queue,
    worker_fn: Callable[[WorkItem], Any],
    process_idx: int,
    execution_config: ExecutionConfig,
    log_queue: Optional[Queue] = None,
):
    """Handle worker loop within process worker runtime.

    Args:
        task_source: Queue or pipe endpoint from which the worker receives tasks.
        completion_queue: Queue used to send completion events to the parent process.
        registry_event_queue: Queue containing worker registry events.
        worker_fn: User worker callable invoked for each work item.
        process_idx: Index of the worker process running this loop.
        execution_config: Execution configuration used by the worker loop.
        log_queue: Optional logging queue configured by the parent process.

    Raises:
        TimeoutError: If the worker task exceeds its configured timeout.

    """
    if log_queue is not None:
        LogManager.setup_worker_logging(log_queue)

    worker_logger = logging.getLogger(__name__)
    worker_logger.debug("ProcessWorker[%d] started.", process_idx)

    tasks_processed = 0
    max_tasks_per_child = execution_config.process_config.max_tasks_per_child
    recycle_jitter_ms = execution_config.process_config.recycle_jitter_ms
    should_exit_after_batch = False

    # Sample jitter once at worker start (constant for this worker's lifetime)
    sampled_jitter = (
        random.randint(0, recycle_jitter_ms) if recycle_jitter_ms > 0 else 0
    )
    recycle_limit = (
        max_tasks_per_child + sampled_jitter if max_tasks_per_child > 0 else None
    )
    while True:
        try:
            item = _receive_task_payload(task_source)
        except EOFError:
            worker_logger.debug(
                "ProcessWorker[%d] input channel closed, shutting down.",
                process_idx,
            )
            break
        worker_received_at = time.monotonic()
        if item is _SENTINEL or item == _PIPE_SENTINEL:
            worker_logger.debug(
                "ProcessWorker[%d] received sentinel, shutting down.",
                process_idx,
            )
            break

        try:
            payloads, timing_metadata = _decode_incoming_payloads(
                item, execution_config.process_config.msgpack_max_bytes
            )
        except Exception as decode_exc:
            completion_event = CompletionEvent(
                id="",
                tp=TopicPartition("", 0),
                offset=-1,
                epoch=0,
                status=CompletionStatus.FAILURE,
                error=str(decode_exc),
                attempt=1,
            )
            try:
                completion_queue.put(  # type: ignore[arg-type]
                    msgpack.packb(
                        _completion_event_to_dict(completion_event), use_bin_type=True
                    )
                )
            except Exception as put_exc:
                worker_logger.error(
                    "Failed to enqueue decode failure in ProcessWorker[%d]: %s",
                    process_idx,
                    put_exc,
                )
            continue

        flush_enqueued_at = timing_metadata.get("flush_enqueued_at")
        if flush_enqueued_at is not None:
            registry_event_queue.put(
                {
                    "kind": "batch_received",
                    "main_to_worker_ipc_seconds": max(
                        0.0, worker_received_at - flush_enqueued_at
                    ),
                }
            )

        batch_run_started_at: Optional[float] = None
        batch_completed_sent = False

        for idx, payload in enumerate(payloads):
            work_item = _work_item_from_dict(payload)
            in_flight_key = (
                process_idx,
                work_item.tp.topic,
                work_item.tp.partition,
                work_item.offset,
            )
            payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
            registry_event_queue.put(
                {
                    "kind": "start",
                    "key": in_flight_key,
                    "payload": payload,
                }
            )
            status = CompletionStatus.FAILURE
            error: Optional[str] = None
            attempt = 0
            fatal_timeout = False

            timeout_ms = getattr(execution_config.process_config, "task_timeout_ms", 0)
            timeout_sec = timeout_ms / 1000.0

            for attempt in range(1, execution_config.max_retries + 1):
                try:
                    if batch_run_started_at is None:
                        batch_run_started_at = time.monotonic()

                    def _run_with_timeout() -> None:
                        """Run with timeout for process worker runtime."""
                        worker_fn(work_item)

                    if timeout_sec > 0:

                        def _handle_timeout(signum, frame):
                            """Handle timeout for process worker runtime.

                            Args:
                                signum: Signal number received by the worker timeout handler.
                                frame: Interpreter frame supplied by the signal handler.

                            Raises:
                                TimeoutError: If the worker task exceeds its configured timeout.

                            """
                            raise TimeoutError(
                                "Task offset=%d exceeded %.3fs"
                                % (work_item.offset, timeout_sec)
                            )

                        signal.signal(signal.SIGALRM, _handle_timeout)
                        signal.setitimer(signal.ITIMER_REAL, timeout_sec)
                        try:
                            _run_with_timeout()
                        finally:
                            signal.setitimer(signal.ITIMER_REAL, 0)
                    else:
                        _run_with_timeout()

                    status = CompletionStatus.SUCCESS
                    error = None
                    worker_logger.debug(
                        "Task offset=%d succeeded on attempt %d in ProcessWorker[%d].",
                        work_item.offset,
                        attempt,
                        process_idx,
                    )
                    break
                except TimeoutError as e:
                    fatal_timeout = True
                    status = CompletionStatus.FAILURE
                    error = str(e)
                    registry_event_queue.put(
                        {
                            "kind": "timeout",
                            "key": in_flight_key,
                            "payload": dict(payload),
                            "attempt": attempt,
                            "timeout_error": error,
                        }
                    )
                    worker_logger.error(
                        "Task offset=%d timed out after %.3fs in ProcessWorker[%d]: %s",
                        work_item.offset,
                        timeout_sec,
                        process_idx,
                        error,
                    )
                    break
                except Exception as e:
                    status = CompletionStatus.FAILURE
                    error = str(e)
                    if attempt < execution_config.max_retries:
                        backoff_sec = _calculate_backoff(
                            attempt,
                            execution_config.retry_backoff_ms,
                            execution_config.exponential_backoff,
                            execution_config.max_retry_backoff_ms,
                            execution_config.retry_jitter_ms,
                        )
                        worker_logger.warning(
                            "Task offset=%d failed on attempt %d in ProcessWorker[%d], retrying after %.3fs: %s",
                            work_item.offset,
                            attempt,
                            process_idx,
                            backoff_sec,
                            error,
                        )
                        time.sleep(backoff_sec)
                    else:
                        worker_logger.error(
                            "Task offset=%d failed after %d attempts in ProcessWorker[%d]: %s",
                            work_item.offset,
                            attempt,
                            process_idx,
                            error,
                        )

            if not fatal_timeout:
                completion_event = CompletionEvent(
                    id=work_item.id,
                    tp=work_item.tp,
                    offset=work_item.offset,
                    epoch=work_item.epoch,
                    status=status,
                    error=error,
                    attempt=attempt,
                )
                if (
                    not batch_completed_sent
                    and batch_run_started_at is not None
                    and idx == len(payloads) - 1
                ):
                    registry_event_queue.put(
                        {
                            "kind": "batch_completed",
                            "worker_exec_seconds": max(
                                0.0, time.monotonic() - batch_run_started_at
                            ),
                        }
                    )
                    batch_completed_sent = True
                packed_completion = msgpack.packb(
                    _completion_event_to_dict(
                        completion_event,
                        extra_fields={"completion_enqueued_at": time.monotonic()},
                    ),
                    use_bin_type=True,
                )
                try:
                    completion_queue.put(packed_completion)
                except Exception as put_exc:
                    worker_logger.error(
                        "Failed to enqueue completion for offset=%d in ProcessWorker[%d]: %s",
                        work_item.offset,
                        process_idx,
                        put_exc,
                    )
                finally:
                    registry_event_queue.put(
                        {
                            "kind": "done",
                            "key": in_flight_key,
                            "payload": _work_item_identity_payload(payload),
                        }
                    )

            # Check worker recycling after task completion
            if recycle_limit is not None:
                tasks_processed += 1
                if tasks_processed >= recycle_limit:
                    worker_logger.debug(
                        "ProcessWorker[%d] recycling after %d tasks (limit=%d, jitter=%d)",
                        process_idx,
                        tasks_processed,
                        max_tasks_per_child,
                        sampled_jitter,
                    )
                    remaining = payloads[idx + 1 :]
                    if remaining:
                        packed_remaining = msgpack.packb(remaining, use_bin_type=True)
                        send_bytes = getattr(task_source, "send_bytes", None)
                        if callable(send_bytes):
                            send_bytes(packed_remaining)
                        else:
                            task_source.put(packed_remaining)
                    should_exit_after_batch = True

            if fatal_timeout:
                worker_logger.error(
                    "ProcessWorker[%d] exiting due to task timeout; parent will respawn",
                    process_idx,
                )
                os._exit(1)

            if should_exit_after_batch:
                break

        if batch_run_started_at is not None and not batch_completed_sent:
            registry_event_queue.put(
                {
                    "kind": "batch_completed",
                    "worker_exec_seconds": max(
                        0.0, time.monotonic() - batch_run_started_at
                    ),
                }
            )

        if should_exit_after_batch:
            break

    worker_logger.debug("ProcessWorker[%d] shutdown complete.", process_idx)


receive_task_payload = _receive_task_payload
calculate_backoff = _calculate_backoff
worker_loop = _worker_loop
SENTINEL = _SENTINEL
PIPE_SENTINEL = _PIPE_SENTINEL
