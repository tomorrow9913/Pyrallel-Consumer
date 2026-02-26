import asyncio
import logging
import logging.handlers
import os
import random
import signal
import threading
import time
from collections.abc import Callable
from multiprocessing import Manager, Process, Queue
from typing import Any, List, Optional

import msgpack

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.logger import LogManager

_SENTINEL = None

_logger = logging.getLogger(__name__)


def _work_item_to_dict(item: WorkItem) -> dict:
    return {
        "id": item.id,
        "topic": item.tp.topic,
        "partition": item.tp.partition,
        "offset": item.offset,
        "epoch": item.epoch,
        "key": item.key,
        "payload": item.payload,
        "requeue_attempts": 0,
    }


def _work_item_from_dict(payload: dict) -> WorkItem:
    return WorkItem(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        key=payload.get("key"),
        payload=payload.get("payload"),
    )


def _completion_event_to_dict(event: CompletionEvent) -> dict:
    return {
        "id": event.id,
        "topic": event.tp.topic,
        "partition": event.tp.partition,
        "offset": event.offset,
        "epoch": event.epoch,
        "status": event.status.value,
        "error": event.error,
        "attempt": event.attempt,
    }


def _completion_event_from_dict(payload: dict) -> CompletionEvent:
    return CompletionEvent(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        status=CompletionStatus(payload["status"]),
        error=payload.get("error"),
        attempt=payload["attempt"],
    )


def _decode_incoming_item(item: Any, max_bytes: int) -> list[WorkItem]:
    if isinstance(item, (bytes, bytearray)):
        if len(item) > max_bytes:
            raise ValueError("payload_too_large")
        unpacker = msgpack.Unpacker(raw=False, max_buffer_size=max_bytes)
        unpacker.feed(item)
        decoded = list(unpacker)
        if len(decoded) == 1 and isinstance(decoded[0], list):
            decoded = decoded[0]
        return [_work_item_from_dict(entry) for entry in decoded]
    if isinstance(item, list):
        return item
    return [item]


