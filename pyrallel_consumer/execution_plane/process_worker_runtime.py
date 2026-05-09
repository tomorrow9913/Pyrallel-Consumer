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
from collections.abc import Callable, Mapping
from multiprocessing import Queue
from typing import Any, Optional, cast

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    BatchCompletion,
    CompletionEvent,
    CompletionStatus,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.batch_result import normalize_batch_worker_result
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_to_dict as _completion_event_to_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    decode_incoming_payloads as _decode_incoming_payloads,
)
from pyrallel_consumer.execution_plane.process_codec import (
    serialize_batch_completion_payload as _serialize_batch_completion_payload,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_from_dict as _work_item_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_identity_payload as _work_item_identity_payload,
)
from pyrallel_consumer.execution_plane.worker_spec import WorkerSpec
from pyrallel_consumer.logger import LogManager
from pyrallel_consumer.worker import (
    BatchWorkerContractError,
    BatchWorkerResult,
    _bound_batch_worker_error_reason,
)

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


def _flush_route_batch_completion(
    *,
    completion_queue: Queue,
    registry_event_queue: Queue,
    worker_logger: logging.Logger,
    process_idx: int,
    route_batch_id: object,
    route_identity: tuple[Any, ...] | None,
    batch_completion_results: list[CompletionEvent],
) -> None:
    """Flush executed route-batch prefix results to the parent completion queue."""
    if not batch_completion_results:
        return
    batch_completion = BatchCompletion(
        batch_id=str(route_batch_id),
        route_identity=route_identity if route_identity is not None else (),
        results=batch_completion_results,
    )
    try:
        completion_queue.put(  # type: ignore[arg-type]
            _serialize_batch_completion_payload(
                batch_completion,
                completion_enqueued_at=time.monotonic(),
            )
        )
    except Exception as put_exc:
        registry_event_queue.put(
            {
                "kind": "batch_completion_send_failed",
                "batch_id": str(route_batch_id),
                "error": str(put_exc),
            }
        )
        worker_logger.error(
            "Failed to enqueue batch completion for batch_id=%s in ProcessWorker[%d]: %s",
            route_batch_id,
            process_idx,
            put_exc,
        )
        for completion_event in batch_completion_results:
            try:
                completion_queue.put(
                    msgpack.packb(
                        _completion_event_to_dict(
                            completion_event,
                            extra_fields={"completion_enqueued_at": time.monotonic()},
                        ),
                        use_bin_type=True,
                    )
                )
            except Exception as fallback_exc:
                worker_logger.error(
                    "Failed to enqueue fallback completion for offset=%d in ProcessWorker[%d]: %s",
                    completion_event.offset,
                    process_idx,
                    fallback_exc,
                )


def _ordered_prefix_blocked_tail_payloads(
    *,
    payloads: list[dict[str, Any]],
    result: BatchWorkerResult,
) -> list[dict[str, Any]]:
    """Return public batch-worker tail payloads marked as not-started."""
    if not isinstance(result, Mapping):
        return []
    saw_failure = False
    tail_payloads: list[dict[str, Any]] = []
    for payload in payloads:
        item_id = str(payload.get("id", ""))
        outcome = result.get(item_id)
        if outcome is None:
            continue
        if saw_failure:
            if outcome.status == "ordered_prefix_blocked":
                tail_payloads.append(dict(payload))
            continue
        if outcome.status == "failure":
            saw_failure = True
    return tail_payloads


