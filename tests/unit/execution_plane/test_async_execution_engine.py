import asyncio

import pytest

from pyrallel_consumer.config import AsyncConfig, ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionStatus,
    EngineWorkerDiagnostics,
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
        # Given: inputs for `submit handles worker timeout` are prepared.
        await engine.submit(mock_timeout_work_item)
        # Wait slightly more than the timeout set in config (500ms)
        await asyncio.sleep(0.6)
        # When: the async execution engine code path is exercised.
        completed_events = await engine.poll_completed_events()
        # Then: the expected `submit handles worker timeout` behavior is asserted.
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
        # Given: inputs for `shutdown waits for in flight tasks` are prepared.
        await engine.submit(mock_work_item)
        await engine.submit(mock_timeout_work_item)  # This task will timeout

        # Ensure tasks are in-flight
        # When: the async execution engine code path is exercised.
        # Then: the expected `shutdown waits for in flight tasks` behavior is asserted.
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
        # Given: inputs for `submit rejects after shutdown` are prepared.
        engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

        await engine.shutdown()

        # When: the async execution engine code path is exercised.
        # Then: the expected `submit rejects after shutdown` behavior is asserted.
        with pytest.raises(RuntimeError):
            await engine.submit(mock_work_item)

    @pytest.mark.asyncio
    async def test_shutdown_cancels_after_grace_timeout(self):
        # Given: inputs for `shutdown cancels after grace timeout` are prepared.
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
        # When: the async execution engine code path is exercised.
        elapsed = asyncio.get_event_loop().time() - start

        # Then: the expected `shutdown cancels after grace timeout` behavior is asserted.
        assert elapsed < 1.0
        assert engine.get_in_flight_count() == 0

    @pytest.mark.asyncio
    async def test_runtime_metrics_reports_async_worker_capacity_state(
        self, config: ExecutionConfig, mock_timeout_work_item: WorkItem
    ):
        # Given: inputs for `runtime metrics reports async worker capacity...` are prepared.
        # When: the async execution engine code path is exercised.
        engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)
        await engine.submit(mock_timeout_work_item)

        # Then: the expected `runtime metrics reports async worker capacity...` behavior is asserted.
        try:
            runtime_metrics = engine.get_runtime_metrics()

            assert runtime_metrics is not None
            assert runtime_metrics.engine_type == "async"
            assert isinstance(runtime_metrics.workers, EngineWorkerDiagnostics)
            assert runtime_metrics.workers.total == config.max_in_flight
            assert runtime_metrics.workers.executing == 1
            assert runtime_metrics.workers.admitted is None
            assert runtime_metrics.workers.top_k_loads == []
        finally:
            await engine.shutdown()


class TestAsyncExecutionEngineRetries:
    """Test retry logic with backoff in AsyncExecutionEngine"""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt_shows_attempt_1(self):
        """When worker succeeds immediately, CompletionEvent.attempt should be 1"""
        # Given: inputs for `success on first attempt shows attempt 1` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `success on first attempt shows attempt 1` behavior is asserted.
        assert len(events) == 1
        assert events[0].status == CompletionStatus.SUCCESS
        assert events[0].attempt == 1
        assert events[0].error is None

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_success_on_second_attempt_shows_attempt_2(self):
        """When worker fails once then succeeds, CompletionEvent.attempt should be 2"""
        # Given: inputs for `success on second attempt shows attempt 2` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `success on second attempt shows attempt 2` behavior is asserted.
        assert len(events) == 1
        assert events[0].status == CompletionStatus.SUCCESS
        assert events[0].attempt == 2
        assert events[0].error is None

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self):
        """When worker fails all retries, CompletionEvent.attempt should equal max_retries"""
        # Given: inputs for `failure after max retries` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `failure after max retries` behavior is asserted.
        assert len(events) == 1
        assert events[0].status == CompletionStatus.FAILURE
        assert events[0].attempt == 3
        assert events[0].error is not None
        assert "Permanent failure" in events[0].error

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_timeout_retried_and_counted(self):
        """Timeouts should be treated as failures and retried"""
        # Given: inputs for `timeout retried and counted` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `timeout retried and counted` behavior is asserted.
        assert len(events) == 1
        assert events[0].status == CompletionStatus.FAILURE
        assert events[0].attempt == 2
        assert events[0].error is not None
        assert "timed out" in events[0].error.lower()

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self):
        """Verify exponential backoff increases delay between retries"""
        # Given: inputs for `exponential backoff timing` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `exponential backoff timing` behavior is asserted.
        assert len(events) == 1

        elapsed = end_time - start_time
        expected_min_delay = 0.05 + 0.1
        assert elapsed >= expected_min_delay

        await engine.shutdown()

    @pytest.mark.asyncio
    async def test_backoff_respects_max_cap(self):
        """Verify backoff doesn't exceed max_retry_backoff_ms"""
        # Given: inputs for `backoff respects max cap` are prepared.
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

        # When: the async execution engine code path is exercised.
        events = await engine.poll_completed_events()
        # Then: the expected `backoff respects max cap` behavior is asserted.
        assert len(events) == 1
        assert events[0].attempt == 5

        elapsed = end_time - start_time
        min_expected = 0.1 + 0.15 + 0.15 + 0.15
        assert elapsed >= min_expected

        await engine.shutdown()


@pytest.mark.asyncio
async def test_get_min_inflight_offset_returns_none_for_async_engine():
    # Given: inputs for `get min inflight offset returns none for asyn...` are prepared.
    config = ExecutionConfig(
        mode=ExecutionMode.ASYNC,
        max_in_flight=1,
        async_config=AsyncConfig(task_timeout_ms=500),
    )
    engine = AsyncExecutionEngine(config=config, worker_fn=async_worker_fn)

    # When: the async execution engine code path is exercised.
    # Then: the expected `get min inflight offset returns none for asyn...` behavior is asserted.
    assert (
        engine.get_min_inflight_offset(TopicPartition(topic="test", partition=0))
        is None
    )

    await engine.shutdown()
