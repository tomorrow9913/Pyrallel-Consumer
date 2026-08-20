# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_completion_scheduling.py
# Role: Verifies completion-driven scheduling, completion monitor cadence, and consumer-loop refill behavior.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_completion_driven_support import (
    AsyncMock,
    MagicMock,
    _make_tracker,
    _run_consume_loop_once_then_stop,
    call,
    patch,
    pytest,
    time,
)


@pytest.mark.asyncio
async def test_run_consumer_schedules_after_completion_even_without_new_messages(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for run consumer schedules after completion even without new messages.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    _run_consume_loop_once_then_stop(broker_poller, [])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with (
        patch("asyncio.to_thread", side_effect=passthrough_to_thread),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await broker_poller._run_consumer()

    # When: The control-plane behavior is exercised for run consumer schedules after completion even without new messages.
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    # Then: The expected run consumer schedules after completion even without new messages behavior is asserted.
    assert broker_poller._work_manager.schedule.await_count == 1


@pytest.mark.asyncio
async def test_run_consumer_schedules_twice_when_messages_and_completions_share_iteration(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for run consumer schedules twice when messages and completions share iteration.
    tracker = _make_tracker(topic_partition)
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.submit_message = AsyncMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()

    message = MagicMock()
    message.error.return_value = None
    message.topic.return_value = topic_partition.topic
    message.partition.return_value = topic_partition.partition
    message.offset.return_value = 0
    message.key.return_value = b"key-A"
    message.value.return_value = b"payload-0"
    _run_consume_loop_once_then_stop(broker_poller, [message])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with (
        patch("asyncio.to_thread", side_effect=passthrough_to_thread),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await broker_poller._run_consumer()

    # When: The control-plane behavior is exercised for run consumer schedules twice when messages and completions share iteration.
    broker_poller._work_manager.submit_message.assert_awaited_once_with(
        tp=topic_partition,
        offset=0,
        epoch=1,
        key=b"key-A",
        payload=b"payload-0",
    )
    # Then: The expected run consumer schedules twice when messages and completions share iteration behavior is asserted.
    assert broker_poller._work_manager.schedule.await_count == 2
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    assert broker_poller._work_manager.schedule.await_args_list == [call(), call()]


@pytest.mark.asyncio
async def test_run_consumer_falls_back_without_duplicate_enqueue_for_sync_batch_submit(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for run consumer falls back without duplicate enqueue for sync batch submit.
    tracker = _make_tracker(topic_partition)
    broker_poller._offset_trackers[topic_partition] = tracker

    class _SyncBatchWorkManager:
        def __init__(self) -> None:
            self.batch_calls = 0
            self.submit_message = AsyncMock()
            self.poll_completed_events = AsyncMock(side_effect=[[], []])
            self.schedule = AsyncMock()
            self.get_total_in_flight_count = MagicMock(return_value=0)
            self.get_virtual_queue_sizes = MagicMock(return_value={})

        def submit_message_batch(self, _grouped_messages) -> None:
            self.batch_calls += 1
            return None

    broker_poller._work_manager = _SyncBatchWorkManager()
    broker_poller._process_completed_events = AsyncMock()

    message = MagicMock()
    message.error.return_value = None
    message.topic.return_value = topic_partition.topic
    message.partition.return_value = topic_partition.partition
    message.offset.return_value = 0
    message.key.return_value = b"key-A"
    message.value.return_value = b"payload-0"
    _run_consume_loop_once_then_stop(broker_poller, [message])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    # When: The control-plane behavior is exercised for run consumer falls back without duplicate enqueue for sync batch submit.
    with (
        patch("asyncio.to_thread", side_effect=passthrough_to_thread),
        patch("asyncio.sleep", new=AsyncMock()),
    ):
        await broker_poller._run_consumer()

    # Then: The expected run consumer falls back without duplicate enqueue for sync batch submit behavior is asserted.
    assert broker_poller._work_manager.batch_calls == 0
    broker_poller._work_manager.submit_message.assert_awaited_once_with(
        tp=topic_partition,
        offset=0,
        epoch=1,
        key=b"key-A",
        payload=b"payload-0",
    )


@pytest.mark.asyncio
async def test_completion_monitor_reschedules_without_waiting_for_consumer_loop(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for completion monitor reschedules without waiting for consumer loop.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.side_effect = [1, 0]
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()
    broker_poller._commit_ready_offsets = AsyncMock()
    broker_poller._dirty_commit_partitions.add(topic_partition)

    async def wait_for_completion(timeout_seconds=None):
        broker_poller._running = False
        return True

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )

    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    broker_poller._execution_engine.wait_for_completion.assert_awaited_once()
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    # When: The control-plane behavior is exercised for completion monitor reschedules without waiting for consumer loop.
    broker_poller._work_manager.schedule.assert_awaited_once_with()
    # Then: The expected completion monitor reschedules without waiting for consumer loop behavior is asserted.
    broker_poller._commit_ready_offsets.assert_awaited_once_with(
        force=True,
        source="completion_monitor",
    )


@pytest.mark.asyncio
async def test_completion_monitor_sets_fatal_error_and_stops_on_exception(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for completion monitor sets fatal error and stops on exception.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 1
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._pending_dlq_events.clear()
    # When: The control-plane behavior is exercised for completion monitor sets fatal error and stops on exception.
    broker_poller._execution_engine = AsyncMock()

    async def boom(timeout_seconds=None):
        del timeout_seconds
        raise RuntimeError("monitor failed")

    broker_poller._execution_engine.wait_for_completion.side_effect = boom
    broker_poller._running = True

    # Then: The expected completion monitor sets fatal error and stops on exception behavior is asserted.
    with pytest.raises(RuntimeError, match="monitor failed"):
        await broker_poller._run_completion_monitor()

    assert isinstance(broker_poller._fatal_error, RuntimeError)
    assert broker_poller._running is False


@pytest.mark.asyncio
async def test_completion_monitor_skips_commit_call_until_debounce_cadence(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for completion monitor skips commit call until debounce cadence.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._commit_debounce_completion_threshold = 100
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 1
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock(
        side_effect=lambda events: broker_poller._dirty_commit_partitions.update(
            event.tp for event in events
        )
    )
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()
    # When: The control-plane behavior is exercised for completion monitor skips commit call until debounce cadence.
    broker_poller._commit_ready_offsets = AsyncMock()

    async def wait_for_completion(timeout_seconds=None):
        del timeout_seconds
        broker_poller._running = False
        return True

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )

    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    # Then: The expected completion monitor skips commit call until debounce cadence behavior is asserted.
    broker_poller._commit_ready_offsets.assert_not_awaited()
