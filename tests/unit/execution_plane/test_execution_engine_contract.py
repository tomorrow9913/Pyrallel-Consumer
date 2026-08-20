import asyncio
from abc import ABC, abstractmethod

import pytest

from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

ALLOWED_ENGINE_CONTRACT_METHODS = {
    "submit",
    "submit_batch",
    "poll_completed_events",
    "wait_for_completion",
    "get_in_flight_count",
    "get_runtime_metrics",
    "shutdown",
}


def test_base_execution_engine_contract_surface_excludes_transport_helpers() -> None:
    # Given: inputs for `base execution engine contract surface exclud...` are prepared.
    # When: the execution engine contract code path is exercised.
    public_methods = {
        name for name in dir(BaseExecutionEngine) if not name.startswith("_")
    }

    # Then: the expected `base execution engine contract surface exclud...` behavior is asserted.
    assert ALLOWED_ENGINE_CONTRACT_METHODS <= public_methods
    assert "dispatch_payload" not in public_methods
    assert "start_worker_task_source" not in public_methods
    assert "signal_shutdown" not in public_methods


def test_base_execution_engine_compatibility_hook_is_not_required_contract() -> None:
    # Given: inputs for `base execution engine compatibility hook is n...` are prepared.
    # When: the execution engine contract code path is exercised.
    public_methods = {
        name for name in dir(BaseExecutionEngine) if not name.startswith("_")
    }

    # Then: the expected `base execution engine compatibility hook is n...` behavior is asserted.
    assert "get_min_inflight_offset" in public_methods
    assert "get_min_inflight_offset" not in ALLOWED_ENGINE_CONTRACT_METHODS


