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
        initial_in_flight = engine.get_in_flight_count()
        await engine.submit(mock_work_item)
        # Assuming that submit immediately increases in-flight count for most engines
        assert engine.get_in_flight_count() == initial_in_flight + 1

    @pytest.mark.asyncio
    async def test_poll_completed_events_returns_list_of_events(
        self, engine: BaseExecutionEngine
    ):
        events = await engine.poll_completed_events()
        assert isinstance(events, list)
        for event in events:
            assert isinstance(event, CompletionEvent)

    @pytest.mark.asyncio
    async def test_shutdown_cleans_up_resources(self, engine: BaseExecutionEngine):
        # This test primarily verifies that shutdown can be called without error.
        # More specific cleanup assertions would be in concrete engine tests.
        await engine.shutdown()
        # After shutdown, in-flight count should ideally be zero, but depends on engine's shutdown behavior
        assert (
            engine.get_in_flight_count() == 0
        )  # Assuming shutdown clears in-flight items

    @pytest.mark.asyncio
    async def test_in_flight_count_reflects_active_tasks(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        await engine.submit(mock_work_item)
        assert engine.get_in_flight_count() >= 1

    @pytest.mark.asyncio
    async def test_submit_executes_worker_function(
        self, engine: BaseExecutionEngine, mock_work_item: WorkItem
    ):
        """
        Contract: Submitting a valid work item should eventually result in a SUCCESS completion event.
        """
        await engine.submit(mock_work_item)

        # Wait for processing (concrete tests might need to adjust timing or use proper synchronization)
        await asyncio.sleep(0.2)

        completed_events = await engine.poll_completed_events()

        # We might need a retry loop here for robustness, but for now simple wait
        # If empty, give it one more chance
        if not completed_events:
            await asyncio.sleep(1.0)
            completed_events = await engine.poll_completed_events()

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
        await engine.submit(mock_failing_work_item)

        await asyncio.sleep(0.5)

        completed_events = await engine.poll_completed_events()

        if not completed_events:
            await asyncio.sleep(1.5)
            completed_events = await engine.poll_completed_events()

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
        await engine.submit(mock_work_item)

        await asyncio.sleep(0.2)

        completed_events = await engine.poll_completed_events()

        if not completed_events:
            await asyncio.sleep(1.0)
            completed_events = await engine.poll_completed_events()

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
        await engine.submit(mock_failing_work_item)

        await asyncio.sleep(0.5)

        completed_events = await engine.poll_completed_events()

        if not completed_events:
            await asyncio.sleep(1.5)
            completed_events = await engine.poll_completed_events()

        assert len(completed_events) == 1
        event = completed_events[0]
        assert hasattr(event, "attempt"), "CompletionEvent must have 'attempt' field"
        assert event.attempt >= 1, "attempt must be 1-based (>= 1)"
        assert event.status == CompletionStatus.FAILURE
