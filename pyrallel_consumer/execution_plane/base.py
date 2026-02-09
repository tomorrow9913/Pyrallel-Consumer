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