class BaseExecutionEngineContractTest(ABC):
    """
    Base class for ExecutionEngine contract tests.
    Concrete test classes must implement `get_engine` to provide an instance of BaseExecutionEngine.
    """

    @pytest.fixture
    @abstractmethod
    def engine(self) -> BaseExecutionEngine:
        """
        Fixture that provides an instance of the concrete ExecutionEngine to be tested.
        """

    @pytest.fixture
    def mock_work_item(self):
        return WorkItem(
            id="test-id-1",
            tp=TopicPartition(topic="test", partition=0),
            offset=0,
            epoch=0,
            key="key",
            payload=b"payload",
        )

    @pytest.fixture
    def mock_completion_event(self):
        return CompletionEvent(
            id="test-id-1",
            tp=TopicPartition(topic="test", partition=0),
            offset=0,
            epoch=0,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )

    @pytest.fixture
    def mock_failing_work_item(self):
        return WorkItem(
            id="test-id-failing",
            tp=TopicPartition(topic="test", partition=0),
            offset=1,
            epoch=0,
            key="key",
            payload=b"fail",
        )

    @pytest.mark.asyncio
    async def test_submit_adds_to_in_flight_count(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        # Given: inputs for `submit adds to in flight count` are prepared.
        initial_in_flight = engine.get_in_flight_count()
        await engine.submit(mock_work_item)
        # Assuming that submit immediately increases in-flight count for most engines
        # When: the execution engine contract code path is exercised.
        # Then: the expected `submit adds to in flight count` behavior is asserted.
        assert engine.get_in_flight_count() == initial_in_flight + 1

    @pytest.mark.asyncio
    async def test_poll_completed_events_returns_list_of_events(
        self, engine: BaseExecutionEngine
    ):
        # Given: inputs for `poll completed events returns list of events` are prepared.
        # When: the execution engine contract code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `poll completed events returns list of events` behavior is asserted.
        assert isinstance(events, list)
        for event in events:
            assert isinstance(event, CompletionEvent)

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_resources(self, engine: BaseExecutionEngine):
        # This test primarily verifies that shutdown can be called without error.
        # More specific cleanup assertions would be in concrete engine tests.
        # Given: inputs for `shutdown cleans up resources` are prepared.
        await engine.shutdown()
        # After shutdown, in-flight count should ideally be zero, but depends on engine's shutdown behavior
        # When: the execution engine contract code path is exercised.
        # Then: the expected `shutdown cleans up resources` behavior is asserted.
        assert (
            engine.get_in_flight_count() == 0
        )  # Assuming shutdown clears in-flight items

    @pytest.mark.asyncio
    async def test_in_flight_count_reflects_active_tasks(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        # Given: inputs for `in flight count reflects active tasks` are prepared.
        await engine.submit(mock_work_item)
        # When: the execution engine contract code path is exercised.
        # Then: the expected `in flight count reflects active tasks` behavior is asserted.
        assert engine.get_in_flight_count() >= 1

    @pytest.mark.asyncio
    async def test_submit_executes_worker_function(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        """
        Contract: Submitting a valid work item should eventually result in a SUCCESS completion event.
        """
        # Given: inputs for `submit executes worker function` are prepared.
        await engine.submit(mock_work_item)

        # Wait for processing (concrete tests might need to adjust timing or use proper synchronization)
        await asyncio.sleep(0.2)

        completed_events = await engine.poll_completed_events()

        # We might need a retry loop here for robustness, but for now simple wait
        # If empty, give it one more chance
        # When: the execution engine contract code path is exercised.
        if not completed_events:
            await asyncio.sleep(1.0)
            completed_events = await engine.poll_completed_events()

        # Then: the expected `submit executes worker function` behavior is asserted.
        assert len(completed_events) == 1
        assert completed_events[0].id == mock_work_item.id
        assert completed_events[0].status == CompletionStatus.SUCCESS
        assert engine.get_in_flight_count() == 0

    @pytest.mark.asyncio
    async def test_submit_handles_worker_failure(
        self, engine: BaseExecutionEngine, mock_failing_work_item: WorkItem
    ):
        """
        Contract: Submitting a work item that causes the worker to fail should result in a FAILURE completion event.
        """
        # Given: inputs for `submit handles worker failure` are prepared.
        await engine.submit(mock_failing_work_item)

        await asyncio.sleep(0.5)

        completed_events = await engine.poll_completed_events()

        # When: the execution engine contract code path is exercised.
        if not completed_events:
            await asyncio.sleep(1.5)
            completed_events = await engine.poll_completed_events()

        # Then: the expected `submit handles worker failure` behavior is asserted.
        assert len(completed_events) == 1
        assert completed_events[0].id == mock_failing_work_item.id
        assert completed_events[0].status == CompletionStatus.FAILURE
        assert completed_events[0].error is not None
        assert "failure" in str(completed_events[0].error)
        assert engine.get_in_flight_count() == 0

    @pytest.mark.asyncio
    async def test_completion_event_has_attempt_field(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        """
        Contract: CompletionEvent must include attempt field tracking the attempt count (1-based).
        Success on first attempt should have attempt=1.
        """
        # Given: inputs for `completion event has attempt field` are prepared.
        await engine.submit(mock_work_item)

        await asyncio.sleep(0.2)

        completed_events = await engine.poll_completed_events()

        # When: the execution engine contract code path is exercised.
        if not completed_events:
            await asyncio.sleep(1.0)
            completed_events = await engine.poll_completed_events()

        # Then: the expected `completion event has attempt field` behavior is asserted.
        assert len(completed_events) == 1
        event = completed_events[0]
        assert hasattr(event, "attempt"), "CompletionEvent must have 'attempt' field"
        assert event.attempt >= 1, "attempt must be 1-based (>= 1)"
        assert event.status == CompletionStatus.SUCCESS
        assert event.attempt == 1, "First successful attempt should have attempt=1"

    @pytest.mark.asyncio
    async def test_completion_event_attempt_on_failure(
        self, engine: BaseExecutionEngine, mock_failing_work_item: WorkItem
    ):
        """
        Contract: CompletionEvent for failures must also include attempt field.
        With retries enabled, attempt count reflects total attempts made.
        """
        # Given: inputs for `completion event attempt on failure` are prepared.
        await engine.submit(mock_failing_work_item)

        await asyncio.sleep(0.5)

        completed_events = await engine.poll_completed_events()

        # When: the execution engine contract code path is exercised.
        if not completed_events:
            await asyncio.sleep(1.5)
            completed_events = await engine.poll_completed_events()

        # Then: the expected `completion event attempt on failure` behavior is asserted.
        assert len(completed_events) == 1
        event = completed_events[0]
        assert hasattr(event, "attempt"), "CompletionEvent must have 'attempt' field"
        assert event.attempt >= 1, "attempt must be 1-based (>= 1)"
        assert event.status == CompletionStatus.FAILURE
