import asyncio

import pytest

from pyrallel_consumer.config import AsyncConfig, ExecutionConfig
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)


# Dummy async worker function for testing
async def async_worker_fn(work_item: WorkItem):
    if work_item.payload == b"fail":
        raise ValueError("Simulated worker failure")
    elif work_item.payload == b"timeout":
        await asyncio.sleep(100)  # Simulate a long-running task
    else:
        await asyncio.sleep(0.01)  # Simulate some work


class TestAsyncExecutionEngine(BaseExecutionEngineContractTest):
    @pytest.fixture
    def config(self):
        # Default config for AsyncExecutionEngine tests
        return ExecutionConfig(
            mode="async",
            max_in_flight=2,  # Allow multiple concurrent tasks for testing
            async_config=AsyncConfig(task_timeout_ms=500),  # 500ms timeout
        )

    @pytest.fixture
    def engine(self, config):
        return AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

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
    def mock_failing_work_item(self):
        return WorkItem(
            id="test-id-failing",
            tp=TopicPartition(topic="test", partition=0),
            offset=1,
            epoch=0,
            key="key",
            payload=b"fail",
        )

    @pytest.fixture
    def mock_timeout_work_item(self):
        return WorkItem(
            id="test-id-timeout",
            tp=TopicPartition(topic="test", partition=0),
            offset=2,
            epoch=0,
            key="key",
            payload=b"timeout",
        )

    @pytest.mark.asyncio
    async def test_submit_executes_worker_function(
        self, engine: AsyncExecutionEngine, mock_work_item: WorkItem
    ):
        await engine.submit(mock_work_item)
        await asyncio.sleep(0.05)  # Give some time for the task to complete
        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 1
        assert completed_events[0].id == mock_work_item.id
        assert completed_events[0].status == CompletionStatus.SUCCESS
        assert engine.get_in_flight_count() == 0  # Should be 0 after completion

    @pytest.mark.asyncio
    async def test_submit_handles_worker_failure(
        self, engine: AsyncExecutionEngine, mock_failing_work_item: WorkItem
    ):
        await engine.submit(mock_failing_work_item)
        await asyncio.sleep(0.05)  # Give some time for the task to complete
        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 1
        assert completed_events[0].id == mock_failing_work_item.id
        assert completed_events[0].status == CompletionStatus.FAILURE
        assert completed_events[0].error is not None
        assert "Simulated worker failure" in completed_events[0].error
        assert engine.get_in_flight_count() == 0

    @pytest.mark.asyncio
    async def test_submit_handles_worker_timeout(
        self, engine: AsyncExecutionEngine, mock_timeout_work_item: WorkItem
    ):
        await engine.submit(mock_timeout_work_item)
        # Wait slightly more than the timeout set in config (500ms)
        await asyncio.sleep(0.6)
        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 1
        assert completed_events[0].id == mock_timeout_work_item.id
        assert completed_events[0].status == CompletionStatus.FAILURE
        assert (
            completed_events[0].error
            == "Task for offset %d timed out." % mock_timeout_work_item.offset
        )
        assert engine.get_in_flight_count() == 0

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_in_flight_tasks(
        self,
        config,
        engine: AsyncExecutionEngine,
        mock_work_item: WorkItem,
        mock_timeout_work_item: WorkItem,
    ):
        # Submit tasks that will take some time
        await engine.submit(mock_work_item)
        await engine.submit(mock_timeout_work_item)  # This task will timeout

        # Ensure tasks are in-flight
        assert engine.get_in_flight_count() == 2

        # Shutdown should wait for them to finish (or timeout in this case)
        start_time = asyncio.get_event_loop().time()
        await engine.shutdown()
        end_time = asyncio.get_event_loop().time()

        # Check that shutdown took at least the timeout duration (approx 500ms)
        # Since one task times out and the other finishes quickly.
        # It should at least wait for the timeout of the timeout task.
        assert (end_time - start_time) >= (
            config.async_config.task_timeout_ms / 1000.0
        ) - 0.05  # Allow for small variance
        assert engine.get_in_flight_count() == 0

        # After shutdown, no new tasks should be submittable (though not explicitly tested here)
        # And any completed events should have been processed
        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 2  # One success, one timeout

        success_event = next(e for e in completed_events if e.id == mock_work_item.id)
        timeout_event = next(
            e for e in completed_events if e.id == mock_timeout_work_item.id
        )

        assert success_event.status == CompletionStatus.SUCCESS
        assert timeout_event.status == CompletionStatus.FAILURE
        assert timeout_event.error is not None and "timed out" in timeout_event.error
