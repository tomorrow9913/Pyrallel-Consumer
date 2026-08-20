# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_completion_stale.py
# Role: Verifies duplicate, stale, and shutdown-preserved completion handling across reassignment.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_completion_driven_support import (
    AsyncMock,
    CompletionEvent,
    CompletionStatus,
    KafkaTopicPartition,
    OffsetTracker,
    pytest,
)


@pytest.mark.asyncio
async def test_duplicate_completion_does_not_overcount_unsettled_diagnostics(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for duplicate completion does not overcount unsettled diagnostics.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker

    completion = CompletionEvent(
        id="work-0",
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )

    await broker_poller._process_completed_events([completion])
    # When: The control-plane behavior is exercised for duplicate completion does not overcount unsettled diagnostics.
    await broker_poller._process_completed_events([completion])

    # Then: The expected duplicate completion does not overcount unsettled diagnostics behavior is asserted.
    assert tracker.completed_offsets == {0}
    assert broker_poller._unsettled_completions_by_partition[topic_partition] == 1
    assert broker_poller._completions_since_last_commit == 1


@pytest.mark.asyncio
async def test_stale_completion_does_not_resubmit_next_same_key_work(
    broker_poller, topic_partition, caplog
):
    # Given: Inputs and test doubles are prepared for stale completion does not resubmit next same key work.
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
    # When: The control-plane behavior is exercised for stale completion does not resubmit next same key work.
    await broker_poller._work_manager.schedule()

    # Then: The expected stale completion does not resubmit next same key work behavior is asserted.
    assert broker_poller._execution_engine.submit.await_count == 1
    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]

    stale_completion = CompletionEvent(
        id=first_item.id,
        tp=topic_partition,
        offset=first_item.offset,
        epoch=tracker.get_current_epoch() - 1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    broker_poller._execution_engine.poll_completed_events.return_value = [
        stale_completion
    ]

    with caplog.at_level("WARNING"):
        completed_events = await broker_poller._work_manager.poll_completed_events()
        await broker_poller._process_completed_events(completed_events)

    assert broker_poller._execution_engine.submit.await_count == 1
    assert "Discarding zombie completion" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_preserved_stale_success_releases_state_without_dirty_commit(
    broker_poller, topic_partition, caplog
):
    # Given: Inputs and test doubles are prepared for shutdown preserved stale success releases state without dirty commit.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager.on_assign({topic_partition: tracker})

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.schedule()

    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]
    tracker.increment_epoch()
    stale_completion = CompletionEvent(
        id=first_item.id,
        tp=topic_partition,
        offset=first_item.offset,
        epoch=first_item.epoch,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    broker_poller._execution_engine.poll_completed_events.return_value = [
        stale_completion
    ]

    # When: The control-plane behavior is exercised for shutdown preserved stale success releases state without dirty commit.
    with caplog.at_level("WARNING"):
        completed_events = await broker_poller._work_manager.poll_completed_events()
        await broker_poller._process_completed_events(completed_events)

    # Then: The expected shutdown preserved stale success releases state without dirty commit behavior is asserted.
    assert first_item.id not in broker_poller._work_manager._in_flight_work_items
    assert first_item.id not in broker_poller._work_manager._dispatch_timestamps
    assert broker_poller._work_manager.get_total_in_flight_count() == 0
    assert first_item.offset not in tracker.completed_offsets
    assert tracker.last_committed_offset == -1
    assert broker_poller._dirty_commit_partitions == set()
    assert broker_poller._completions_since_last_commit == 0
    assert "Discarding zombie completion" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_preserved_stale_failure_skips_dlq_commit_and_cache_cleanup(
    broker_poller, topic_partition, caplog
):
    # Given: Inputs and test doubles are prepared for shutdown preserved stale failure skips dlq commit and cache cleanup.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager.on_assign({topic_partition: tracker})
    broker_poller._kafka_config.dlq_enabled = True
    broker_poller._kafka_config.parallel_consumer.execution.max_retries = 1
    broker_poller._publish_to_dlq = AsyncMock(return_value=True)

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.schedule()

    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]
    broker_poller._message_cache[(topic_partition, first_item.offset)] = (
        b"key-A",
        b"payload-0",
    )
    tracker.increment_epoch()
    stale_completion = CompletionEvent(
        id=first_item.id,
        tp=topic_partition,
        offset=first_item.offset,
        epoch=first_item.epoch,
        status=CompletionStatus.FAILURE,
        error="worker failed before shutdown boundary",
        attempt=1,
    )
    broker_poller._execution_engine.poll_completed_events.return_value = [
        stale_completion
    ]

    # When: The control-plane behavior is exercised for shutdown preserved stale failure skips dlq commit and cache cleanup.
    with caplog.at_level("WARNING"):
        completed_events = await broker_poller._work_manager.poll_completed_events()
        await broker_poller._process_completed_events(completed_events)

    # Then: The expected shutdown preserved stale failure skips dlq commit and cache cleanup behavior is asserted.
    assert first_item.id not in broker_poller._work_manager._in_flight_work_items
    assert first_item.id not in broker_poller._work_manager._dispatch_timestamps
    assert broker_poller._work_manager.get_total_in_flight_count() == 0
    assert first_item.offset not in tracker.completed_offsets
    assert tracker.last_committed_offset == -1
    assert broker_poller._dirty_commit_partitions == set()
    assert broker_poller._completions_since_last_commit == 0
    assert broker_poller._pending_dlq_events == {}
    assert (topic_partition, first_item.offset) in broker_poller._message_cache
    broker_poller._publish_to_dlq.assert_not_awaited()
    assert "Discarding zombie completion" in caplog.text


@pytest.mark.asyncio
async def test_on_assign_shared_tracker_allows_key_hash_backlog_to_resume(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for on assign shared tracker allows key hash backlog to resume.
    # When: The control-plane behavior is exercised for on assign shared tracker allows key hash backlog to resume.
    broker_poller._on_assign(
        broker_poller.consumer,
        [KafkaTopicPartition(topic_partition.topic, topic_partition.partition, 0)],
    )
    broker_poller.ORDERING_MODE = broker_poller._work_manager._ordering_mode
    broker_poller._work_manager._blocking_cache_ttl = 0
    broker_poller._work_manager._blocking_cache_counter = 0

    tracker = broker_poller._offset_trackers[topic_partition]
    shared_tracker = broker_poller._work_manager._offset_trackers[topic_partition]
    # Then: The expected on assign shared tracker allows key hash backlog to resume behavior is asserted.
    assert shared_tracker is tracker
    assert shared_tracker.get_current_epoch() == tracker.get_current_epoch() == 1

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
    await broker_poller._work_manager.schedule()

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

    completed_events = await broker_poller._work_manager.poll_completed_events()
    await broker_poller._process_completed_events(completed_events)
    await broker_poller._work_manager.schedule()

    assert broker_poller._execution_engine.submit.await_count == 2
    second_item = broker_poller._execution_engine.submit.await_args_list[1].args[0]
    assert second_item.offset == 1
