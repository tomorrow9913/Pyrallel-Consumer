from typing import List

import pytest

from pyrallel_consumer.dto import CompletionEvent, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class ConcreteExecutionEngine(BaseExecutionEngine):
    def __init__(self) -> None:
        self.submitted_work_items: list[WorkItem] = []

    async def submit(self, work_item: WorkItem) -> None:
        self.submitted_work_items.append(work_item)

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        return []

    async def wait_for_completion(self, timeout_seconds=None) -> bool:
        return False

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

    async def wait_for_completion(self, timeout_seconds=None) -> bool:
        return False

    # get_in_flight_count and shutdown are intentionally not implemented


def _instantiate_engine(engine_type: type[object]) -> object:
    return engine_type()


def test_incomplete_execution_engine_raises_type_error():
    with pytest.raises(TypeError) as excinfo:
        _instantiate_engine(IncompleteExecutionEngine)
    assert (
        "Can't instantiate abstract class IncompleteExecutionEngine without an "
        "implementation for abstract methods 'get_in_flight_count', 'shutdown'"
        in str(excinfo.value)
    )


def test_concrete_execution_engine_can_be_instantiated():
    try:
        engine = ConcreteExecutionEngine()
        assert isinstance(engine, ConcreteExecutionEngine)
    except TypeError as e:
        pytest.fail(f"Could not instantiate ConcreteExecutionEngine: {e}")


def test_base_execution_engine_min_inflight_offset_defaults_to_none():
    engine = ConcreteExecutionEngine()

    assert (
        engine.get_min_inflight_offset(TopicPartition(topic="test", partition=0))
        is None
    )


@pytest.mark.asyncio
async def test_submit_batch_fallback_submits_each_work_item_in_order():
    engine = ConcreteExecutionEngine()
    first = WorkItem(
        id="work-1",
        tp=TopicPartition(topic="test", partition=0),
        offset=1,
        epoch=0,
        key="key-1",
        payload=b"payload-1",
    )
    second = WorkItem(
        id="work-2",
        tp=TopicPartition(topic="test", partition=0),
        offset=2,
        epoch=0,
        key="key-1",
        payload=b"payload-2",
    )

    await engine.submit_batch([first, second])

    assert engine.submitted_work_items == [first, second]
