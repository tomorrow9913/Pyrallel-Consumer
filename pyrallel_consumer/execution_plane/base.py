from abc import ABC, abstractmethod
from typing import List

from pyrallel_consumer.dto import CompletionEvent, WorkItem


class BaseExecutionEngine(ABC):
    """
    Basic abstract class for execution engines.
    실행 엔진의 기본 추상 클래스입니다.

    Args:
        ABC (_type_): Abstract Base Class
    """

    @abstractmethod
    async def submit(self, work_item: WorkItem) -> None:
        """
        Submits a WorkItem to the execution engine for processing.
        작업 항목을 처리하기 위해 실행 엔진에 제출합니다.

        Args:
            work_item (WorkItem): 제출할 작업 항목
        """

    @abstractmethod
    async def poll_completed_events(self) -> List[CompletionEvent]:
        """
        Polls for completed events from the execution engine.
        실행 엔진에서 완료된 이벤트를 폴링합니다.

        Returns:
            List[CompletionEvent]: 완료된 이벤트 리스트
        """

    @abstractmethod
    def get_in_flight_count(
        self,
    ) -> int:  # Renamed from in_flight to be consistent with get_* naming
        """
        Returns the number of messages currently in flight.
        현재 처리 중인 메시지 수를 반환합니다.

        Returns:
            int: 현재 처리 중인 메시지 수
        """

    @abstractmethod
    async def shutdown(self) -> None:
        """
        Shuts down the execution engine gracefully.
        실행 엔진을 정상적으로 종료합니다.

        Returns:
            None
        """
