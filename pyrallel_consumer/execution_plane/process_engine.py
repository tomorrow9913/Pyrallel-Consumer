from collections.abc import Callable
from typing import Any, List

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import CompletionEvent, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


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
        # TODO: Implement process-specific initialization (e.g., process pool, IPC queues)

    async def submit(self, work_item: WorkItem) -> None:
        raise NotImplementedError

    async def poll_completed_events(self) -> List[CompletionEvent]:
        raise NotImplementedError

    def get_in_flight_count(self) -> int:
        raise NotImplementedError

    async def shutdown(self) -> None:
        raise NotImplementedError