class _BatchAccumulator:
    """Buffers WorkItems and flushes as batches to reduce IPC overhead.

    Flush triggers:
    - ``batch_size`` items accumulated (eager flush)
    - ``max_batch_wait_ms`` elapsed since first buffered item (timer flush)
    """

    def __init__(
        self,
        task_queue: Queue,
        batch_size: int,
        max_batch_wait_ms: int,
    ):
        self._task_queue = task_queue
        self._batch_size = batch_size
        self._max_batch_wait_sec = max_batch_wait_ms / 1000.0
        self._buffer: list[WorkItem] = []
        self._first_item_time: Optional[float] = None
        self._lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._closed = False

    def add(self, work_item: WorkItem) -> None:
        with self._lock:
            if self._closed:
                return
            self._buffer.append(work_item)
            if self._first_item_time is None:
                self._first_item_time = time.monotonic()
                self._start_flush_timer()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked(reason="size")

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
        batch = list(self._buffer)
        self._buffer.clear()
        self._first_item_time = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("Batch flush (%s): size=%d", reason, len(batch))
        payload = [_work_item_to_dict(item) for item in batch]
        packed = msgpack.packb(payload, use_bin_type=True)
        self._task_queue.put(packed)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._buffer:
                self._flush_locked(reason="close")


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
    task_queue: Queue,
    completion_queue: Queue,
    worker_fn: Callable[[WorkItem], Any],
    process_idx: int,
    execution_config: ExecutionConfig,
    log_queue: Optional[Queue] = None,
    in_flight_registry=None,
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
        item = task_queue.get()
        if item is _SENTINEL:
            worker_logger.debug(
                "ProcessWorker[%d] received sentinel, shutting down.", process_idx
            )
            break

        try:
            work_items = _decode_incoming_item(
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

        for idx, work_item in enumerate(work_items):
            if in_flight_registry is not None:
                key = (
                    process_idx,
                    work_item.tp.topic,
                    work_item.tp.partition,
                    work_item.offset,
                )
                payload = _work_item_to_dict(work_item)
                payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
                in_flight_registry[key] = payload
            status = CompletionStatus.FAILURE
            error: Optional[str] = None
            attempt = 0
            fatal_timeout = False

            timeout_ms = getattr(execution_config.process_config, "task_timeout_ms", 0)
            timeout_sec = timeout_ms / 1000.0

            for attempt in range(1, execution_config.max_retries + 1):
                try:

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

            completion_event = CompletionEvent(
                id=work_item.id,
                tp=work_item.tp,
                offset=work_item.offset,
                epoch=work_item.epoch,
                status=status,
                error=error,
                attempt=attempt,
            )
            try:
                completion_queue.put(
                    msgpack.packb(
                        _completion_event_to_dict(completion_event), use_bin_type=True
                    )
                )
            except Exception as put_exc:
                worker_logger.error(
                    "Failed to enqueue completion for offset=%d in ProcessWorker[%d]: %s",
                    work_item.offset,
                    process_idx,
                    put_exc,
                )
            finally:
                if in_flight_registry is not None and not fatal_timeout:
                    key = (
                        process_idx,
                        work_item.tp.topic,
                        work_item.tp.partition,
                        work_item.offset,
                    )
                    in_flight_registry.pop(key, None)

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
                    remaining = work_items[idx + 1 :]
                    if remaining:
                        remaining_payloads = [
                            _work_item_to_dict(item) for item in remaining
                        ]
                        packed_remaining = msgpack.packb(
                            remaining_payloads, use_bin_type=True
                        )
                        task_queue.put(packed_remaining)
                    should_exit_after_batch = True

            if fatal_timeout:
                worker_logger.error(
                    "ProcessWorker[%d] exiting due to task timeout; parent will respawn",
                    process_idx,
                )
                os._exit(1)

            if should_exit_after_batch:
                break

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
        self._config = config
        self._worker_fn = worker_fn
        self._task_queue: Queue[Optional[WorkItem]] = Queue(
            maxsize=config.process_config.queue_size
        )
        self._completion_queue: Queue[Any] = Queue()
        self._manager = Manager()
        self._in_flight_registry = self._manager.dict()
        self._workers: List[Process] = []
        self._in_flight_count: int = 0
        self._in_flight_lock = threading.Lock()

        self._logger = logging.getLogger(__name__)
        self._is_shutdown: bool = False

        self._log_queue: Queue[logging.LogRecord] = Queue()
        main_handlers = tuple(logging.getLogger().handlers)
        self._log_listener = LogManager.create_queue_listener(
            self._log_queue, main_handlers
        )
        self._log_listener.start()

        self._batch_accumulator = _BatchAccumulator(
            task_queue=self._task_queue,
            batch_size=config.process_config.batch_size,
            max_batch_wait_ms=config.process_config.max_batch_wait_ms,
        )

        self._start_workers()

    def _start_worker(self, idx: int) -> Process:
        worker = Process(
            target=_worker_loop,
            args=(
                self._task_queue,
                self._completion_queue,
                self._worker_fn,
                idx,
                self._config,
                self._log_queue,
                self._in_flight_registry,
            ),
        )
        worker.start()
        self._logger.debug("Started ProcessWorker[%d] (PID: %d)", idx, worker.pid)
        return worker

    def _start_workers(self):
        """
        워커 프로세스 풀을 시작합니다.
        """
        for i in range(self._config.process_config.process_count):
            worker = self._start_worker(i)
            self._workers.append(worker)

    def _ensure_workers_alive(self) -> None:
        for idx, worker in enumerate(self._workers):
            if worker.is_alive():
                continue
            exitcode = worker.exitcode
            try:
                items = [
                    (key, payload)
                    for key, payload in self._in_flight_registry.items()
                    if key[0] == idx
                ]
                to_requeue = []
                for key, payload in items:
                    attempts = payload.get("requeue_attempts", 0)
                    if attempts >= self._config.max_retries:
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
                                error="worker_died_max_retries",
                                attempt=attempts,
                            )
                            packed = msgpack.packb(
                                _completion_event_to_dict(completion_event),
                                use_bin_type=True,
                            )
                            self._completion_queue.put(packed)  # type: ignore[arg-type]
                        except Exception as push_exc:
                            self._logger.error(
                                "Failed to emit failure for worker %d item offset=%s: %s",
                                idx,
                                payload.get("offset"),
                                push_exc,
                            )
                    else:
                        payload["requeue_attempts"] = attempts + 1
                        to_requeue.append(payload)
                    self._in_flight_registry.pop(key, None)

                if to_requeue:
                    packed = msgpack.packb(to_requeue, use_bin_type=True)
                    self._task_queue.put(packed)
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

    def get_min_inflight_offset(self, tp: TopicPartition) -> Optional[int]:
        min_offset = None
        for (_w, topic, partition, _o), payload in self._in_flight_registry.items():
            if topic == tp.topic and partition == tp.partition:
                off = payload.get("offset")
                if isinstance(off, int):
                    if min_offset is None or off < min_offset:
                        min_offset = off
        return min_offset

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 태스크 큐에 넣습니다.
        """
        await asyncio.to_thread(self._batch_accumulator.add, work_item)
        with self._in_flight_lock:
            self._in_flight_count += 1

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """
        완료 큐에서 완료 이벤트를 가져와 리스트로 반환합니다.
        """
        self._ensure_workers_alive()
        completed_events: List[CompletionEvent] = []
        while (
            len(completed_events) < batch_limit and not self._completion_queue.empty()
        ):
            try:
                raw_event = self._completion_queue.get_nowait()
                if isinstance(raw_event, (bytes, bytearray)):
                    event = _completion_event_from_dict(
                        msgpack.unpackb(raw_event, raw=False)
                    )
                else:
                    event = raw_event

                completed_events.append(event)
                with self._in_flight_lock:
                    self._in_flight_count -= 1
            except Exception as e:
                _logger.error("Error getting item from completion queue: %s", e)
                break
        return completed_events

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.
        """
        with self._in_flight_lock:
            return self._in_flight_count

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

        _logger.debug("Initiating ProcessExecutionEngine shutdown.")
        self._batch_accumulator.close()
        # Send sentinel to all workers to signal shutdown
        for _ in self._workers:
            self._task_queue.put(_SENTINEL)

        # Wait for all workers to finish
        for worker in self._workers:
            worker.join(
                timeout=self._config.process_config.worker_join_timeout_ms / 1000.0
            )
            if worker.is_alive():
                _logger.warning(
                    "ProcessWorker[%d] did not shut down gracefully. Terminating.",
                    worker.pid,
                )
                worker.terminate()

        _logger.debug("ProcessExecutionEngine shutdown complete.")
        self._log_listener.stop()
