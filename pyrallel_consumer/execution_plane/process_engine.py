from __future__ import annotations

import asyncio
import inspect
import logging
import logging.handlers
import os
import pickle
import queue
import random
import signal
import threading
import time
from collections import deque
from collections.abc import Callable
from multiprocessing import Process, Queue
from typing import Any, Deque, List, Optional

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    WORK_ITEM_POISON_KEY_UNSET,
    CompletionEvent,
    CompletionStatus,
    ProcessBatchMetrics,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_registry_support import (
    ProcessRegistrySupport,
)
from pyrallel_consumer.execution_plane.process_transport import (
    ProcessTransport,
    resolve_route_identity,
)
from pyrallel_consumer.execution_plane.process_transport_shared_queue import (
    SharedQueueProcessTransport,
)
from pyrallel_consumer.execution_plane.process_transport_worker_pipes import (
    WorkerPipesProcessTransport,
)
from pyrallel_consumer.logger import LogManager

SerializedWorkItem = dict[str, Any]
SerializedCompletionEvent = dict[str, Any]
SerializedRegistryEvent = dict[str, Any]
SerializedBatchEnvelope = dict[str, Any]

_SENTINEL = None
_PIPE_SENTINEL = b"__pyrallel_consumer_pipe_sentinel__"
_logger = logging.getLogger(__name__)


def _work_item_to_dict(item: WorkItem) -> SerializedWorkItem:
    payload: SerializedWorkItem = {
        "id": item.id,
        "topic": item.tp.topic,
        "partition": item.tp.partition,
        "offset": item.offset,
        "epoch": item.epoch,
        "key": item.key,
        "payload": item.payload,
        "requeue_attempts": item.requeue_attempts,
    }
    if item.poison_key is not WORK_ITEM_POISON_KEY_UNSET:
        payload["poison_key"] = item.poison_key
    return payload


def _work_item_from_dict(payload: SerializedWorkItem) -> WorkItem:
    return WorkItem(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        key=payload.get("key"),
        payload=payload.get("payload"),
        requeue_attempts=payload.get("requeue_attempts", 0),
        poison_key=payload.get("poison_key", WORK_ITEM_POISON_KEY_UNSET),
    )


