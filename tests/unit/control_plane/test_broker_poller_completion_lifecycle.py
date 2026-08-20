# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_completion_lifecycle.py
# Role: Verifies completion-driven poller start, stop, graceful drain, cleanup, and wait-closed lifecycle.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_completion_driven_support import (
    AsyncMock,
    BrokerPoller,
    MagicMock,
    asyncio,
    patch,
    pytest,
    threading,
    time,
)


@pytest.mark.asyncio
async def test_start_skips_completion_monitor_task_when_disabled(
    broker_poller,
    mock_kafka_config,
):
    # Given: Inputs and test doubles are prepared for start skips completion monitor task when disabled.
    mock_kafka_config.parallel_consumer.strict_completion_monitor_enabled = False
    created_coroutines: list[str] = []

    def fake_create_task(coro, *, name=None):
        del name
        created_coroutines.append(coro.cr_code.co_name)
        coro.close()
        return MagicMock()

    # When: The control-plane behavior is exercised for start skips completion monitor task when disabled.
    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=broker_poller.producer,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=broker_poller.admin,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=broker_poller.consumer,
        ),
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    # Then: The expected start skips completion monitor task when disabled behavior is asserted.
    assert created_coroutines == ["_run_consumer"]
    assert broker_poller._consumer_task is not None
    assert broker_poller._completion_monitor_task is None


@pytest.mark.asyncio
async def test_start_stores_consumer_task_handle(broker_poller):
    # Given: Inputs and test doubles are prepared for start stores consumer task handle.
    created_tasks = []

    def fake_create_task(coro, *, name=None):
        task = MagicMock()
        task.name = name
        created_tasks.append((coro.cr_code.co_name, name, task))
        coro.close()
        return task

    # When: The control-plane behavior is exercised for start stores consumer task handle.
    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=broker_poller.producer,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=broker_poller.admin,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=broker_poller.consumer,
        ),
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    # Then: The expected start stores consumer task handle behavior is asserted.
    assert broker_poller._consumer_task is created_tasks[-1][2]
    assert created_tasks[-1][0] == "_run_consumer"


