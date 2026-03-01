import asyncio

import pytest

from pyrallel_consumer.config import AsyncConfig, ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)

# Global counter for retry tests
retry_attempt_counter = 0


# Dummy async worker function for testing
async def async_worker_fn(work_item: WorkItem):
    if work_item.payload == b"fail":
        raise ValueError("Simulated worker failure")
    elif work_item.payload == b"timeout":
        await asyncio.sleep(100)  # Simulate a long-running task
    else:
        await asyncio.sleep(0.01)  # Simulate some work


# Worker that succeeds on 2nd attempt
async def async_worker_fn_succeed_on_retry(work_item: WorkItem):
    global retry_attempt_counter
    retry_attempt_counter += 1
    if retry_attempt_counter < 2:
        raise ValueError("Transient failure")
    await asyncio.sleep(0.01)


# Worker that always fails
async def async_worker_fn_always_fails(work_item: WorkItem):
    raise ValueError("Permanent failure")


class TestAsyncExecutionEngine(BaseExecutionEngineContractTest):
    @pytest.fixture
    def config(self):
        return ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=2,
            max_retries=1,
            async_config=AsyncConfig(task_timeout_ms=500),
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

    @pytest.mark.asyncio
    async def test_submit_rejects_after_shutdown(
        self, config, mock_work_item: WorkItem
    ):
        engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

        await engine.shutdown()

        with pytest.raises(RuntimeError):
            await engine.submit(mock_work_item)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_after_grace_timeout(self):
        async_cfg = AsyncConfig(task_timeout_ms=5000)
        setattr(async_cfg, "shutdown_grace_timeout_ms", 50)  # type: ignore[attr-defined]

        grace_config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=1,
            async_config=async_cfg,
        )

        async def blocking_worker(_: WorkItem):
            await asyncio.sleep(5)

        engine = AsyncExecutionEngine(config=grace_config, worker_fn=blocking_worker)

        work_item = WorkItem(
            id="blocking",
            tp=TopicPartition(topic="test", partition=0),
            offset=0,
            epoch=0,
            key="k",
            payload=b"block",
        )

        await engine.submit(work_item)

        start = asyncio.get_event_loop().time()
        await engine.shutdown()
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed < 1.0
        assert engine.get_in_flight_count() == 0


