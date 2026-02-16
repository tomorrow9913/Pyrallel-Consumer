import asyncio
import logging
import logging.handlers
import threading
import time
from collections.abc import Callable
from multiprocessing import Process, Queue
from typing import Any, List, Optional

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.logger import LogManager

_SENTINEL = None

_logger = logging.getLogger(__name__)


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
                self._flush_locked()

    def _start_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(self._max_batch_wait_sec, self._timer_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timer_flush(self) -> None:
        with self._lock:
            if self._buffer and not self._closed:
                self._flush_locked()

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        self._first_item_time = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        self._task_queue.put(batch)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._buffer:
                self._flush_locked()


def _worker_loop(
    task_queue: Queue,
    completion_queue: Queue,
    worker_fn: Callable[[WorkItem], Any],
    process_idx: int,
    log_queue: Optional[Queue] = None,
):
    if log_queue is not None:
        LogManager.setup_worker_logging(log_queue)

    worker_logger = logging.getLogger(__name__)
    worker_logger.info("ProcessWorker[%d] started.", process_idx)

    while True:
        item = task_queue.get()
        if item is _SENTINEL:
            worker_logger.info(
                "ProcessWorker[%d] received sentinel, shutting down.", process_idx
            )
            break

        work_items = item if isinstance(item, list) else [item]

        for work_item in work_items:
            status = CompletionStatus.SUCCESS
            error: Optional[str] = None
            try:
                worker_fn(work_item)
            except Exception as e:
                status = CompletionStatus.FAILURE
                error = str(e)
                worker_logger.exception(
                    "Task for offset %d failed in ProcessWorker[%d].",
                    work_item.offset,
                    process_idx,
                )
            finally:
                completion_event = CompletionEvent(
                    id=work_item.id,
                    tp=work_item.tp,
                    offset=work_item.offset,
                    epoch=work_item.epoch,
                    status=status,
                    error=error,
                )
                completion_queue.put(completion_event)

    worker_logger.info("ProcessWorker[%d] shutdown complete.", process_idx)


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
        self._completion_queue: Queue[CompletionEvent] = Queue()
        self._workers: List[Process] = []
        self._in_flight_count: int = 0

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

    def _start_workers(self):
        """
        워커 프로세스 풀을 시작합니다.
        """
        for i in range(self._config.process_config.process_count):
            worker = Process(
                target=_worker_loop,
                args=(
                    self._task_queue,
                    self._completion_queue,
                    self._worker_fn,
                    i,
                    self._log_queue,
                ),
            )
            self._workers.append(worker)
            worker.start()
            self._logger.info("Started ProcessWorker[%d] (PID: %d)", i, worker.pid)

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 태스크 큐에 넣습니다.
        """
        await asyncio.to_thread(self._batch_accumulator.add, work_item)
        self._in_flight_count += 1

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """
        완료 큐에서 완료 이벤트를 가져와 리스트로 반환합니다.
        """
        completed_events: List[CompletionEvent] = []
        while (
            len(completed_events) < batch_limit and not self._completion_queue.empty()
        ):
            try:
                event = self._completion_queue.get_nowait()
                completed_events.append(event)
                self._in_flight_count -= 1
            except Exception as e:
                _logger.error("Error getting item from completion queue: %s", e)
                break
        return completed_events

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.
        """
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

        _logger.info("Initiating ProcessExecutionEngine shutdown.")
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

        _logger.info("ProcessExecutionEngine shutdown complete.")
        self._log_listener.stop()