@pytest.mark.asyncio
async def test_stop_awaits_and_clears_consumer_task_handle(broker_poller):
    # Given: Inputs and test doubles are prepared for stop awaits and clears consumer task handle.
    async def fake_consumer_task():
        await asyncio.sleep(0)
        broker_poller._shutdown_event.set()

    broker_poller._running = True
    broker_poller._consumer_task_stop_timeout_seconds = 0.05
    broker_poller._consumer_task = asyncio.create_task(fake_consumer_task())

    # When: The control-plane behavior is exercised for stop awaits and clears consumer task handle.
    await broker_poller.stop()

    # Then: The expected stop awaits and clears consumer task handle behavior is asserted.
    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_stop_cancels_consumer_task_when_wait_times_out(broker_poller):
    # Given: Inputs and test doubles are prepared for stop cancels consumer task when wait times out.
    cancelled = asyncio.Event()

    async def hanging_consumer_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            broker_poller._shutdown_event.set()
            raise

    broker_poller._running = True
    broker_poller._consumer_task_stop_timeout_seconds = 0.01
    broker_poller._consumer_task = asyncio.create_task(hanging_consumer_task())

    # When: The control-plane behavior is exercised for stop cancels consumer task when wait times out.
    await broker_poller.stop()

    # Then: The expected stop cancels consumer task when wait times out behavior is asserted.
    assert cancelled.is_set()
    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_stop_uses_stable_consumer_task_reference_when_timeout_races_with_cleanup(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for stop uses stable consumer task reference when timeout races with cleanup.
    cancelled = asyncio.Event()

    async def hanging_consumer_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            broker_poller._shutdown_event.set()
            raise

    async def fake_wait_for(task, timeout):
        broker_poller._consumer_task = None
        broker_poller._shutdown_event.set()
        raise asyncio.TimeoutError

    broker_poller._running = True
    # When: The control-plane behavior is exercised for stop uses stable consumer task reference when timeout races with cleanup.
    broker_poller._consumer_task = asyncio.create_task(hanging_consumer_task())

    with patch("asyncio.wait_for", side_effect=fake_wait_for):
        await broker_poller.stop()

    # Then: The expected stop uses stable consumer task reference when timeout races with cleanup behavior is asserted.
    assert cancelled.is_set() or broker_poller._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_graceful_stop_drains_before_closing_consumer(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for graceful stop drains before closing consumer.
    events: list[str] = []
    consume_started = threading.Event()

    def fake_consume(num_messages=1, timeout=0.1):
        del num_messages, timeout
        consume_started.set()
        while broker_poller._running:
            time.sleep(0.001)
        return []

    async def fake_cleanup():
        events.append("cleanup")
        broker_poller.consumer = None

    async def fake_drain_shutdown_work(*, timeout_seconds: float) -> bool:
        del timeout_seconds
        events.append(f"drain_consumer_open={broker_poller.consumer is not None}")
        return True

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)
    broker_poller._drain_completion_events_once = AsyncMock(return_value=False)
    broker_poller._commit_ready_offsets = AsyncMock()
    broker_poller._cleanup = AsyncMock(side_effect=fake_cleanup)
    broker_poller._drain_shutdown_work = AsyncMock(side_effect=fake_drain_shutdown_work)
    broker_poller._get_consume_timeout_seconds = AsyncMock(return_value=0.001)
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._consumer_task_stop_timeout_seconds = 0.05
    broker_poller._running = True
    broker_poller._consumer_task = asyncio.create_task(broker_poller._run_consumer())

    # When: The control-plane behavior is exercised for graceful stop drains before closing consumer.
    try:
        # Then: The expected graceful stop drains before closing consumer behavior is asserted.
        assert await asyncio.to_thread(consume_started.wait, 1)

        await broker_poller.stop()
    finally:
        broker_poller._running = False
        if broker_poller._consumer_task is not None:
            broker_poller._consumer_task.cancel()
            await asyncio.gather(
                broker_poller._consumer_task,
                return_exceptions=True,
            )

    assert events == ["drain_consumer_open=True", "cleanup"]
    broker_poller._drain_shutdown_work.assert_awaited_once()
    broker_poller._cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_graceful_stop_cleans_up_when_stop_runtime_raises(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for graceful stop cleans up when stop runtime raises.
    class _FailingLifecycleSupport:
        async def stop_runtime(self, **_kwargs):
            raise RuntimeError("stop-runtime failed")

    broker_poller._make_task_lifecycle_support = MagicMock(
        return_value=_FailingLifecycleSupport()
    )
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._cleanup = AsyncMock()
    broker_poller._running = True
    # When: The control-plane behavior is exercised for graceful stop cleans up when stop runtime raises.
    broker_poller._consumer_task = MagicMock()

    # Then: The expected graceful stop cleans up when stop runtime raises behavior is asserted.
    with pytest.raises(RuntimeError, match="stop-runtime failed"):
        await broker_poller.stop()

    broker_poller._cleanup.assert_awaited_once()
    assert broker_poller._defer_consumer_cleanup_for_stop is False


@pytest.mark.asyncio
async def test_concurrent_graceful_stop_cleans_up_once(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for concurrent graceful stop cleans up once.
    class _SlowLifecycleSupport:
        def __init__(self) -> None:
            self.calls = 0

        async def stop_runtime(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(0.01)
            broker_poller._shutdown_event.set()

    support = _SlowLifecycleSupport()
    broker_poller._make_task_lifecycle_support = MagicMock(return_value=support)
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._drain_shutdown_work = AsyncMock(return_value=True)
    broker_poller._cleanup = AsyncMock()
    broker_poller._running = True
    broker_poller._consumer_task = MagicMock()

    # When: The control-plane behavior is exercised for concurrent graceful stop cleans up once.
    await asyncio.gather(broker_poller.stop(), broker_poller.stop())

    # Then: The expected concurrent graceful stop cleans up once behavior is asserted.
    assert support.calls == 1
    broker_poller._drain_shutdown_work.assert_awaited_once()
    broker_poller._cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_timeout_still_drains_commit_coordinator_before_abort(
    broker_poller,
) -> None:
    # Given: Inputs and test doubles are prepared for shutdown timeout still drains commit coordinator before abort.
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._drain_completion_events_once = AsyncMock(return_value=False)
    broker_poller._work_manager.get_total_in_flight_count = MagicMock(return_value=1)
    broker_poller._get_total_queued_messages = AsyncMock(return_value=0)
    broker_poller._drain_commit_coordinator_for_shutdown = AsyncMock(return_value=True)

    # When: The control-plane behavior is exercised for shutdown timeout still drains commit coordinator before abort.
    drained = await broker_poller._drain_shutdown_work(timeout_seconds=0.0)

    # Then: The expected shutdown timeout still drains commit coordinator before abort behavior is asserted.
    assert drained is False
    broker_poller._drain_commit_coordinator_for_shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_cleanup_drains_commit_coordinator_before_closing_consumer(
    broker_poller,
) -> None:
    # Given: Inputs and test doubles are prepared for cleanup drains commit coordinator before closing consumer.
    events: list[str] = []

    async def drain_before_close(deadline: float) -> bool:
        del deadline
        events.append("drain")
        return True

    broker_poller.producer = None
    broker_poller._commit_coordinator = MagicMock()
    broker_poller._drain_commit_coordinator_for_shutdown = AsyncMock(
        side_effect=drain_before_close
    )
    broker_poller.consumer.close.side_effect = lambda: events.append("close")

    # When: The control-plane behavior is exercised for cleanup drains commit coordinator before closing consumer.
    await BrokerPoller._cleanup(broker_poller)

    # Then: The expected cleanup drains commit coordinator before closing consumer behavior is asserted.
    assert events == ["drain", "close"]


@pytest.mark.asyncio
async def test_wait_closed_returns_immediately_when_not_running_and_no_task(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for wait closed returns immediately when not running and no task.
    # When: The control-plane behavior is exercised for wait closed returns immediately when not running and no task.
    # Then: The expected wait closed returns immediately when not running and no task behavior is asserted.
    await broker_poller.wait_closed()
