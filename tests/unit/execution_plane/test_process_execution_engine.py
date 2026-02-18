import asyncio
import time

import pytest
import pytest_asyncio

from pyrallel_consumer.config import AsyncConfig, ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)


# Dummy synchronous worker function for testing
def sync_worker_fn(work_item: WorkItem):
    if work_item.payload == b"fail":
        raise ValueError("Simulated worker failure")
    elif work_item.payload == b"sleep":
        time.sleep(0.1)  # Simulate some work
    else:
        pass


class TestProcessExecutionEngine(BaseExecutionEngineContractTest):
    @pytest.fixture
    def config(self):
        return ExecutionConfig(
            mode="process",
            max_in_flight=2,
            max_retries=3,
            retry_backoff_ms=50,
            exponential_backoff=True,
            max_retry_backoff_ms=200,
            retry_jitter_ms=10,
            process_config=ProcessConfig(
                process_count=2,
                queue_size=10,
                require_picklable_worker=True,
            ),
            async_config=AsyncConfig(task_timeout_ms=1000),
        )

    @pytest_asyncio.fixture
    async def engine(self, config):
        # ProcessExecutionEngine needs a synchronous worker function
        eng = ProcessExecutionEngine(config=config, worker_fn=sync_worker_fn)
        yield eng
        await eng.shutdown()

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
    def mock_sleeping_work_item(self):
        return WorkItem(
            id="test-id-sleeping",
            tp=TopicPartition(topic="test", partition=0),
            offset=2,
            epoch=0,
            key="key",
            payload=b"sleep",
        )

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_in_flight_tasks(
        self, engine: ProcessExecutionEngine, mock_sleeping_work_item: WorkItem
    ):
        await engine.submit(mock_sleeping_work_item)
        await engine.submit(mock_sleeping_work_item)
        await asyncio.sleep(0.1)  # Give workers time to pick up tasks

        # Ensure tasks are in-flight
        assert engine.get_in_flight_count() == 2

        start_time = asyncio.get_event_loop().time()
        await engine.shutdown()
        end_time = asyncio.get_event_loop().time()

        # Check that shutdown waited for tasks to complete
        assert (
            end_time - start_time
        ) >= 0.1 * 2 - 0.05  # 2 tasks, each sleeps for 0.1s. Allow for some variance.

        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 2
        assert all(e.status == CompletionStatus.SUCCESS for e in completed_events)
        assert engine.get_in_flight_count() == 0
