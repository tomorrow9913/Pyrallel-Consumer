# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_work_manager_revoke_stale.py
# Role: Verifies WorkManager revoke cleanup and stale epoch completion handling.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._work_manager_support import (
    CompletionEvent,
    CompletionStatus,
    OffsetTracker,
    OrderingMode,
    WorkItem,
    WorkManager,
    pytest,
)


@pytest.mark.asyncio
async def test_poll_completed_events_logs_unmanaged_partition(
    work_manager, mock_dto_topic_partition, caplog
):
    # Given: Inputs and test doubles are prepared for poll completed events logs unmanaged partition.
    work_manager._execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id="missing",
            tp=mock_dto_topic_partition,
            offset=10,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    # When: The control-plane behavior is exercised for poll completed events logs unmanaged partition.
    with caplog.at_level("WARNING"):
        completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events logs unmanaged partition behavior is asserted.
    assert len(completed_events) == 1
    assert "Completion event for unmanaged TopicPartition" in caplog.text


@pytest.mark.asyncio
async def test_on_revoke_cleans_revoked_in_flight_state(
    mock_execution_engine, mock_dto_topic_partition, mock_dto_topic_partition_1
):
    # Given: Inputs and test doubles are prepared for on revoke cleans revoked in flight state.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    work_manager.on_assign([mock_dto_topic_partition, mock_dto_topic_partition_1])

    await work_manager.submit_message(
        mock_dto_topic_partition, 10, 1, b"revoked-key", b"payload-10"
    )
    await work_manager.submit_message(
        mock_dto_topic_partition_1, 20, 1, b"kept-key", b"payload-20"
    )
    # When: The control-plane behavior is exercised for on revoke cleans revoked in flight state.
    await work_manager.schedule()

    revoked_item = mock_execution_engine.submit.await_args_list[0].args[0]
    kept_item = mock_execution_engine.submit.await_args_list[1].args[0]
    # Then: The expected on revoke cleans revoked in flight state behavior is asserted.
    assert work_manager._current_in_flight_count == 2
    assert (
        work_manager.get_min_in_flight_offset(mock_dto_topic_partition)
        == revoked_item.offset
    )
    assert (
        work_manager.get_min_in_flight_offset(mock_dto_topic_partition_1)
        == kept_item.offset
    )
    assert (mock_dto_topic_partition, b"revoked-key") in work_manager._keys_in_flight
    assert (
        mock_dto_topic_partition_1,
        b"kept-key",
    ) in work_manager._keys_in_flight

    work_manager.on_revoke([mock_dto_topic_partition])

    assert revoked_item.id not in work_manager._in_flight_work_items
    assert revoked_item.id not in work_manager._dispatch_timestamps
    assert kept_item.id in work_manager._in_flight_work_items
    assert kept_item.id in work_manager._dispatch_timestamps
    assert work_manager._current_in_flight_count == 1
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None
    assert (
        work_manager.get_min_in_flight_offset(mock_dto_topic_partition_1)
        == kept_item.offset
    )
    assert (
        mock_dto_topic_partition,
        b"revoked-key",
    ) not in work_manager._keys_in_flight
    assert (
        mock_dto_topic_partition_1,
        b"kept-key",
    ) in work_manager._keys_in_flight


