from typing import List

from pyrallel_consumer.dto import CompletionEvent, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class AsyncExecutionEngine(BaseExecutionEngine):
    """
    비동기 실행 엔진의 기본 구현입니다.

    Args:
        BaseExecutionEngine (_type_): BaseExecutionEngine을 상속받습니다.
    """

    async def submit(self, work_item: WorkItem) -> None:
        """
        제출된 작업 항목을 처리합니다.

        Args:
            work_item (WorkItem): 제출할 작업 항목

        Raises:
            NotImplementedError: 구현되지 않은 메서드 호출 시 발생
        """
        raise NotImplementedError

    async def poll_completed_events(self) -> List[CompletionEvent]:
        """
        완료된 이벤트를 폴링합니다.

        Raises:
            NotImplementedError: 구현되지 않은 메서드 호출 시 발생

        Returns:
            List[CompletionEvent]: 완료된 이벤트 목록
        """
        raise NotImplementedError

    def get_in_flight_count(self) -> int:
        """
        현재 처리 중인 작업 항목의 수를 반환합니다.

        Raises:
            NotImplementedError: 구현되지 않은 메서드 호출 시 발생

        Returns:
            int: 현재 처리 중인 작업 항목의 수
        """
        raise NotImplementedError

    async def shutdown(self) -> None:
        """
        실행 엔진을 정상적으로 종료합니다.

        Raises:
            NotImplementedError: 구현되지 않은 메서드 호출 시 발생

        Returns:
            None
        """
        raise NotImplementedError
