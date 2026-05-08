# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_completion_processing.py
# Role: Verifies completion processing, dirty partition tracking, DLQ-driven commits, and refill overlap.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_completion_driven_support import (
    AsyncMock,
    CompletionEvent,
    CompletionProcessingResult,
    CompletionStatus,
    DtoTopicPartition,
    MagicMock,
    OffsetTracker,
    _make_tracker,
    patch,
    pytest,
    time,
)


@pytest.mark.asyncio
async def test_partial_commit_retains_gap_completion_and_counts_next_completion(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for partial commit retains gap completion and counts next completion.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=10,
        max_revoke_grace_ms=1000,
    )
    tracker.mark_complete(10)
    tracker.mark_complete(12)
    tracker.commit_through(10)
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)
    broker_poller._unsettled_completions_by_partition[topic_partition] = 2
    broker_poller._execution_engine.get_runtime_metrics.return_value = None

    broker_poller._clear_committed_dirty_partitions([(topic_partition, 10)])

    # When: The control-plane behavior is exercised for partial commit retains gap completion and counts next completion.
    diagnostics_after_commit = broker_poller.get_pipeline_diagnostics()
    # Then: The expected partial commit retains gap completion and counts next completion behavior is asserted.
    assert diagnostics_after_commit.settlement.completed_unsettled == 1

    tracker.mark_complete(11)
    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(
            processed_count=1,
            completed_partitions=frozenset({topic_partition}),
            completed_counts_by_partition={topic_partition: 1},
            completed_offsets_by_partition={topic_partition: (11,)},
        )
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    await broker_poller._process_completed_events(
        [
            CompletionEvent(
                id="work-11",
                tp=topic_partition,
                offset=11,
                epoch=0,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            )
        ]
    )

    diagnostics_after_next_completion = broker_poller.get_pipeline_diagnostics()
    assert diagnostics_after_next_completion.settlement.completed_unsettled == 2


@pytest.mark.asyncio
async def test_process_completed_events_records_unsettled_completion_timestamps(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for process completed events records unsettled completion timestamps.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(
            processed_count=1,
            completed_partitions=frozenset({topic_partition}),
            completed_counts_by_partition={topic_partition: 1},
            completed_offsets_by_partition={topic_partition: (0,)},
        )
    )
    # When: The control-plane behavior is exercised for process completed events records unsettled completion timestamps.
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    with patch("pyrallel_consumer.control_plane.broker_poller.time.monotonic") as clock:
        clock.return_value = 123.0
        await broker_poller._process_completed_events([completion_event])

    # Then: The expected process completed events records unsettled completion timestamps behavior is asserted.
    assert broker_poller._unsettled_completion_timestamps_by_partition == {
        topic_partition: {0: 123.0}
    }


def test_clear_committed_dirty_partitions_observes_commit_latency_and_retains_gap(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for clear committed dirty partitions observes commit latency and retains gap.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=10,
        max_revoke_grace_ms=1000,
    )
    tracker.mark_complete(10)
    tracker.mark_complete(12)
    tracker.commit_through(10)
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)
    broker_poller._unsettled_completion_timestamps_by_partition[topic_partition] = {
        10: 100.0,
        12: 101.0,
    }
    broker_poller._metrics_exporter = MagicMock()

    with patch("pyrallel_consumer.control_plane.broker_poller.time.monotonic") as clock:
        clock.return_value = 105.0
        broker_poller._clear_committed_dirty_partitions([(topic_partition, 10)])

    # When: The control-plane behavior is exercised for clear committed dirty partitions observes commit latency and retains gap.
    broker_poller._metrics_exporter.observe_completion_to_commit_latency.assert_called_once_with(
        engine_type="async",
        duration_seconds=5.0,
    )
    # Then: The expected clear committed dirty partitions observes commit latency and retains gap behavior is asserted.
    assert broker_poller._unsettled_completion_timestamps_by_partition == {
        topic_partition: {12: 101.0}
    }


