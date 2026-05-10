# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_run_loop_shutdown.py
# Role: Verifies BrokerPoller run-loop failure handling and task shutdown lifecycle.
# Extend here for focused control-plane regression coverage in this area.

from pyrallel_consumer.dto import ExecutionControlEvent
from tests.unit.control_plane._broker_poller_support import (
    AsyncMock,
    BrokerPoller,
    DtoTopicPartition,
    KafkaException,
    MagicMock,
    OffsetTracker,
    _make_message,
    asyncio,
    patch,
    pytest,
)


class TestRunConsumerCommitExceptionDefense:
    """_run_consumer commit failure must not kill the consumer loop."""

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_kill_consumer_loop(self, broker_poller):
        """When commit raises KafkaException, the loop should continue, not terminate."""
        # Given: Inputs and test doubles are prepared for commit failure does not kill consumer loop.
        tp = DtoTopicPartition(topic="test-topic", partition=0)
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.last_committed_offset = -1
        tracker.last_fetched_offset = 2
        tracker.mark_complete(0)
        tracker.mark_complete(1)
        tracker.mark_complete(2)
        broker_poller._offset_trackers[tp] = tracker

        # Consumer.consume returns empty after first iteration to let commit path run
        iteration = 0

        def fake_consume(num_messages=1, timeout=0.1):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                broker_poller._running = False
            return []

        broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

        # Commit raises on first call, succeeds on second
        commit_calls = 0

        def commit_side_effect(offsets=None, asynchronous=False):
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise KafkaException("Broker unavailable")
            return None

        broker_poller.consumer.commit = MagicMock(side_effect=commit_side_effect)

        # Patch asyncio.to_thread to call functions directly
        async def passthrough_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        broker_poller._running = True
        broker_poller.MAX_IN_FLIGHT_MESSAGES = 1000
        broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
        broker_poller.QUEUE_MAX_MESSAGES = 0
        broker_poller._max_blocking_duration_ms = 0
        broker_poller.producer = MagicMock()  # sync mock for _cleanup flush
        broker_poller._work_manager = MagicMock()
        broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
        broker_poller._work_manager.get_total_in_flight_count.return_value = 0
        broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
        # When: The control-plane behavior is exercised for commit failure does not kill consumer loop.
        broker_poller._work_manager.schedule = AsyncMock()
        with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
            await broker_poller._run_consumer()

        # Loop must have continued past the commit failure (iteration >= 3)
        # Then: The expected commit failure does not kill consumer loop behavior is asserted.
        assert (
            iteration >= 3
        ), f"Consumer loop died after {iteration} iterations — commit failure killed the loop"

    @pytest.mark.asyncio
    async def test_commit_failure_retries_once_then_succeeds(self, broker_poller):
        """Transient commit failure should be retried once, then succeed."""
        # Given: Inputs and test doubles are prepared for commit failure retries once then succeeds.
        tp = DtoTopicPartition(topic="test-topic", partition=0)
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.last_committed_offset = -1
        tracker.last_fetched_offset = 0
        tracker.mark_complete(0)
        broker_poller._offset_trackers[tp] = tracker
        broker_poller._dirty_commit_partitions.add(tp)
        broker_poller._commit_debounce_completion_threshold = 1
        broker_poller._completions_since_last_commit = 1

        iteration = 0

        def fake_consume(num_messages=1, timeout=0.1):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                broker_poller._running = False
            return []

        broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

        commit_calls = 0

        def commit_side_effect(offsets=None, asynchronous=False):
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise KafkaException("Transient error")
            return None

        broker_poller.consumer.commit = MagicMock(side_effect=commit_side_effect)

        async def passthrough_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        broker_poller._running = True
        broker_poller.MAX_IN_FLIGHT_MESSAGES = 1000
        broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
        broker_poller.QUEUE_MAX_MESSAGES = 0
        broker_poller._max_blocking_duration_ms = 0
        broker_poller.producer = MagicMock()  # sync mock for _cleanup flush
        broker_poller._work_manager = MagicMock()
        broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
        broker_poller._work_manager.get_total_in_flight_count.return_value = 0
        broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
        # When: The control-plane behavior is exercised for commit failure retries once then succeeds.
        broker_poller._work_manager.schedule = AsyncMock()
        with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
            await broker_poller._run_consumer()

        # commit should have been called twice (1 failure + 1 retry success)
        # Then: The expected commit failure retries once then succeeds behavior is asserted.
        assert commit_calls == 2
        # After successful retry, advance_high_water_mark should have been called
        assert tracker.last_committed_offset == 0


