import asyncio
import logging
from collections.abc import Callable
from multiprocessing import Process, Queue, Value
from typing import Any, List, Optional

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

# Sentinel for graceful shutdown of worker processes
_SENTINEL = None

_logger = logging.getLogger(__name__)


def _worker_loop(
    task_queue: Queue,
    completion_queue: Queue,
    worker_fn: Callable[[WorkItem], Any],
    process_idx: int,
):
    """
    Worker process loop to fetch tasks, execute the worker function, and put results.
    """
    _logger.info("ProcessWorker[%d] started.", process_idx)
    # Re-initialize logger for the new process to avoid issues with inherited loggers
    worker_logger = logging.getLogger(__name__)

    while True:
        work_item: WorkItem = task_queue.get()
        if work_item is _SENTINEL:
            worker_logger.info(
                "ProcessWorker[%d] received sentinel, shutting down.", process_idx
            )
            break

        status = CompletionStatus.SUCCESS
        error: Optional[str] = None

        try:
            # Execute the user's worker function
            # Note: For process-based workers, the worker_fn itself should be synchronous
            # or handle its own async event loop if it's async.
            # Here, we assume it's a synchronous callable for simplicity in the process context.
            # Real async worker in process would need more complex setup.
            worker_fn(work_item)
        except Exception as e:
            status = CompletionStatus.FAILURE
            error = str(e)
            worker_logger.exception(
                "Task for offset %d failed with exception in ProcessWorker[%d].",
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
        self._in_flight_count_tracker: Value[int] = Value(
            "i", 0
        )  # Track in-flight tasks

        self._logger = logging.getLogger(__name__)

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
                ),
            )
            self._workers.append(worker)
            worker.start()
            self._logger.info("Started ProcessWorker[%d] (PID: %d)", i, worker.pid)

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 태스크 큐에 넣습니다.
        """
        await asyncio.to_thread(self._task_queue.put, work_item)
        with self._in_flight_count_tracker.get_lock():
            self._in_flight_count_tracker.value += 1

    async def poll_completed_events(self) -> List[CompletionEvent]:
        """
        완료 큐에서 모든 완료 이벤트를 가져와 리스트로 반환합니다.
        """
        completed_events: List[CompletionEvent] = []
        while not self._completion_queue.empty():
            try:
                event = self._completion_queue.get_nowait()
                completed_events.append(event)
                with self._in_flight_count_tracker.get_lock():
                    self._in_flight_count_tracker.value -= 1
            except Exception as e:
                _logger.error("Error getting item from completion queue: %s", e)
                break  # Exit if there's an issue with the queue
        return completed_events

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.
        """
        return self._in_flight_count_tracker.value

    async def shutdown(self) -> None:
        """
        실행 엔진을 정상적으로 종료합니다. 모든 워커 프로세스에 종료 시그널을 보내고 대기합니다.
        """
        _logger.info("Initiating ProcessExecutionEngine shutdown.")
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
