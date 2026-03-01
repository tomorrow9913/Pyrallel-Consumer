import asyncio
import contextvars
import logging
import random
from asyncio import Semaphore, Task
from collections.abc import Callable
from typing import Any, List, Optional, Set

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class AsyncExecutionEngine(BaseExecutionEngine):
    """
    비동기 실행 엔진의 구현입니다. asyncio.Task를 기반으로 워커 함수를 실행하고,
    세마포어를 사용하여 동시 실행 태스크 수를 제어합니다.

    Args:
        config (ExecutionConfig): 실행 엔진 설정.
        worker_fn (Callable[[WorkItem], Any]): 사용자 정의 비동기 워커 함수.
    """

    def __init__(self, config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]):
        self._config = config
        self._worker_fn = worker_fn
        self._semaphore = Semaphore(config.max_in_flight)
        self._completion_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        self._in_flight_tasks: Set[Task] = set()
        self._shutdown_event = asyncio.Event()

        # Logger for this module
        self._logger = logging.getLogger(__name__)

        # Context propagation (initial capture for consistent logging in tasks)
        # Note: contextvars.copy_context() is primarily for when the worker function itself
        # depends on contextvars set up by the caller. For simple logging/tracing,
        # passing relevant IDs explicitly or relying on thread-local storage might be simpler.
        self._context = contextvars.copy_context()

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 처리합니다. 세마포어를 획득하고, 새 비동기 태스크를 생성하여 워커 함수를 실행합니다.

        Args:
            work_item (WorkItem): 제출할 작업 항목
        """
        if self._shutdown_event.is_set():
            raise RuntimeError("Engine is shutting down, cannot accept new work")

        await self._semaphore.acquire()

        # Create a task to execute the worker function within the captured context
        task = self._context.copy().run(
            asyncio.create_task, self._execute_worker_task(work_item)
        )
        self._in_flight_tasks.add(task)
        task.add_done_callback(self._task_done_callback)

    async def _execute_worker_task(self, work_item: WorkItem) -> None:
        """
        사용자 워커 함수를 실행하고 결과를 완료 큐에 넣습니다.
        예외 처리 및 타임아웃 로직을 포함합니다.
        """
        status = CompletionStatus.SUCCESS
        error: Optional[str] = None
        attempt = 0

        for attempt in range(1, self._config.max_retries + 1):
            try:
                await asyncio.wait_for(
                    self._worker_fn(work_item),
                    timeout=self._config.async_config.task_timeout_ms / 1000.0,
                )
                status = CompletionStatus.SUCCESS
                error = None
                break
            except asyncio.TimeoutError:
                status = CompletionStatus.FAILURE
                error = "Task for offset %d timed out." % work_item.offset
                self._logger.error(error)
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)
            except Exception as e:
                status = CompletionStatus.FAILURE
                error = str(e)
                self._logger.exception(
                    "Task for offset %d failed with exception: %s"
                    % (work_item.offset, error)
                )
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)

        completion_event = CompletionEvent(
            id=work_item.id,
            tp=work_item.tp,
            offset=work_item.offset,
            epoch=work_item.epoch,
            status=status,
            error=error,
            attempt=attempt,
        )
        await self._completion_queue.put(completion_event)

    async def _apply_backoff(self, attempt: int) -> None:
        base_delay_ms = self._config.retry_backoff_ms

        if self._config.exponential_backoff:
            delay_ms = base_delay_ms * (2 ** (attempt - 1))
            delay_ms = min(delay_ms, self._config.max_retry_backoff_ms)
        else:
            delay_ms = base_delay_ms

        if self._config.retry_jitter_ms > 0:
            jitter = random.uniform(0, self._config.retry_jitter_ms)
            delay_ms += jitter

        await asyncio.sleep(delay_ms / 1000.0)

    def _task_done_callback(self, task: Task) -> None:
        """
        태스크 완료 시 호출되는 콜백. 세마포어를 해제하고 완료된 태스크를 추적 목록에서 제거합니다.
        """
        self._in_flight_tasks.discard(task)
        self._semaphore.release()

        # Check for exceptions that were not caught within _execute_worker_task
        # (e.g., if the task itself was cancelled or an unexpected error occurred before try/except)
        if task.cancelled():
            self._logger.warning("Task %s was cancelled." % task.get_name())
        elif task.exception():
            # Exception already handled and logged in _execute_worker_task,
            # but this catches any unhandled exceptions during the callback itself.
            self._logger.error(
                "Unhandled exception in task done callback for task %s."
                % task.get_name(),
                exc_info=task.exception(),
            )

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
            completed_events.append(self._completion_queue.get_nowait())
        return completed_events

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.
        """
        return len(self._in_flight_tasks)

    async def shutdown(self) -> None:
        """
        실행 엔진을 정상적으로 종료합니다. 모든 진행 중인 태스크가 완료되거나 취소될 때까지 대기합니다.
        """
        self._logger.debug("Initiating AsyncExecutionEngine shutdown.")
        self._shutdown_event.set()

        grace_timeout = self._config.async_config.shutdown_grace_timeout_ms / 1000.0

        if self._in_flight_tasks:
            self._logger.debug(
                "Waiting for %d in-flight tasks to complete."
                % len(self._in_flight_tasks)
            )

            done, pending = await asyncio.wait(
                self._in_flight_tasks, timeout=grace_timeout
            )

            if pending:
                self._logger.warning(
                    "Cancelling %d task(s) after shutdown grace timeout", len(pending)
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            if done:
                await asyncio.gather(*done, return_exceptions=True)
            self._logger.debug("All in-flight tasks handled during shutdown.")

        self._logger.debug("AsyncExecutionEngine shutdown complete.")
