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

    # Additional contract tests can be added here
    # - test for correct event completion (success/failure)
    # - test for handling multiple submits and completions
    # - test for shutdown with pending tasks
    # - test for task timeout/error handling (for engines that implement it)
