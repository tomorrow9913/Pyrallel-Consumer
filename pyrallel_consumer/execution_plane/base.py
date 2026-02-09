from abc import ABC, abstractmethod
from typing import List

from pyrallel_consumer.dto import CompletionEvent, WorkItem


class BaseExecutionEngine(ABC):
    @abstractmethod
    async def submit(self, work_item: WorkItem) -> None:
        pass

    @abstractmethod
    async def poll_completed_events(self) -> List[CompletionEvent]:
        pass

    @abstractmethod
    def get_in_flight_count(
        self,
    ) -> int:  # Renamed from in_flight to be consistent with get_* naming
        pass

    @abstractmethod
    async def shutdown(self) -> None:
        pass