@pytest.mark.asyncio
async def test_commit_ready_offsets_does_not_observe_latency_when_commit_fails(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for commit ready offsets does not observe latency when commit fails.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._dirty_commit_partitions.add(topic_partition)
    broker_poller._unsettled_completion_timestamps_by_partition[topic_partition] = {
        0: 100.0
    }
    broker_poller._metrics_exporter = MagicMock()
    broker_poller._commit_offsets = AsyncMock(return_value=False)
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    await broker_poller._commit_ready_offsets(force=True)

    # When: The control-plane behavior is exercised for commit ready offsets does not observe latency when commit fails.
    broker_poller._metrics_exporter.observe_completion_to_commit_latency.assert_not_called()
    # Then: The expected commit ready offsets does not observe latency when commit fails behavior is asserted.
    assert broker_poller._unsettled_completion_timestamps_by_partition == {
        topic_partition: {0: 100.0}
    }


@pytest.mark.asyncio
async def test_commit_ready_offsets_force_flushes_dirty_partitions(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for commit ready offsets force flushes dirty partitions.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._commit_debounce_completion_threshold = 100
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._commit_offsets = AsyncMock()
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(1, frozenset({topic_partition}))
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    await broker_poller._process_completed_events([completion_event])
    # When: The control-plane behavior is exercised for commit ready offsets force flushes dirty partitions.
    await broker_poller._commit_ready_offsets(force=True)

    # Then: The expected commit ready offsets force flushes dirty partitions behavior is asserted.
    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 0)])