class TestAsyncExecutionEngineRetries:
    """Test retry logic with backoff in AsyncExecutionEngine"""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_shows_attempt_1(self):
        """When worker succeeds immediately, CompletionEvent.attempt should be 1"""
        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=3,
            retry_backoff_ms=100,
            exponential_backoff=True,
            max_retry_backoff_ms=1000,
            retry_jitter_ms=50,
        )
        engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

        work_item = WorkItem(
            id="test-success-1",
            tp=TopicPartition(topic="test", partition=0),
            offset=100,
            epoch=1,
            key="key1",
            payload=b"success",
        )

        await engine.submit(work_item)
        await asyncio.sleep(0.1)

        events = await engine.poll_completed_events()
        assert len(events) == 1
        assert events[0].status == CompletionStatus.SUCCESS
        assert events[0].attempt == 1
        assert events[0].error is None

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_success_on_second_attempt_shows_attempt_2(self):
        """When worker fails once then succeeds, CompletionEvent.attempt should be 2"""
        global retry_attempt_counter
        retry_attempt_counter = 0

        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=3,
            retry_backoff_ms=50,
            exponential_backoff=False,
            max_retry_backoff_ms=1000,
            retry_jitter_ms=10,
        )
        engine = AsyncExecutionEngine(
            config=config, worker_fn=async_worker_fn_succeed_on_retry
        )

        work_item = WorkItem(
            id="test-retry-success",
            tp=TopicPartition(topic="test", partition=0),
            offset=200,
            epoch=1,
            key="key2",
            payload=b"retry_success",
        )

        await engine.submit(work_item)
        await asyncio.sleep(0.3)

        events = await engine.poll_completed_events()
        assert len(events) == 1
        assert events[0].status == CompletionStatus.SUCCESS
        assert events[0].attempt == 2
        assert events[0].error is None

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self):
        """When worker fails all retries, CompletionEvent.attempt should equal max_retries"""
        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=3,
            retry_backoff_ms=20,
            exponential_backoff=True,
            max_retry_backoff_ms=1000,
            retry_jitter_ms=5,
        )
        engine = AsyncExecutionEngine(
            config=config, worker_fn=async_worker_fn_always_fails
        )

        work_item = WorkItem(
            id="test-retry-fail",
            tp=TopicPartition(topic="test", partition=0),
            offset=300,
            epoch=1,
            key="key3",
            payload=b"always_fail",
        )

        await engine.submit(work_item)
        await asyncio.sleep(0.5)

        events = await engine.poll_completed_events()
        assert len(events) == 1
        assert events[0].status == CompletionStatus.FAILURE
        assert events[0].attempt == 3
        assert events[0].error is not None
        assert "Permanent failure" in events[0].error

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_timeout_retried_and_counted(self):
        """Timeouts should be treated as failures and retried"""
        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=2,
            retry_backoff_ms=10,
            exponential_backoff=False,
            max_retry_backoff_ms=1000,
            retry_jitter_ms=5,
            async_config=AsyncConfig(task_timeout_ms=100),
        )
        engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

        work_item = WorkItem(
            id="test-timeout-retry",
            tp=TopicPartition(topic="test", partition=0),
            offset=400,
            epoch=1,
            key="key4",
            payload=b"timeout",
        )

        await engine.submit(work_item)
        await asyncio.sleep(0.5)

        events = await engine.poll_completed_events()
        assert len(events) == 1
        assert events[0].status == CompletionStatus.FAILURE
        assert events[0].attempt == 2
        assert events[0].error is not None
        assert "timed out" in events[0].error.lower()

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Verify exponential backoff increases delay between retries"""
        global retry_attempt_counter
        retry_attempt_counter = 0

        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=3,
            retry_backoff_ms=50,
            exponential_backoff=True,
            max_retry_backoff_ms=500,
            retry_jitter_ms=0,
        )
        engine = AsyncExecutionEngine(
            config=config, worker_fn=async_worker_fn_always_fails
        )

        work_item = WorkItem(
            id="test-backoff-timing",
            tp=TopicPartition(topic="test", partition=0),
            offset=500,
            epoch=1,
            key="key5",
            payload=b"backoff_test",
        )

        start_time = asyncio.get_event_loop().time()
        await engine.submit(work_item)
        await asyncio.sleep(0.5)
        end_time = asyncio.get_event_loop().time()

        events = await engine.poll_completed_events()
        assert len(events) == 1

        elapsed = end_time - start_time
        expected_min_delay = 0.05 + 0.1
        assert elapsed >= expected_min_delay

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_backoff_respects_max_cap(self):
        """Verify backoff doesn't exceed max_retry_backoff_ms"""
        config = ExecutionConfig(
            mode=ExecutionMode.ASYNC,
            max_in_flight=10,
            max_retries=5,
            retry_backoff_ms=100,
            exponential_backoff=True,
            max_retry_backoff_ms=150,
            retry_jitter_ms=0,
        )
        engine = AsyncExecutionEngine(
            config=config, worker_fn=async_worker_fn_always_fails
        )

        work_item = WorkItem(
            id="test-backoff-cap",
            tp=TopicPartition(topic="test", partition=0),
            offset=600,
            epoch=1,
            key="key6",
            payload=b"cap_test",
        )

        start_time = asyncio.get_event_loop().time()
        await engine.submit(work_item)
        await asyncio.sleep(1.2)
        end_time = asyncio.get_event_loop().time()

        events = await engine.poll_completed_events()
        assert len(events) == 1
        assert events[0].attempt == 5

        elapsed = end_time - start_time
        min_expected = 0.1 + 0.15 + 0.15 + 0.15
        assert elapsed >= min_expected

        await engine.shutdown()