@pytest.mark.asyncio
async def test_on_revoke_does_not_decrement_for_queued_unsubmitted_items(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for on revoke does not decrement for queued unsubmitted items.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
        max_in_flight_messages=1,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message(
        mock_dto_topic_partition, 10, 1, b"key-1", b"payload-10"
    )
    await work_manager.submit_message(
        mock_dto_topic_partition, 11, 1, b"key-2", b"payload-11"
    )

    # When: The control-plane behavior is exercised for on revoke does not decrement for queued unsubmitted items.
    await work_manager.schedule()

    # Then: The expected on revoke does not decrement for queued unsubmitted items behavior is asserted.
    assert work_manager._current_in_flight_count == 1
    assert len(work_manager._in_flight_work_items) == 2
    assert work_manager.get_total_queued_messages() == 1

    work_manager.on_revoke([mock_dto_topic_partition])

    assert work_manager._current_in_flight_count == 0
    assert work_manager._in_flight_work_items == {}
    assert work_manager.get_total_queued_messages() == 0


@pytest.mark.asyncio
async def test_poll_completed_events_cleans_stale_epoch_in_flight_state(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poll completed events cleans stale epoch in flight state.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message(
        mock_dto_topic_partition, 10, tracker.get_current_epoch(), b"key-A", b"payload"
    )
    # When: The control-plane behavior is exercised for poll completed events cleans stale epoch in flight state.
    await work_manager.schedule()

    submitted_item = mock_execution_engine.submit.await_args_list[0].args[0]
    # Then: The expected poll completed events cleans stale epoch in flight state behavior is asserted.
    assert work_manager._current_in_flight_count == 1
    assert (
        work_manager.get_min_in_flight_offset(mock_dto_topic_partition)
        == submitted_item.offset
    )
    assert (mock_dto_topic_partition, b"key-A") in work_manager._keys_in_flight

    tracker.increment_epoch()

    stale_completion = CompletionEvent(
        id=submitted_item.id,
        tp=mock_dto_topic_partition,
        offset=submitted_item.offset,
        epoch=submitted_item.epoch,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [stale_completion]

    completed_events = await work_manager.poll_completed_events()

    assert completed_events == [stale_completion]
    assert submitted_item.id not in work_manager._in_flight_work_items
    assert submitted_item.id not in work_manager._dispatch_timestamps
    assert work_manager._current_in_flight_count == 0
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None
    assert (mock_dto_topic_partition, b"key-A") not in work_manager._keys_in_flight
    assert tracker.last_committed_offset == -1


@pytest.mark.asyncio
async def test_poll_completed_events_cleans_any_stale_epoch_for_tracked_work_item(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poll completed events cleans any stale epoch for tracked work item.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    await work_manager.submit_message(
        mock_dto_topic_partition, 10, tracker.get_current_epoch(), b"key-A", b"payload"
    )
    await work_manager.schedule()

    submitted_item = mock_execution_engine.submit.await_args_list[0].args[0]

    stale_completion = CompletionEvent(
        id=submitted_item.id,
        tp=mock_dto_topic_partition,
        offset=submitted_item.offset,
        epoch=submitted_item.epoch - 1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [stale_completion]

    # When: The control-plane behavior is exercised for poll completed events cleans any stale epoch for tracked work item.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events cleans any stale epoch for tracked work item behavior is asserted.
    assert completed_events == [stale_completion]
    assert submitted_item.id not in work_manager._in_flight_work_items
    assert submitted_item.id not in work_manager._dispatch_timestamps
    assert work_manager._current_in_flight_count == 0
    assert (mock_dto_topic_partition, b"key-A") not in work_manager._keys_in_flight


@pytest.mark.asyncio
async def test_stale_completion_after_reassign_does_not_touch_new_epoch_state(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for stale completion after reassign does not touch new epoch state.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )

    old_tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    old_tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: old_tracker})

    await work_manager.submit_message(
        mock_dto_topic_partition, 10, old_tracker.get_current_epoch(), b"key-A", b"v1"
    )
    await work_manager.schedule()
    old_item = mock_execution_engine.submit.await_args_list[0].args[0]

    work_manager.on_revoke([mock_dto_topic_partition])

    new_tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    new_tracker.increment_epoch()
    new_tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: new_tracker})

    await work_manager.submit_message(
        mock_dto_topic_partition, 11, new_tracker.get_current_epoch(), b"key-A", b"v2"
    )
    await work_manager.schedule()
    new_item = mock_execution_engine.submit.await_args_list[1].args[0]

    stale_completion = CompletionEvent(
        id=old_item.id,
        tp=mock_dto_topic_partition,
        offset=old_item.offset,
        epoch=old_item.epoch,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [stale_completion]

    # When: The control-plane behavior is exercised for stale completion after reassign does not touch new epoch state.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected stale completion after reassign does not touch new epoch state behavior is asserted.
    assert completed_events == [stale_completion]
    assert old_item.id not in work_manager._in_flight_work_items
    assert new_item.id in work_manager._in_flight_work_items
    assert new_item.id in work_manager._dispatch_timestamps
    assert work_manager._current_in_flight_count == 1
    assert (mock_dto_topic_partition, b"key-A") in work_manager._keys_in_flight
    assert new_tracker.last_committed_offset == -1


@pytest.mark.asyncio
async def test_stale_completion_same_offset_keeps_new_identity_mapping(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for stale completion same offset keeps new identity mapping.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.increment_epoch()
    tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    old_item = WorkItem(
        id="old-work",
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=tracker.get_current_epoch() - 1,
        key=b"key-A",
        payload=b"old",
    )
    new_item = WorkItem(
        id="new-work",
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"new",
    )
    work_manager._current_in_flight_count = 2
    work_manager._in_flight_work_items[old_item.id] = old_item
    work_manager._in_flight_work_items[new_item.id] = new_item
    work_manager._dispatch_timestamps[old_item.id] = 1.0
    work_manager._dispatch_timestamps[new_item.id] = 2.0
    work_manager._work_item_ids_by_tp_offset[
        (mock_dto_topic_partition, 10)
    ] = new_item.id
    work_manager._keys_in_flight.add((mock_dto_topic_partition, b"key-A"))
    work_manager._key_in_flight_counts[(mock_dto_topic_partition, b"key-A")] = 2

    stale_completion = CompletionEvent(
        id=old_item.id,
        tp=mock_dto_topic_partition,
        offset=old_item.offset,
        epoch=old_item.epoch,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [stale_completion]

    # When: The control-plane behavior is exercised for stale completion same offset keeps new identity mapping.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected stale completion same offset keeps new identity mapping behavior is asserted.
    assert completed_events == [stale_completion]
    assert old_item.id not in work_manager._in_flight_work_items
    assert old_item.id not in work_manager._dispatch_timestamps
    assert new_item.id in work_manager._in_flight_work_items
    assert new_item.id in work_manager._dispatch_timestamps
    assert (
        work_manager._work_item_ids_by_tp_offset[(mock_dto_topic_partition, 10)]
        == new_item.id
    )
    assert work_manager._current_in_flight_count == 1
    assert (mock_dto_topic_partition, b"key-A") in work_manager._keys_in_flight
    assert work_manager._key_in_flight_counts[(mock_dto_topic_partition, b"key-A")] == 1
    assert tracker.last_committed_offset == -1