def _worker_loop(
    task_source: Any,
    completion_queue: Queue,
    registry_event_queue: Queue,
    worker_fn: Callable[[WorkItem], Any] | WorkerSpec,
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
        route_batch_id = timing_metadata.get("route_batch_id")
        route_identity = timing_metadata.get("route_identity")
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
        batch_completion_results: list[CompletionEvent] = []
        deferred_done_events: list[dict[str, Any]] = []
        if route_batch_id is not None:
            registry_event_queue.put(
                {
                    "kind": "batch_start",
                    "batch_id": route_batch_id,
                    "worker_index": process_idx,
                    "item_ids": [str(payload.get("id", "")) for payload in payloads],
                    "item_count": len(payloads),
                }
            )

        if (
            route_batch_id is not None
            and isinstance(worker_fn, WorkerSpec)
            and worker_fn.kind == "batch"
        ):
            pending_items = [_work_item_from_dict(payload) for payload in payloads]
            batch_run_started_at = time.monotonic()
            result: BatchWorkerResult = None
            batch_error: Optional[str] = None
            try:
                batch_callable = cast(
                    Callable[[list[WorkItem]], Any], worker_fn.callable
                )
                timeout_ms = getattr(
                    execution_config.process_config, "task_timeout_ms", 0
                )
                timeout_sec = timeout_ms / 1000.0
                if timeout_sec > 0:

                    def _handle_timeout(signum, frame):
                        """Handle public batch-worker invocation timeout."""
                        raise TimeoutError(
                            "Batch task batch_id=%s exceeded %.3fs"
                            % (route_batch_id, timeout_sec)
                        )

                    signal.signal(signal.SIGALRM, _handle_timeout)
                    signal.setitimer(signal.ITIMER_REAL, timeout_sec)
                    try:
                        maybe_result = batch_callable(list(pending_items))
                    finally:
                        signal.setitimer(signal.ITIMER_REAL, 0)
                else:
                    maybe_result = batch_callable(list(pending_items))
                result = cast(BatchWorkerResult, maybe_result)
            except TimeoutError as exc:
                worker_logger.error(
                    "Batch task batch_id=%s timed out in ProcessWorker[%d]: %s",
                    route_batch_id,
                    process_idx,
                    exc,
                )
                os._exit(1)
            except Exception as exc:
                batch_error = _bound_batch_worker_error_reason(str(exc))

            if batch_error is None:
                batch_runtime = worker_fn.batch_runtime
                if batch_runtime is None:
                    raise RuntimeError("batch worker runtime spec is required")
                try:
                    batch_completion_results = normalize_batch_worker_result(
                        pending_items=pending_items,
                        result=result,
                        ordering_mode=batch_runtime.ordering_mode,
                        attempt=1,
                    )
                except BatchWorkerContractError as exc:
                    registry_event_queue.put(
                        {
                            "kind": "control",
                            "control_kind": "fatal",
                            "error_code": exc.code,
                            "error": exc.reason,
                        }
                    )
                    continue
                blocked_tail_payloads = _ordered_prefix_blocked_tail_payloads(
                    payloads=payloads,
                    result=result,
                )
                if blocked_tail_payloads:
                    registry_event_queue.put(
                        {
                            "kind": "not_started",
                            "reason": "ordered_batch_failure",
                            "batch_id": route_batch_id,
                            "payloads": blocked_tail_payloads,
                        }
                    )
            else:
                batch_completion_results = [
                    CompletionEvent(
                        id=work_item.id,
                        tp=work_item.tp,
                        offset=work_item.offset,
                        epoch=work_item.epoch,
                        status=CompletionStatus.FAILURE,
                        error=batch_error,
                        attempt=1,
                    )
                    for work_item in pending_items
                ]
            registry_event_queue.put(
                {
                    "kind": "batch_completed",
                    "worker_exec_seconds": max(
                        0.0, time.monotonic() - batch_run_started_at
                    ),
                }
            )
            _flush_route_batch_completion(
                completion_queue=completion_queue,
                registry_event_queue=registry_event_queue,
                worker_logger=worker_logger,
                process_idx=process_idx,
                route_batch_id=route_batch_id,
                route_identity=route_identity,
                batch_completion_results=batch_completion_results,
            )
            payload_by_id = {
                str(payload.get("id", "")): payload for payload in payloads
            }
            for completion_event in batch_completion_results:
                payload = payload_by_id.get(completion_event.id)
                if payload is None:
                    continue
                registry_event_queue.put(
                    {
                        "kind": "done",
                        "key": (
                            process_idx,
                            completion_event.tp.topic,
                            completion_event.tp.partition,
                            completion_event.offset,
                        ),
                        "payload": _work_item_identity_payload(payload),
                    }
                )
            continue

        single_worker = cast(Callable[[WorkItem], Any], worker_fn)
        for idx, payload in enumerate(payloads):
            work_item = _work_item_from_dict(payload)
            in_flight_key = (
                process_idx,
                work_item.tp.topic,
                work_item.tp.partition,
                work_item.offset,
            )
            payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
            if route_batch_id is None:
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

            max_child_attempts = (
                1 if route_batch_id is not None else execution_config.max_retries
            )
            for attempt in range(1, max_child_attempts + 1):
                try:
                    if batch_run_started_at is None:
                        batch_run_started_at = time.monotonic()

                    def _run_with_timeout() -> None:
                        """Run with timeout for process worker runtime."""
                        single_worker(work_item)

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
                    error = _bound_batch_worker_error_reason(str(e))
                    if attempt < max_child_attempts:
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
                if route_batch_id is not None:
                    batch_completion_results.append(completion_event)
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
                if route_batch_id is None:
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
                done_event = {
                    "kind": "done",
                    "key": in_flight_key,
                    "payload": _work_item_identity_payload(payload),
                }
                if route_batch_id is None:
                    registry_event_queue.put(done_event)
                else:
                    deferred_done_events.append(done_event)

                if status == CompletionStatus.FAILURE and route_batch_id is not None:
                    remaining_payloads = [dict(entry) for entry in payloads[idx + 1 :]]
                    if remaining_payloads:
                        registry_event_queue.put(
                            {
                                "kind": "not_started",
                                "reason": "ordered_batch_failure",
                                "batch_id": route_batch_id,
                                "payloads": remaining_payloads,
                            }
                        )
                    break

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
                if route_batch_id is not None:
                    _flush_route_batch_completion(
                        completion_queue=completion_queue,
                        registry_event_queue=registry_event_queue,
                        worker_logger=worker_logger,
                        process_idx=process_idx,
                        route_batch_id=route_batch_id,
                        route_identity=route_identity,
                        batch_completion_results=batch_completion_results,
                    )
                    for done_event in deferred_done_events:
                        registry_event_queue.put(done_event)
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

        if route_batch_id is not None and batch_completion_results:
            _flush_route_batch_completion(
                completion_queue=completion_queue,
                registry_event_queue=registry_event_queue,
                worker_logger=worker_logger,
                process_idx=process_idx,
                route_batch_id=route_batch_id,
                route_identity=route_identity,
                batch_completion_results=batch_completion_results,
            )
            for done_event in deferred_done_events:
                registry_event_queue.put(done_event)

        if should_exit_after_batch:
            break

    worker_logger.debug("ProcessWorker[%d] shutdown complete.", process_idx)


receive_task_payload = _receive_task_payload
calculate_backoff = _calculate_backoff
worker_loop = _worker_loop
SENTINEL = _SENTINEL
PIPE_SENTINEL = _PIPE_SENTINEL