@pytest.mark.asyncio
async def test_stop_reraises_terminal_consumer_loop_error(broker_poller, mock_consumer):
    # Given: Inputs and test doubles are prepared for stop reraises terminal consumer loop error.
    broker_poller.consumer = mock_consumer
    broker_poller.producer = None
    broker_poller._running = True
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 70
    broker_poller.QUEUE_MAX_MESSAGES = 0
    # When: The control-plane behavior is exercised for stop reraises terminal consumer loop error.
    mock_consumer.consume.side_effect = RuntimeError("boom")

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", new=passthrough_to_thread):
        broker_poller._consumer_task = asyncio.create_task(
            broker_poller._run_consumer()
        )
        await asyncio.sleep(0)

        # Then: The expected stop reraises terminal consumer loop error behavior is asserted.
        with pytest.raises(RuntimeError, match="boom"):
            await broker_poller.stop()

    assert broker_poller._pipeline_poll_error_total == 1


@pytest.mark.asyncio
async def test_consumer_loop_downstream_exception_does_not_count_as_poll_error(
    broker_poller,
    mock_consumer,
):
    # Given: Inputs and test doubles are prepared for consumer loop downstream exception does not count as poll error.
    message = _make_message("test-topic", 0, 1, b"key", b"value")
    mock_consumer.consume.return_value = [message]
    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._running = True
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 70
    broker_poller.QUEUE_MAX_MESSAGES = 0
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    dispatch_support = MagicMock()
    dispatch_support.dispatch_messages = AsyncMock(
        side_effect=RuntimeError("dispatch boom")
    )
    # When: The control-plane behavior is exercised for consumer loop downstream exception does not count as poll error.
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", new=passthrough_to_thread):
        await broker_poller._run_consumer()

    # Then: The expected consumer loop downstream exception does not count as poll error behavior is asserted.
    assert isinstance(broker_poller._fatal_error, RuntimeError)
    assert broker_poller._pipeline_poll_records_total == 1
    assert broker_poller._pipeline_poll_error_total == 0


@pytest.mark.asyncio
async def test_drain_execution_control_events_cancels_engine_batches_with_fatal_reason(
    broker_poller,
) -> None:
    # Given: the execution engine reports a fatal control event.
    broker_poller._execution_engine.poll_control_events = AsyncMock(
        return_value=[
            ExecutionControlEvent(
                kind="fatal",
                error=RuntimeError("boom"),
                code="invalid_batch_worker_result",
                reason="invalid_batch_worker_result:missing_item_ids",
                failure_class="BATCH_WORKER_CONTRACT_ERROR",
                committable=False,
                batch_id="batch-live",
                worker_generation=0,
                item_ids=("a", "b"),
                item_count=2,
                epoch=1,
                attempt=1,
            )
        ]
    )
    broker_poller._execution_engine.cancel_batch_items = MagicMock()

    with pytest.raises(RuntimeError, match="boom"):
        await broker_poller._drain_execution_control_events_once()

    # Then: broker fatal handling tombstones active engine-owned batch work first.
    broker_poller._execution_engine.cancel_batch_items.assert_called_once()
    scope = broker_poller._execution_engine.cancel_batch_items.call_args.args[0]
    assert scope.reason == "fatal"


@pytest.mark.asyncio
async def test_drain_execution_control_events_ignores_inactive_batch_fatal(
    broker_poller,
) -> None:
    # Given: a stale process fatal references a batch no longer active in the engine.
    broker_poller._execution_engine.poll_control_events = AsyncMock(
        return_value=[
            ExecutionControlEvent(
                kind="fatal",
                error=RuntimeError("stale fatal"),
                code="invalid_batch_worker_result",
                reason="invalid_batch_worker_result:missing_item_ids",
                failure_class="BATCH_WORKER_CONTRACT_ERROR",
                committable=False,
                batch_id="batch-stale",
                worker_generation=0,
                item_ids=("a",),
                item_count=1,
                epoch=1,
                attempt=1,
            )
        ]
    )
    broker_poller._execution_engine._active_process_batch_ids = {"batch-live"}
    broker_poller._execution_engine.cancel_batch_items = MagicMock()
    broker_poller._running = True

    drained = await broker_poller._drain_execution_control_events_once()

    # Then: inactive/stale fatal controls are quarantined and do not kill the consumer.
    assert drained is True
    assert broker_poller._fatal_error is None
    assert broker_poller._running is not False
    broker_poller._execution_engine.cancel_batch_items.assert_not_called()