@pytest.mark.asyncio
async def test_process_completed_events_marks_only_managed_partitions_dirty(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for process completed events marks only managed partitions dirty.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    untracked_partition = DtoTopicPartition(topic="stale-topic", partition=1)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(1, frozenset({topic_partition}))
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    # When: The control-plane behavior is exercised for process completed events marks only managed partitions dirty.
    await broker_poller._process_completed_events(
        [
            completion_event,
            CompletionEvent(
                id="stale-work",
                tp=untracked_partition,
                offset=99,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
        ]
    )

    # Then: The expected process completed events marks only managed partitions dirty behavior is asserted.
    assert broker_poller._dirty_commit_partitions == {topic_partition}


@pytest.mark.asyncio
async def test_process_completed_events_unions_retry_and_completion_partitions(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for process completed events unions retry and completion partitions.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    retry_partition = DtoTopicPartition(topic="retry-topic", partition=2)
    broker_poller._offset_trackers[retry_partition] = _make_tracker(retry_partition)
    broker_poller._pending_dlq_events[(retry_partition, 7)] = completion_event

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(1, frozenset({topic_partition}))
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    # When: The control-plane behavior is exercised for process completed events unions retry and completion partitions.
    await broker_poller._process_completed_events([completion_event])

    # Then: The expected process completed events unions retry and completion partitions behavior is asserted.
    assert broker_poller._dirty_commit_partitions == {
        topic_partition,
        retry_partition,
    }


@pytest.mark.asyncio
async def test_process_completed_events_does_not_mark_everything_dirty_for_untracked_only(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for process completed events does not mark everything dirty for untracked only.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    untracked_partition = DtoTopicPartition(topic="stale-topic", partition=1)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(0, frozenset())
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    # When: The control-plane behavior is exercised for process completed events does not mark everything dirty for untracked only.
    await broker_poller._process_completed_events(
        [
            CompletionEvent(
                id="stale-work",
                tp=untracked_partition,
                offset=99,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            )
        ]
    )

    # Then: The expected process completed events does not mark everything dirty for untracked only behavior is asserted.
    assert broker_poller._dirty_commit_partitions == set()


@pytest.mark.asyncio
async def test_process_completed_events_dirties_only_accepted_mixed_batch_partitions(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for process completed events dirties only accepted mixed batch partitions.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    stale_partition = DtoTopicPartition(topic="stale-topic", partition=1)
    broker_poller._offset_trackers[stale_partition] = _make_tracker(stale_partition)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        return_value=CompletionProcessingResult(1, frozenset({topic_partition}))
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    # When: The control-plane behavior is exercised for process completed events dirties only accepted mixed batch partitions.
    await broker_poller._process_completed_events(
        [
            completion_event,
            CompletionEvent(
                id="stale-work",
                tp=stale_partition,
                offset=99,
                epoch=0,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
        ]
    )

    # Then: The expected process completed events dirties only accepted mixed batch partitions behavior is asserted.
    assert broker_poller._dirty_commit_partitions == {topic_partition}
    assert broker_poller._completions_since_last_commit == 1


@pytest.mark.asyncio
async def test_maybe_commit_ready_offsets_forces_commit_after_pending_dlq_activity(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for maybe commit ready offsets forces commit after pending dlq activity.
    broker_poller._commit_debounce_completion_threshold = 100
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._commit_ready_offsets = AsyncMock()

    # When: The control-plane behavior is exercised for maybe commit ready offsets forces commit after pending dlq activity.
    await broker_poller._maybe_commit_ready_offsets(had_pending_dlq_events=True)

    # Then: The expected maybe commit ready offsets forces commit after pending dlq activity behavior is asserted.
    broker_poller._commit_ready_offsets.assert_awaited_once_with(
        force=True, source="unknown"
    )


@pytest.mark.asyncio
async def test_completion_monitor_noops_when_wait_for_completion_times_out(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for completion monitor noops when wait for completion times out.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock()
    broker_poller._work_manager.get_total_in_flight_count.side_effect = [1, 0]
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()

    async def wait_for_completion(timeout_seconds=None):
        del timeout_seconds
        broker_poller._running = False
        return False

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )

    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    broker_poller._work_manager.poll_completed_events.assert_not_called()
    # When: The control-plane behavior is exercised for completion monitor noops when wait for completion times out.
    broker_poller._process_completed_events.assert_not_called()
    # Then: The expected completion monitor noops when wait for completion times out behavior is asserted.
    broker_poller._work_manager.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_drain_completion_events_once_processes_blocking_timeout_events(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for drain completion events once processes blocking timeout events.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[completion_event])
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._max_blocking_duration_ms = 1

    # When: The control-plane behavior is exercised for drain completion events once processes blocking timeout events.
    has_completion = await broker_poller._drain_completion_events_once()

    # Then: The expected drain completion events once processes blocking timeout events behavior is asserted.
    assert has_completion is True
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])


@pytest.mark.asyncio
async def test_drain_completion_events_once_preserves_work_manager_refill_overlap(
    broker_poller, completion_event
):
    # Given: Inputs and test doubles are prepared for drain completion events once preserves work manager refill overlap.
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        return_value=[completion_event]
    )
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._process_completed_events = AsyncMock()

    # When: The control-plane behavior is exercised for drain completion events once preserves work manager refill overlap.
    has_completion = await broker_poller._drain_completion_events_once()

    # Then: The expected drain completion events once preserves work manager refill overlap behavior is asserted.
    assert has_completion is True
    broker_poller._work_manager.poll_completed_events.assert_awaited_once_with()
    broker_poller._work_manager.schedule.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_completion_monitor_submits_next_same_key_work_without_new_consume(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for completion monitor submits next same key work without new consume.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager.on_assign({topic_partition: tracker})
    broker_poller._work_manager._blocking_cache_ttl = 0
    broker_poller._work_manager._blocking_cache_counter = 0
    broker_poller._max_blocking_duration_ms = 0

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=1,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-1",
    )
    # When: The control-plane behavior is exercised for completion monitor submits next same key work without new consume.
    await broker_poller._work_manager.schedule()

    # Then: The expected completion monitor submits next same key work without new consume behavior is asserted.
    assert broker_poller._execution_engine.submit.await_count == 1
    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]

    broker_poller._execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=first_item.id,
            tp=topic_partition,
            offset=first_item.offset,
            epoch=tracker.get_current_epoch(),
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

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

    assert broker_poller._execution_engine.submit.await_count == 2
    second_item = broker_poller._execution_engine.submit.await_args_list[1].args[0]
    assert second_item.offset == 1
    assert second_item.key == b"key-A"
