from typing import List

import pytest

from pyrallel_consumer.dto import CompletionEvent, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine, BatchSubmitError


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


class FailingSecondSubmitEngine(ConcreteExecutionEngine):
    def __init__(self) -> None:
        super().__init__()
        self.original_error = RuntimeError("submit-two-failed")

    async def submit(self, work_item: WorkItem) -> None:
        if len(self.submitted_work_items) == 1:
            raise self.original_error
        await super().submit(work_item)


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
    # Given: inputs for `incomplete execution engine raises type error` are prepared.
    # When: the base execution engine code path is exercised.
    # Then: the expected `incomplete execution engine raises type error` behavior is asserted.
    with pytest.raises(TypeError) as excinfo:
        _instantiate_engine(IncompleteExecutionEngine)
    assert (
        "Can't instantiate abstract class IncompleteExecutionEngine without an "
        "implementation for abstract methods 'get_in_flight_count', 'shutdown'"
        in str(excinfo.value)
    )


def test_concrete_execution_engine_can_be_instantiated():
    # Given: inputs for `concrete execution engine can be instantiated` are prepared.
    # When: the base execution engine code path is exercised.
    # Then: the expected `concrete execution engine can be instantiated` behavior is asserted.
    try:
        engine = ConcreteExecutionEngine()
        assert isinstance(engine, ConcreteExecutionEngine)
    except TypeError as e:
        pytest.fail(f"Could not instantiate ConcreteExecutionEngine: {e}")


def test_base_execution_engine_min_inflight_offset_defaults_to_none():
    # Given: inputs for `base execution engine min inflight offset def...` are prepared.
    engine = ConcreteExecutionEngine()

    # When: the base execution engine code path is exercised.
    # Then: the expected `base execution engine min inflight offset def...` behavior is asserted.
    assert (
        engine.get_min_inflight_offset(TopicPartition(topic="test", partition=0))
        is None
    )


def test_base_execution_engine_runtime_metrics_default_to_none():
    # Given: inputs for `base execution engine runtime metrics default...` are prepared.
    engine = ConcreteExecutionEngine()

    # When: the base execution engine code path is exercised.
    # Then: the expected `base execution engine runtime metrics default...` behavior is asserted.
    assert engine.get_runtime_metrics() is None


def test_base_execution_engine_ordered_route_batch_capability_defaults_to_false():
    # Given: inputs for `base execution engine ordered route batch cap...` are prepared.
    # When: the base execution engine code path is exercised.
    engine = ConcreteExecutionEngine()

    # Then: the expected `base execution engine ordered route batch cap...` behavior is asserted.
    assert engine.supports_ordered_route_batch is False


@pytest.mark.asyncio
async def test_submit_batch_fallback_submits_each_work_item_in_order():
    # Given: inputs for `submit batch fallback submits each work item...` are prepared.
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

    # When: the base execution engine code path is exercised.
    await engine.submit_batch([first, second])

    # Then: the expected `submit batch fallback submits each work item...` behavior is asserted.
    assert engine.submitted_work_items == [first, second]


@pytest.mark.asyncio
async def test_submit_batch_fallback_atomic_or_batch_submit_error_reports_accepted_prefix():
    # Given: inputs for `submit batch fallback atomic or batch submit...` are prepared.
    engine = FailingSecondSubmitEngine()
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

    # When: the base execution engine code path is exercised.
    # Then: the expected `submit batch fallback atomic or batch submit...` behavior is asserted.
    with pytest.raises(BatchSubmitError) as excinfo:
        await engine.submit_batch([first, second])

    assert excinfo.value.accepted_count == 1
    assert engine.submitted_work_items == [first]


@pytest.mark.asyncio
async def test_batch_submit_error_preserves_original_exception():
    # Given: inputs for `batch submit error preserves original exception` are prepared.
    engine = FailingSecondSubmitEngine()
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

    # When: the base execution engine code path is exercised.
    # Then: the expected `batch submit error preserves original exception` behavior is asserted.
    with pytest.raises(BatchSubmitError) as excinfo:
        await engine.submit_batch([first, second])

    assert excinfo.value.original_error is engine.original_error


def test_submit_batch_docstring_names_atomic_or_batch_submit_error_contract():
    # Given: inputs for `submit batch docstring names atomic or batch...` are prepared.
    # When: the base execution engine code path is exercised.
    # Then: the expected `submit batch docstring names atomic or batch...` behavior is asserted.
    assert BaseExecutionEngine.submit_batch.__doc__ is not None
    assert "atomic" in BaseExecutionEngine.submit_batch.__doc__
    assert "BatchSubmitError" in BaseExecutionEngine.submit_batch.__doc__