@pytest.mark.asyncio
async def test_cleanup_cancels_engine_batches_with_shutdown_reason(
    broker_poller,
) -> None:
    # Given: broker cleanup is closing runtime after shutdown starts.
    broker_poller.producer = None
    broker_poller._commit_coordinator = MagicMock()
    broker_poller._drain_commit_coordinator_for_shutdown = AsyncMock(return_value=True)
    broker_poller._execution_engine.cancel_batch_items = MagicMock()

    await BrokerPoller._cleanup(broker_poller)

    # Then: shutdown cleanup tombstones active engine-owned batch work before close.
    broker_poller._execution_engine.cancel_batch_items.assert_called_once()
    scope = broker_poller._execution_engine.cancel_batch_items.call_args.args[0]
    assert scope.reason == "shutdown"


@pytest.mark.asyncio
async def test_start_skips_completion_monitor_when_disabled(
    mock_kafka_config, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for start skips completion monitor when disabled.
    mock_kafka_config.parallel_consumer.strict_completion_monitor_enabled = False
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    created_tasks = []

    def fake_create_task(coro, name=None):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        created_tasks.append((name, task))
        return task

    # When: The control-plane behavior is exercised for start skips completion monitor when disabled.
    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=MagicMock(),
        ) as mock_producer,
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=MagicMock(),
        ) as mock_admin,
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=MagicMock(),
        ) as mock_consumer_ctor,
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    # Then: The expected start skips completion monitor when disabled behavior is asserted.
    assert broker_poller._running is True
    assert broker_poller._completion_monitor_task is None
    assert broker_poller._consumer_task is created_tasks[0][1]
    assert created_tasks == [("broker-poller-loop", created_tasks[0][1])]
    mock_producer.assert_called_once()
    mock_admin.assert_called_once_with({"bootstrap.servers": "broker:9092"})
    mock_consumer_ctor.assert_called_once()


@pytest.mark.asyncio
async def test_stop_cancels_consumer_task_after_timeout(broker_poller):
    # Given: Inputs and test doubles are prepared for stop cancels consumer task after timeout.
    timed_out_task = MagicMock()
    timed_out_task.cancel = MagicMock()
    broker_poller._running = True
    broker_poller._consumer_task = timed_out_task
    broker_poller._shutdown_event.set()

    with (
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        patch("asyncio.gather", new=AsyncMock()) as gather_mock,
    ):
        await broker_poller.stop()

    timed_out_task.cancel.assert_called_once_with()
    # When: The control-plane behavior is exercised for stop cancels consumer task after timeout.
    gather_mock.assert_awaited_once_with(timed_out_task, return_exceptions=True)
    # Then: The expected stop cancels consumer task after timeout behavior is asserted.
    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_wait_closed_reraises_terminal_error_when_shutdown_is_complete(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for wait closed reraises terminal error when shutdown is complete.
    broker_poller._running = False
    broker_poller._consumer_task = None
    broker_poller._shutdown_event.set()
    # When: The control-plane behavior is exercised for wait closed reraises terminal error when shutdown is complete.
    broker_poller._fatal_error = RuntimeError("closed-boom")

    # Then: The expected wait closed reraises terminal error when shutdown is complete behavior is asserted.
    with pytest.raises(RuntimeError, match="closed-boom"):
        await broker_poller.wait_closed()


@pytest.mark.asyncio
async def test_run_consumer_keeps_consumer_task_until_cleanup_finishes(
    broker_poller, mock_consumer
):
    # Given: Inputs and test doubles are prepared for run consumer keeps consumer task until cleanup finishes.
    cleanup_started = asyncio.Event()
    allow_cleanup_finish = asyncio.Event()

    async def fake_cleanup():
        cleanup_started.set()
        await allow_cleanup_finish.wait()

    def fake_consume(num_messages=1, timeout=0.1):
        broker_poller._running = False
        return []

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._max_blocking_duration_ms = 0
    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)
    # When: The control-plane behavior is exercised for run consumer keeps consumer task until cleanup finishes.
    broker_poller._cleanup = AsyncMock(side_effect=fake_cleanup)
    broker_poller._running = True

    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        consumer_task = asyncio.create_task(broker_poller._run_consumer())
        broker_poller._consumer_task = consumer_task
        await cleanup_started.wait()
        # Then: The expected run consumer keeps consumer task until cleanup finishes behavior is asserted.
        assert broker_poller._consumer_task is consumer_task
        assert not broker_poller._shutdown_event.is_set()
        allow_cleanup_finish.set()
        await consumer_task

    assert broker_poller._shutdown_event.is_set()
    assert broker_poller._consumer_task is None
