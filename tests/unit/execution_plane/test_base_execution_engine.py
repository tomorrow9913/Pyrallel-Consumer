from typing import List

import pytest

from pyrallel_consumer.dto import CompletionEvent, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class ConcreteExecutionEngine(BaseExecutionEngine):
    async def submit(self, work_item: WorkItem) -> None:
        pass

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        return []

    def get_in_flight_count(self) -> int:
        return 0

    async def shutdown(self) -> None:
        pass


class IncompleteExecutionEngine(BaseExecutionEngine):
    async def submit(self, work_item: WorkItem) -> None:
        pass

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        return []

    # get_in_flight_count and shutdown are intentionally not implemented


def test_incomplete_execution_engine_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        IncompleteExecutionEngine()
    assert (
        "Can't instantiate abstract class IncompleteExecutionEngine without an implementation for abstract methods 'get_in_flight_count', 'shutdown'"
        in str(excinfo.value)
    )


def test_concrete_execution_engine_can_be_instantiated():
    try:
        engine = ConcreteExecutionEngine()
        assert isinstance(engine, ConcreteExecutionEngine)
    except TypeError as e:
        pytest.fail(f"Could not instantiate ConcreteExecutionEngine: {e}")