def _completion_event_to_dict(
    event: CompletionEvent,
    extra_fields: Optional[dict[str, Any]] = None,
) -> SerializedCompletionEvent:
    payload: SerializedCompletionEvent = {
        "id": event.id,
        "topic": event.tp.topic,
        "partition": event.tp.partition,
        "offset": event.offset,
        "epoch": event.epoch,
        "status": event.status.value,
        "error": event.error,
        "attempt": event.attempt,
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def _completion_event_from_dict(
    payload: SerializedCompletionEvent,
) -> CompletionEvent:
    return CompletionEvent(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        status=CompletionStatus(payload["status"]),
        error=payload.get("error"),
        attempt=payload["attempt"],
    )


def _serialize_batch_payload(batch: list[WorkItem], flush_enqueued_at: float) -> bytes:
    envelope: SerializedBatchEnvelope = {
        "items": [_work_item_to_dict(item) for item in batch],
        "timing": {"flush_enqueued_at": flush_enqueued_at},
    }
    return msgpack.packb(envelope, use_bin_type=True)


def _normalize_decoded_payloads(
    decoded: Any,
) -> tuple[list[SerializedWorkItem], dict[str, float]]:
    if isinstance(decoded, dict):
        if "items" in decoded:
            timing = decoded.get("timing", {})
            timing_values = {
                key: float(value)
                for key, value in dict(timing).items()
                if isinstance(value, (int, float))
            }
            return [dict(entry) for entry in decoded.get("items", [])], timing_values
        payload = dict(decoded)
        payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
        return [payload], {}

    if isinstance(decoded, list):
        payloads: list[SerializedWorkItem] = []
        for entry in decoded:
            if isinstance(entry, WorkItem):
                payload = _work_item_to_dict(entry)
            else:
                payload = dict(entry)
            payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
            payloads.append(payload)
        return payloads, {}

    if isinstance(decoded, WorkItem):
        payload = _work_item_to_dict(decoded)
    else:
        payload = dict(decoded)
    payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
    return [payload], {}


def _decode_incoming_payloads(
    item: Any, max_bytes: int
) -> tuple[list[SerializedWorkItem], dict[str, float]]:
    if isinstance(item, (bytes, bytearray)):
        if len(item) > max_bytes:
            raise ValueError("payload_too_large")
        unpacker = msgpack.Unpacker(raw=False, max_buffer_size=max_bytes)
        unpacker.feed(item)
        decoded_items = list(unpacker)
        if len(decoded_items) == 1:
            decoded = decoded_items[0]
        else:
            decoded = decoded_items
        return _normalize_decoded_payloads(decoded)
    return _normalize_decoded_payloads(item)


def _decode_incoming_item(item: Any, max_bytes: int) -> list[WorkItem]:
    return [
        _work_item_from_dict(payload)
        for payload in _decode_incoming_payloads(item, max_bytes)[0]
    ]


class _BatchAccumulator:
    """Buffers WorkItems and flushes as batches to reduce IPC overhead.

    Flush triggers:
    - ``batch_size`` items accumulated (eager flush)
    - ``max_batch_wait_ms`` elapsed since first buffered item (timer flush)
    """

    def __init__(
        self,
        task_queue: Any,
        batch_size: int,
        max_batch_wait_ms: int,
        flush_policy: str = "size_or_timer",
        demand_flush_min_residence_ms: int = 0,
    ):
        self._task_queue = task_queue
        self._batch_size = batch_size
        self._max_batch_wait_sec = max_batch_wait_ms / 1000.0
        self._flush_policy = flush_policy
        self._demand_flush_min_residence_sec = demand_flush_min_residence_ms / 1000.0
        self._buffer: list[WorkItem] = []
        self._first_item_time: Optional[float] = None
        self._lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._closed = False
        self._size_flush_count = 0
        self._timer_flush_count = 0
        self._close_flush_count = 0
        self._demand_flush_count = 0
        self._total_flushed_items = 0
        self._last_flush_size = 0
        self._last_flush_wait_seconds = 0.0

    def add_nowait_fast_path(self, work_item: WorkItem) -> bool:
        """Flush single-item batches inline when the process queue has capacity."""
        if self._batch_size != 1:
            return False

        with self._lock:
            if self._closed or self._buffer:
                return False

            flush_enqueued_at = time.monotonic()
            packed = _serialize_batch_payload([work_item], flush_enqueued_at)
            put_nowait = getattr(self._task_queue, "put_nowait", None)
            try:
                if callable(put_nowait):
                    put_nowait(packed)
                else:
                    self._task_queue.put(packed, block=False)
            except queue.Full:
                return False

            self._size_flush_count += 1
            self._total_flushed_items += 1
            self._last_flush_size = 1
            self._last_flush_wait_seconds = 0.0
            if _logger.isEnabledFor(logging.DEBUG):
                _logger.debug("Batch flush (%s): size=%d", "size", 1)
            return True

    def add(self, work_item: WorkItem) -> None:
        with self._lock:
            if self._closed:
                return
            if self._should_flush_on_demand_locked():
                self._flush_locked(reason="demand")
            self._buffer.append(work_item)
            if self._first_item_time is None:
                self._first_item_time = time.monotonic()
                self._start_flush_timer()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked(reason="size")

    def _should_flush_on_demand_locked(self) -> bool:
        if not self._buffer:
            return False
        if self._flush_policy == "demand":
            return True
        if self._flush_policy != "demand_min_residence":
            return False
        if self._first_item_time is None:
            return False
        oldest_age = max(0.0, time.monotonic() - self._first_item_time)
        return oldest_age >= self._demand_flush_min_residence_sec

    def _start_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(self._max_batch_wait_sec, self._timer_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timer_flush(self) -> None:
        with self._lock:
            if self._buffer and not self._closed:
                self._flush_locked(reason="timer")

    def _flush_locked(self, *, reason: str = "manual") -> None:
        if not self._buffer:
            return
        wait_seconds = (
            max(0.0, time.monotonic() - self._first_item_time)
            if self._first_item_time is not None
            else 0.0
        )
        batch = list(self._buffer)
        self._buffer.clear()
        self._first_item_time = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        if reason == "size":
            self._size_flush_count += 1
        elif reason == "timer":
            self._timer_flush_count += 1
        elif reason == "close":
            self._close_flush_count += 1
        elif reason == "demand":
            self._demand_flush_count += 1
        self._total_flushed_items += len(batch)
        self._last_flush_size = len(batch)
        self._last_flush_wait_seconds = wait_seconds
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("Batch flush (%s): size=%d", reason, len(batch))
        flush_enqueued_at = time.monotonic()
        packed = _serialize_batch_payload(batch, flush_enqueued_at)
        self._task_queue.put(packed)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._buffer:
                self._flush_locked(reason="close")

    def snapshot(self) -> ProcessBatchMetrics:
        with self._lock:
            buffered_age_seconds = (
                max(0.0, time.monotonic() - self._first_item_time)
                if self._first_item_time is not None
                else 0.0
            )
            return ProcessBatchMetrics(
                size_flush_count=self._size_flush_count,
                timer_flush_count=self._timer_flush_count,
                close_flush_count=self._close_flush_count,
                total_flushed_items=self._total_flushed_items,
                last_flush_size=self._last_flush_size,
                last_flush_wait_seconds=self._last_flush_wait_seconds,
                buffered_items=len(self._buffer),
                buffered_age_seconds=buffered_age_seconds,
                demand_flush_count=self._demand_flush_count,
            )


class _NoOpBatchAccumulator:
    """No-op accumulator used when transport bypasses shared-queue batching."""

    def add_nowait_fast_path(self, work_item: WorkItem) -> bool:
        del work_item
        return False

    def add(self, work_item: WorkItem) -> None:
        del work_item
        return None

    def close(self) -> None:
        return None

    def snapshot(self) -> ProcessBatchMetrics:
        return ProcessBatchMetrics(
            size_flush_count=0,
            timer_flush_count=0,
            close_flush_count=0,
            total_flushed_items=0,
            last_flush_size=0,
            last_flush_wait_seconds=0.0,
            buffered_items=0,
            buffered_age_seconds=0.0,
            demand_flush_count=0,
        )


def _receive_task_payload(task_source: Any) -> Any:
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
    """Calculate backoff delay in seconds with optional exponential scaling and jitter."""

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
                        worker_fn(work_item)

                    if timeout_sec > 0:

                        def _handle_timeout(signum, frame):
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
                    registry_event_queue.put({"kind": "done", "key": in_flight_key})

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


class ProcessExecutionEngine(BaseExecutionEngine):
    """
    프로세스 기반 실행 엔진의 구현입니다.

    Args:
        config (ExecutionConfig): 실행 엔진 설정.
        worker_fn (Callable[[WorkItem], Any]): 사용자 정의 워커 함수.
    """

    def __init__(self, config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]):
        if inspect.iscoroutinefunction(worker_fn) or inspect.iscoroutinefunction(
            getattr(worker_fn, "__call__", None)
        ):
            raise TypeError(
                "Process execution mode requires a synchronous picklable worker"
            )
        if getattr(config.process_config, "require_picklable_worker", False):
            try:
                pickle.dumps(worker_fn)
            except Exception as exc:
                raise TypeError(
                    "Process execution mode requires a synchronous picklable worker"
                ) from exc

        self._config = config
        self._worker_fn = worker_fn
        self._transport_mode = config.process_config.transport_mode
        self._validate_transport_config()
        self._task_queue: Optional[Queue[Optional[WorkItem]]] = None
        self._batch_accumulator: _BatchAccumulator | _NoOpBatchAccumulator
        if self._transport_mode == "shared_queue":
            self._task_queue = Queue(maxsize=config.process_config.queue_size)
        self._completion_queue: Queue[Any] = Queue()
        self._registry_event_queue: Queue[Any] = Queue()
        self._prefetched_completion_events: Deque[CompletionEvent] = deque()
        self._in_flight_registry: dict[
            tuple[int, str, int, int], SerializedWorkItem
        ] = {}
        self._workers: List[Process] = []
        self._worker_pid_by_index: dict[int, Optional[int]] = {}
        self._in_flight_count: int = 0
        self._in_flight_lock = threading.Lock()

        self._logger = logging.getLogger(__name__)
        self._is_shutdown: bool = False
        self._initialize_runtime_timing_state()
        self._last_worker_liveness_check = 0.0
        self._worker_liveness_check_interval_seconds = 0.05
        self._worker_pipe_senders: list[Any] = []
        self._pending_pipe_dispatch: dict[
            tuple[int, str, int, int], SerializedWorkItem
        ] = {}
        self._worker_pipe_queue_slots: threading.BoundedSemaphore | None = None

        self._log_queue: Queue[logging.LogRecord] = Queue(
            maxsize=config.process_config.queue_size
        )
        main_handlers = tuple(logging.getLogger().handlers)
        self._log_listener = LogManager.create_queue_listener(
            self._log_queue, main_handlers
        )
        self._log_listener.start()

        if self._transport_mode == "shared_queue":
            if self._task_queue is None:
                raise RuntimeError("shared_queue transport requires a task queue")
            self._batch_accumulator = _BatchAccumulator(
                task_queue=self._task_queue,
                batch_size=config.process_config.batch_size,
                max_batch_wait_ms=config.process_config.max_batch_wait_ms,
                flush_policy=config.process_config.flush_policy,
                demand_flush_min_residence_ms=(
                    config.process_config.demand_flush_min_residence_ms
                ),
            )
            if self._task_queue is None:
                raise RuntimeError("shared_queue transport requires a task queue")
            self._transport: ProcessTransport = SharedQueueProcessTransport(
                task_queue=self._task_queue,
                batch_accumulator=self._batch_accumulator,
                work_item_from_dict=_work_item_from_dict,
                increment_in_flight=self._increment_in_flight_count,
                sentinel=_SENTINEL,
            )
        else:
            self._batch_accumulator = _NoOpBatchAccumulator()
            worker_pipe_transport = WorkerPipesProcessTransport(
                process_count=config.process_config.process_count,
                queue_size=config.process_config.queue_size,
                serialize_work_item=_work_item_to_dict,
                serialize_batch_payload=_serialize_batch_payload,
                work_item_from_dict=_work_item_from_dict,
                get_worker_pipe_senders=lambda: self._worker_pipe_senders,
                get_pending_pipe_dispatch=lambda: self._pending_pipe_dispatch,
                release_worker_pipe_queue_slot=self._release_worker_pipe_queue_slot,
                increment_in_flight=self._increment_in_flight_count,
                pipe_sentinel=_PIPE_SENTINEL,
            )
            self._transport = worker_pipe_transport
            self._worker_pipe_queue_slots = (
                worker_pipe_transport._worker_pipe_queue_slots
            )

        self._start_workers()

    def _validate_transport_config(self) -> None:
        process_config = self._config.process_config
        if self._transport_mode != "worker_pipes":
            return
        if process_config.batch_size != 1:
            raise ValueError(
                "worker_pipes transport only supports batch_size=1 in the first slice"
            )
        if process_config.max_batch_wait_ms != 0:
            raise ValueError(
                "worker_pipes transport rejects timer batching; set max_batch_wait_ms=0"
            )
        if process_config.flush_policy != "size_or_timer":
            raise ValueError(
                "worker_pipes transport rejects flush_policy=%s in the first slice"
                % process_config.flush_policy
            )
        if process_config.demand_flush_min_residence_ms != 0:
            raise ValueError(
                "worker_pipes transport rejects demand_flush_min_residence_ms>0"
            )
        if process_config.max_tasks_per_child != 0:
            raise ValueError(
                "worker_pipes transport does not support max_tasks_per_child yet"
            )
        if process_config.recycle_jitter_ms != 0:
            raise ValueError(
                "worker_pipes transport does not support recycle_jitter_ms yet"
            )

    def _start_worker(self, idx: int) -> Process:
        (
            task_source,
            close_parent_after_start,
        ) = self._transport.start_worker_task_source(idx)
        worker = Process(
            target=_worker_loop,
            args=(
                task_source,
                self._completion_queue,
                self._registry_event_queue,
                self._worker_fn,
                idx,
                self._config,
                self._log_queue,
            ),
        )
        worker.start()
        if close_parent_after_start:
            close = getattr(task_source, "close", None)
            if callable(close):
                close()
        self._worker_pid_by_index[idx] = worker.pid
        self._logger.debug("Started ProcessWorker[%d] (PID: %d)", idx, worker.pid)
        return worker

    def _start_workers(self):
        """
        워커 프로세스 풀을 시작합니다.
        """
        for i in range(self._config.process_config.process_count):
            worker = self._start_worker(i)
            self._workers.append(worker)

    def _join_worker_with_escalation(self, worker: Process) -> None:
        timeout_sec = self._config.process_config.worker_join_timeout_ms / 1000.0
        worker.join(timeout=timeout_sec)
        if not worker.is_alive():
            return

        self._logger.warning(
            "ProcessWorker[%s] did not shut down gracefully after %.3fs. Terminating.",
            worker.pid,
            timeout_sec,
        )
        worker.terminate()
        worker.join(timeout=timeout_sec)
        if not worker.is_alive():
            return

        kill = getattr(worker, "kill", None)
        if callable(kill):
            self._logger.warning(
                "ProcessWorker[%s] still alive after terminate(). Killing.",
                worker.pid,
            )
            kill()
            worker.join(timeout=timeout_sec)

    def _emit_completion_event(self, completion_event: CompletionEvent) -> None:
        packed = msgpack.packb(
            _completion_event_to_dict(completion_event),
            use_bin_type=True,
        )
        self._completion_queue.put(packed)  # type: ignore[arg-type]

    def _emit_worker_recovery_failure(
        self,
        idx: int,
        payload: SerializedWorkItem,
        *,
        error: str,
        attempt: int,
        timeout_failure: bool = False,
    ) -> None:
        try:
            completion_event = CompletionEvent(
                id=payload.get("id", ""),
                tp=TopicPartition(
                    payload.get("topic", ""),
                    payload.get("partition", 0),
                ),
                offset=payload.get("offset", -1),
                epoch=payload.get("epoch", 0),
                status=CompletionStatus.FAILURE,
                error=error,
                attempt=attempt,
            )
            self._emit_completion_event(completion_event)
        except Exception as push_exc:
            if timeout_failure:
                self._logger.error(
                    "Failed to emit timeout failure for worker %d item offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    push_exc,
                )
            else:
                self._logger.error(
                    "Failed to emit failure for worker %d item offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    push_exc,
                )

    def _recover_dead_worker_items(self, idx: int) -> list[SerializedWorkItem]:
        return ProcessRegistrySupport.recover_dead_worker_items(
            worker_index=idx,
            in_flight_registry=self._in_flight_registry,
            max_retries=self._config.max_retries,
            emit_worker_recovery_failure=self._emit_worker_recovery_failure,
        )

    def _drain_registry_event_queue(self) -> int:
        return ProcessRegistrySupport.drain_registry_event_queue(
            registry_event_queue=getattr(self, "_registry_event_queue", None),
            apply_event=self._apply_registry_event,
        )

    def _ensure_workers_alive(self) -> None:
        self._drain_registry_events()
        liveness_interval = getattr(
            self,
            "_worker_liveness_check_interval_seconds",
            0.0,
        )
        if liveness_interval > 0:
            now = time.monotonic()
            last_check = getattr(self, "_last_worker_liveness_check", 0.0)
            if now - last_check < liveness_interval:
                return
            self._last_worker_liveness_check = now

        for idx, worker in enumerate(self._workers):
            if worker.is_alive():
                continue
            exitcode = worker.exitcode
            try:
                to_requeue = self._recover_dead_worker_items(idx)
                to_requeue.extend(self._recover_pending_pipe_dispatches(idx))
                if to_requeue:
                    self._requeue_recovered_payloads(to_requeue)
                    offsets = [entry.get("offset") for entry in to_requeue]
                    self._logger.warning(
                        "Requeued %d lost work item(s) offsets=%s from dead worker %d",
                        len(to_requeue),
                        offsets,
                        idx,
                    )
            except Exception as requeue_exc:
                self._logger.error(
                    "Failed to requeue work from worker %d: %s", idx, requeue_exc
                )
            self._logger.error(
                "ProcessWorker[%d] died (exitcode=%s). Restarting worker.",
                idx,
                exitcode,
            )
            new_worker = self._start_worker(idx)
            self._workers[idx] = new_worker

    def _recover_pending_pipe_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        transport = getattr(self, "_transport", None)
        if transport is not None:
            return transport.recover_pending_dispatches(idx)
        if self._get_transport_mode() != "worker_pipes":
            return []
        pending_dispatch = getattr(self, "_pending_pipe_dispatch", {})
        to_requeue: list[SerializedWorkItem] = []
        for key, payload in list(pending_dispatch.items()):
            if key[0] != idx:
                continue
            recovered_payload = dict(payload)
            recovered_payload["requeue_attempts"] = (
                recovered_payload.get("requeue_attempts", 0) + 1
            )
            to_requeue.append(recovered_payload)
            pending_dispatch.pop(key, None)
            self._release_worker_pipe_queue_slot()
        return to_requeue

    def _requeue_recovered_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        if not payloads:
            return
        self._transport.requeue_payloads(payloads)

    def _apply_registry_event(self, event: dict[str, Any]) -> None:
        self._initialize_runtime_timing_state()
        transport = getattr(self, "_transport", None)
        if transport is not None:
            transport.handle_registry_event(event)
        elif (
            event.get("kind") == "start"
            and self._get_transport_mode() == "worker_pipes"
        ):
            key = event.get("key")
            pending_dispatch = getattr(self, "_pending_pipe_dispatch", {})
            if key in pending_dispatch:
                pending_dispatch.pop(key, None)
                self._release_worker_pipe_queue_slot()
        ProcessRegistrySupport.apply_registry_event(
            event=event,
            in_flight_registry=self._in_flight_registry,
            record_main_to_worker_ipc=self._record_main_to_worker_ipc,
            record_worker_exec=self._record_worker_exec,
        )

    def _drain_registry_events(self) -> None:
        self._drain_registry_event_queue()

    def _drain_shutdown_ipc_once(self) -> tuple[int, int]:
        drained_registry = self._drain_registry_event_queue()
        drained_completion = 0

        while True:
            try:
                raw_event = self._completion_queue.get_nowait()
            except queue.Empty:
                break
            drained_completion += 1
            self._prefetched_completion_events.append(
                self._decode_completion_queue_item(raw_event)
            )

        return drained_registry, drained_completion

    def get_min_inflight_offset(self, tp: TopicPartition) -> Optional[int]:
        self._drain_registry_events()
        return ProcessRegistrySupport.get_min_inflight_offset(
            in_flight_registry=self._in_flight_registry,
            tp=tp,
        )

    def get_runtime_metrics(self) -> Optional[ProcessBatchMetrics]:
        self._drain_registry_events()
        base_metrics = self._batch_accumulator.snapshot()
        self._initialize_runtime_timing_state()
        with self._runtime_timing_lock:
            main_to_worker_avg = (
                self._main_to_worker_ipc_sum_seconds / self._main_to_worker_ipc_samples
                if self._main_to_worker_ipc_samples > 0
                else 0.0
            )
            worker_exec_avg = (
                self._worker_exec_sum_seconds / self._worker_exec_samples
                if self._worker_exec_samples > 0
                else 0.0
            )
            worker_to_main_avg = (
                self._worker_to_main_ipc_sum_seconds / self._worker_to_main_ipc_samples
                if self._worker_to_main_ipc_samples > 0
                else 0.0
            )
            return ProcessBatchMetrics(
                size_flush_count=base_metrics.size_flush_count,
                timer_flush_count=base_metrics.timer_flush_count,
                close_flush_count=base_metrics.close_flush_count,
                total_flushed_items=base_metrics.total_flushed_items,
                last_flush_size=base_metrics.last_flush_size,
                last_flush_wait_seconds=base_metrics.last_flush_wait_seconds,
                buffered_items=base_metrics.buffered_items,
                buffered_age_seconds=base_metrics.buffered_age_seconds,
                demand_flush_count=base_metrics.demand_flush_count,
                last_main_to_worker_ipc_seconds=self._last_main_to_worker_ipc_seconds,
                avg_main_to_worker_ipc_seconds=main_to_worker_avg,
                last_worker_exec_seconds=self._last_worker_exec_seconds,
                avg_worker_exec_seconds=worker_exec_avg,
                last_worker_to_main_ipc_seconds=self._last_worker_to_main_ipc_seconds,
                avg_worker_to_main_ipc_seconds=worker_to_main_avg,
            )

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 태스크 큐에 넣습니다.
        """
        self._drain_registry_events()
        await self._transport.submit_work_item(
            work_item,
            route_identity=resolve_route_identity(work_item),
            count_in_flight=True,
        )

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """
        완료 큐에서 완료 이벤트를 가져와 리스트로 반환합니다.
        """
        self._ensure_workers_alive()
        self._drain_registry_events()
        completed_events: List[CompletionEvent] = []
        while (
            len(completed_events) < batch_limit and self._prefetched_completion_events
        ):
            completed_events.append(self._prefetched_completion_events.popleft())
            with self._in_flight_lock:
                self._in_flight_count -= 1
        while len(completed_events) < batch_limit:
            try:
                raw_event = self._completion_queue.get_nowait()
                event = self._decode_completion_queue_item(raw_event)
                completed_events.append(event)
                with self._in_flight_lock:
                    self._in_flight_count -= 1
            except queue.Empty:
                break
            except Exception as e:
                _logger.error(
                    "Error getting item from completion queue: %r", e, exc_info=True
                )
                break
        return completed_events

    async def wait_for_completion(
        self, timeout_seconds: Optional[float] = None
    ) -> bool:
        self._ensure_workers_alive()
        self._drain_registry_events()

        if self._prefetched_completion_events or not self._completion_queue.empty():
            return True

        if timeout_seconds is not None and timeout_seconds <= 0:
            return False

        try:
            raw_event = await asyncio.to_thread(
                self._completion_queue.get,
                True,
                timeout_seconds,
            )
        except queue.Empty:
            return False

        self._prefetched_completion_events.append(
            self._decode_completion_queue_item(raw_event)
        )
        return True

    def _initialize_runtime_timing_state(self) -> None:
        if hasattr(self, "_runtime_timing_lock"):
            return
        self._runtime_timing_lock = threading.Lock()
        self._main_to_worker_ipc_samples = 0
        self._main_to_worker_ipc_sum_seconds = 0.0
        self._last_main_to_worker_ipc_seconds = 0.0
        self._worker_exec_samples = 0
        self._worker_exec_sum_seconds = 0.0
        self._last_worker_exec_seconds = 0.0
        self._worker_to_main_ipc_samples = 0
        self._worker_to_main_ipc_sum_seconds = 0.0
        self._last_worker_to_main_ipc_seconds = 0.0

    def _record_main_to_worker_ipc(self, duration_seconds: Any) -> None:
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_main_to_worker_ipc_samples",
            sum_attr="_main_to_worker_ipc_sum_seconds",
            last_attr="_last_main_to_worker_ipc_seconds",
        )

    def _record_worker_exec(self, duration_seconds: Any) -> None:
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_worker_exec_samples",
            sum_attr="_worker_exec_sum_seconds",
            last_attr="_last_worker_exec_seconds",
        )

    def _record_worker_to_main_ipc(self, duration_seconds: Any) -> None:
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_worker_to_main_ipc_samples",
            sum_attr="_worker_to_main_ipc_sum_seconds",
            last_attr="_last_worker_to_main_ipc_seconds",
        )

    def _record_runtime_timing(
        self,
        duration_seconds: Any,
        *,
        sample_attr: str,
        sum_attr: str,
        last_attr: str,
    ) -> None:
        if not isinstance(duration_seconds, (int, float)):
            return
        self._initialize_runtime_timing_state()
        duration = max(0.0, float(duration_seconds))
        with self._runtime_timing_lock:
            setattr(self, sample_attr, getattr(self, sample_attr) + 1)
            setattr(self, sum_attr, getattr(self, sum_attr) + duration)
            setattr(self, last_attr, duration)

    def _decode_completion_queue_item(self, raw_event: Any) -> CompletionEvent:
        if isinstance(raw_event, (bytes, bytearray)):
            payload = msgpack.unpackb(raw_event, raw=False)
            completion_enqueued_at = payload.get("completion_enqueued_at")
            if isinstance(completion_enqueued_at, (int, float)):
                self._record_worker_to_main_ipc(
                    time.monotonic() - float(completion_enqueued_at)
                )
            return _completion_event_from_dict(payload)
        return raw_event

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.
        """
        with self._in_flight_lock:
            return self._in_flight_count

    def _dispatch_payload_to_transport(
        self,
        payload: SerializedWorkItem,
        count_in_flight: bool = False,
    ) -> None:
        work_item = _work_item_from_dict(payload)
        self._transport.dispatch_payload(
            payload,
            route_identity=resolve_route_identity(work_item),
            count_in_flight=count_in_flight,
        )

    def _release_worker_pipe_queue_slot(self) -> None:
        worker_pipe_queue_slots = getattr(self, "_worker_pipe_queue_slots", None)
        if worker_pipe_queue_slots is None:
            return
        try:
            worker_pipe_queue_slots.release()
        except ValueError:
            return

    def _get_transport_mode(self) -> str:
        return getattr(self, "_transport_mode", "shared_queue")

    async def shutdown(self) -> None:
        """
        실행 엔진을 정상적으로 종료합니다. 모든 워커 프로세스에 종료 시그널을 보내고 대기합니다.
        이 메서드는 멱등(idempotent)하며, 여러 번 호출해도 안전합니다.
        """
        if self._is_shutdown:
            _logger.debug(
                "ProcessExecutionEngine.shutdown() called but already shut down. Skipping."
            )
            return
        self._is_shutdown = True

        self._drain_registry_events()
        prefetched_count = len(self._prefetched_completion_events)
        in_flight_registry_size = len(self._in_flight_registry)
        worker_count = len(self._workers)
        _logger.debug(
            "Initiating ProcessExecutionEngine shutdown. prefetched_completion_events=%d in_flight_registry=%d worker_count=%d",
            prefetched_count,
            in_flight_registry_size,
            worker_count,
        )
        self._batch_accumulator.close()
        self._transport.signal_shutdown(len(self._workers))

        shutdown_drain_deadline = time.monotonic() + 1.0
        total_registry_drained = 0
        total_completion_drained = 0
        while time.monotonic() < shutdown_drain_deadline:
            drained_registry, drained_completion = self._drain_shutdown_ipc_once()
            total_registry_drained += drained_registry
            total_completion_drained += drained_completion
            if (
                drained_registry == 0
                and drained_completion == 0
                and not self._in_flight_registry
            ):
                break
            await asyncio.sleep(0.01)
        _logger.debug(
            "ProcessExecutionEngine shutdown pre-join drain: registry_events=%d completion_events=%d residual_in_flight_registry=%d",
            total_registry_drained,
            total_completion_drained,
            len(self._in_flight_registry),
        )
        if self._in_flight_registry:
            registry_summary = []
            for (worker_idx, topic, partition, offset), payload in sorted(
                self._in_flight_registry.items(),
                key=lambda item: item[0],
            ):
                registry_summary.append(
                    "%d(pid=%s):%s-%d@%d timed_out=%s attempts=%s"
                    % (
                        worker_idx,
                        self._worker_pid_by_index.get(worker_idx),
                        topic,
                        partition,
                        offset,
                        payload.get("timed_out", False),
                        payload.get("requeue_attempts", 0),
                    )
                )
            _logger.warning(
                "Residual in-flight registry after shutdown drain: %s",
                "; ".join(registry_summary),
            )

        # Wait for all workers to finish
        for worker in self._workers:
            self._join_worker_with_escalation(worker)

        self._prefetched_completion_events.clear()
        self._in_flight_registry.clear()
        pending_pipe_dispatch = getattr(self, "_pending_pipe_dispatch", None)
        if pending_pipe_dispatch is not None:
            pending_pipe_dispatch.clear()
        with self._in_flight_lock:
            self._in_flight_count = 0

        _logger.debug("ProcessExecutionEngine shutdown complete.")
        self._log_listener.stop()
        for queue_obj in (
            self._task_queue,
            self._completion_queue,
            self._registry_event_queue,
        ):
            if queue_obj is None:
                continue
            close = getattr(queue_obj, "close", None)
            if callable(close):
                close()
        self._transport.close()

    def _increment_in_flight_count(self) -> None:
        with self._in_flight_lock:
            self._in_flight_count += 1
