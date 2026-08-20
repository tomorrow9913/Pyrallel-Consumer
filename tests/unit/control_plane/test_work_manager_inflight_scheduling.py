# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_work_manager_inflight_scheduling.py
# Role: Verifies WorkManager in-flight accounting, force-fail lookup, scheduling, and minimum in-flight offsets.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._work_manager_support import (
    AsyncMock,
    CompletionEvent,
    CompletionStatus,
    MagicMock,
    OffsetTracker,
    WorkItem,
    WorkManager,
    patch,
    pytest,
    re,
    uuid,
)


@pytest.mark.asyncio
async def test_get_in_flight_counts_excludes_queued_not_dispatched_items(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for get in flight counts excludes queued not dispatched items.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        max_in_flight_messages=1,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message(
        mock_dto_topic_partition, 0, 1, b"key-a", b"payload-0"
    )
    # When: The control-plane behavior is exercised for get in flight counts excludes queued not dispatched items.
    await work_manager.submit_message(
        mock_dto_topic_partition, 1, 1, b"key-b", b"payload-1"
    )

    # Then: The expected get in flight counts excludes queued not dispatched items behavior is asserted.
    assert work_manager.get_in_flight_counts() == {}

    await work_manager.schedule()

    assert work_manager.get_in_flight_counts() == {mock_dto_topic_partition: 1}


@pytest.mark.asyncio
async def test_force_fail_uses_tp_offset_index(work_manager, mock_dto_topic_partition):
    # Given: Inputs and test doubles are prepared for force fail uses tp offset index.
    work_manager.on_assign([mock_dto_topic_partition])

    await work_manager.submit_message(
        mock_dto_topic_partition, 11, 1, b"message-key", b"payload"
    )
    work_item = next(iter(work_manager._in_flight_work_items.values()))

    # When: The control-plane behavior is exercised for force fail uses tp offset index.
    result = await work_manager.force_fail(
        tp=mock_dto_topic_partition,
        offset=11,
        epoch=1,
        error="forced",
        attempt=3,
    )

    # Then: The expected force fail uses tp offset index behavior is asserted.
    assert result is True
    event = await work_manager._completion_queue.get()
    assert event.id == work_item.id
    assert event.offset == 11
    assert event.error == "forced"


@pytest.mark.asyncio
async def test_schedule_does_not_rescan_every_virtual_queue_head(
    mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for schedule does not rescan every virtual queue head.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        max_in_flight_messages=1,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    for offset in range(50):
        await work_manager.submit_message(
            mock_dto_topic_partition,
            offset,
            1,
            b"key-%d" % offset,
            b"payload",
        )

    # When: The control-plane behavior is exercised for schedule does not rescan every virtual queue head.
    with patch.object(
        work_manager, "_peek_queue", wraps=work_manager._peek_queue
    ) as peek:
        await work_manager.schedule()

    # Then: The expected schedule does not rescan every virtual queue head behavior is asserted.
    assert mock_execution_engine.submit.await_count == 1
    assert peek.call_count <= 3


@pytest.mark.asyncio
async def test_schedule_keeps_assigned_partition_after_queue_drains(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for schedule keeps assigned partition after queue drains.
    # When: The control-plane behavior is exercised for schedule keeps assigned partition after queue drains.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        mock_tracker_instance = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        mock_tracker_instance.get_gaps.return_value = []
        MockOffsetTrackerClass.return_value = mock_tracker_instance

        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        await work_manager.submit_message(
            mock_dto_topic_partition, 0, 1, b"message-key", b"payload-0"
        )

        await work_manager.schedule()

        # Then: The expected schedule keeps assigned partition after queue drains behavior is asserted.
        assert mock_dto_topic_partition in work_manager._virtual_partition_queues
        assert work_manager._virtual_partition_queues[mock_dto_topic_partition] == {}

        await work_manager.submit_message(
            mock_dto_topic_partition, 1, 1, b"message-key", b"payload-1"
        )

        assert (
            work_manager._virtual_partition_queues[mock_dto_topic_partition][
                b"message-key"
            ].qsize()
            == 1
        )


def test_work_manager_allows_runtime_max_in_flight_updates(
    work_manager,
) -> None:
    # Given: Inputs and test doubles are prepared for work manager allows runtime max in flight updates.
    # When: The control-plane behavior is exercised for work manager allows runtime max in flight updates.
    work_manager.set_max_in_flight_messages(37)

    # Then: The expected work manager allows runtime max in flight updates behavior is asserted.
    assert work_manager.get_max_in_flight_messages() == 37


@pytest.mark.asyncio
async def test_submit_message_unassigned_tp_raises_error(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for submit message unassigned tp raises error.
    # When: The control-plane behavior is exercised for submit message unassigned tp raises error.
    # Then: The expected submit message unassigned tp raises error behavior is asserted.
    with pytest.raises(
        ValueError,
        match=re.escape(
            "TopicPartition %s is not assigned to WorkManager."
            % mock_dto_topic_partition
        ),
    ):
        await work_manager.submit_message(
            mock_dto_topic_partition, 10, 1, b"key", b"payload"
        )


@pytest.mark.asyncio
async def test_poll_completed_events(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for poll completed events.
    # When: The control-plane behavior is exercised for poll completed events.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        mock_tracker_instance = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        MockOffsetTrackerClass.return_value = mock_tracker_instance
        mock_tracker_instance.get_current_epoch.return_value = 1

        # Assign a topic-partition
        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = (
            mock_tracker_instance  # Ensure WorkManager uses our mock instance
        )
        # Ensure no blocking offsets by default
        mock_tracker_instance.get_gaps.return_value = []

        # Simulate some in-flight messages by manually setting _current_in_flight_count
        work_manager._current_in_flight_count = 2
        work_item_id_1 = str(uuid.uuid4())
        work_item_id_2 = str(uuid.uuid4())
        work_manager._in_flight_work_items[work_item_id_1] = WorkItem(
            id=work_item_id_1,
            tp=mock_dto_topic_partition,
            offset=10,
            epoch=1,
            key=b"",
            payload=b"",
        )
        work_manager._in_flight_work_items[work_item_id_2] = WorkItem(
            id=work_item_id_2,
            tp=mock_dto_topic_partition,
            offset=11,
            epoch=1,
            key=b"",
            payload=b"",
        )
        work_manager._dispatch_timestamps[work_item_id_1] = 0.0
        work_manager._dispatch_timestamps[work_item_id_2] = 0.0

        # Mock completed events from the execution engine
        event1 = CompletionEvent(
            id=work_item_id_1,
            tp=mock_dto_topic_partition,
            offset=10,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
        event2 = CompletionEvent(
            id=work_item_id_2,
            tp=mock_dto_topic_partition,
            offset=11,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
        mock_execution_engine.poll_completed_events.return_value = [event1, event2]

        original_schedule = work_manager.schedule
        work_manager.schedule = AsyncMock()

        completed_events = await work_manager.poll_completed_events()

        # Then: The expected poll completed events behavior is asserted.
        assert len(completed_events) == 2
        assert event1 in completed_events
        assert event2 in completed_events

        # Verify mark_complete was called on the OffsetTracker
        mock_tracker_instance.mark_complete.assert_any_call(10)
        mock_tracker_instance.mark_complete.assert_any_call(11)

        # Verify _current_in_flight_count decremented and work items removed
        assert work_manager._current_in_flight_count == 0
        assert work_item_id_1 not in work_manager._in_flight_work_items
        assert work_item_id_2 not in work_manager._in_flight_work_items

        assert (
            work_manager._current_in_flight_count == 0
        )  # Ensure no messages left in flight
        assert (
            mock_execution_engine.submit.call_count == 0
        )  # No messages should be submitted if the queue is empty.
        work_manager.schedule.assert_awaited_once()
        work_manager.schedule = original_schedule


@pytest.mark.asyncio
async def test_poll_completed_events_batches_internal_refill_scheduling(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for poll completed events batches internal refill scheduling.
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: tracker})
    work_manager._current_in_flight_count = 2

    events: list[CompletionEvent] = []
    for offset in (10, 11):
        work_item_id = str(uuid.uuid4())
        work_manager._in_flight_work_items[work_item_id] = WorkItem(
            id=work_item_id,
            tp=mock_dto_topic_partition,
            offset=offset,
            epoch=tracker.get_current_epoch(),
            key=b"",
            payload=b"",
        )
        work_manager._dispatch_timestamps[work_item_id] = 0.0
        events.append(
            CompletionEvent(
                id=work_item_id,
                tp=mock_dto_topic_partition,
                offset=offset,
                epoch=tracker.get_current_epoch(),
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            )
        )
    mock_execution_engine.poll_completed_events.return_value = events
    work_manager.schedule = AsyncMock()

    # When: The control-plane behavior is exercised for poll completed events batches internal refill scheduling.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events batches internal refill scheduling behavior is asserted.
    assert completed_events == events
    work_manager.schedule.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_poll_completed_events_can_defer_refill_scheduling(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for poll completed events can defer refill scheduling.
    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.increment_epoch()
    work_manager.on_assign({mock_dto_topic_partition: tracker})
    work_manager._current_in_flight_count = 1
    work_item_id = str(uuid.uuid4())
    work_manager._in_flight_work_items[work_item_id] = WorkItem(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=tracker.get_current_epoch(),
        key=b"",
        payload=b"",
    )
    work_manager._dispatch_timestamps[work_item_id] = 0.0
    event = CompletionEvent(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [event]
    work_manager.schedule = AsyncMock()

    # When: The control-plane behavior is exercised for poll completed events can defer refill scheduling.
    completed_events = await work_manager.poll_completed_events(
        schedule_after_release=False
    )

    # Then: The expected poll completed events can defer refill scheduling behavior is asserted.
    assert completed_events == [event]
    work_manager.schedule.assert_not_awaited()


@pytest.mark.asyncio
async def test_get_total_in_flight_count(
    work_manager,
    mock_dto_topic_partition,
    mock_dto_topic_partition_1,
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for get total in flight count.
    # When: The control-plane behavior is exercised for get total in flight count.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        MockOffsetTrackerClass.return_value = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        work_manager.on_assign([mock_dto_topic_partition, mock_dto_topic_partition_1])

        work_manager._current_in_flight_count = 8  # Set directly for WorkManager

        total_in_flight = work_manager.get_total_in_flight_count()
        # Then: The expected get total in flight count behavior is asserted.
        assert total_in_flight == 8


def test_get_min_in_flight_offset_returns_none_for_queued_but_not_dispatched_items(
    work_manager,
    mock_dto_topic_partition,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset returns none for queued but not dispatched items.
    work_manager.on_assign([mock_dto_topic_partition])

    # When: The control-plane behavior is exercised for get min in flight offset returns none for queued but not dispatched items.
    queued_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=21,
        epoch=1,
        key=b"queued",
        payload=b"payload",
    )
    work_manager._in_flight_work_items[queued_item.id] = queued_item

    # Then: The expected get min in flight offset returns none for queued but not dispatched items behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None


def test_get_min_in_flight_offset_uses_dispatch_timestamps_only(
    work_manager,
    mock_dto_topic_partition,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset uses dispatch timestamps only.
    work_manager.on_assign([mock_dto_topic_partition])

    dispatched_a = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=8,
        epoch=1,
        key=b"a",
        payload=b"payload-a",
    )
    dispatched_b = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=5,
        epoch=1,
        key=b"b",
        payload=b"payload-b",
    )
    # When: The control-plane behavior is exercised for get min in flight offset uses dispatch timestamps only.
    queued_only = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=3,
        epoch=1,
        key=b"queued",
        payload=b"payload-c",
    )

    for item in (dispatched_a, dispatched_b, queued_only):
        work_manager._in_flight_work_items[item.id] = item
    work_manager._dispatch_timestamps[dispatched_a.id] = 1.0
    work_manager._dispatch_timestamps[dispatched_b.id] = 2.0

    # Then: The expected get min in flight offset uses dispatch timestamps only behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) == 5


def test_get_min_in_flight_offset_ignores_stale_dispatch_ids(
    work_manager,
    mock_dto_topic_partition,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset ignores stale dispatch ids.
    work_manager.on_assign([mock_dto_topic_partition])

    # When: The control-plane behavior is exercised for get min in flight offset ignores stale dispatch ids.
    stale_item_id = str(uuid.uuid4())
    work_manager._dispatch_timestamps[stale_item_id] = 1.0

    # Then: The expected get min in flight offset ignores stale dispatch ids behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None


@pytest.mark.asyncio
async def test_get_min_in_flight_offset_updates_after_completion_cleanup(
    work_manager,
    mock_dto_topic_partition,
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset updates after completion cleanup.
    tracker = MagicMock(spec=OffsetTracker)
    tracker.get_current_epoch.return_value = 1
    tracker.get_gaps.return_value = []
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    first_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=5,
        epoch=1,
        key=b"first",
        payload=b"payload-first",
    )
    second_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=8,
        epoch=1,
        key=b"second",
        payload=b"payload-second",
    )
    for item, dispatch_time in ((first_item, 1.0), (second_item, 2.0)):
        work_manager._in_flight_work_items[item.id] = item
        work_manager._dispatch_timestamps[item.id] = dispatch_time
        work_manager._work_item_ids_by_tp_offset[(item.tp, item.offset)] = item.id
    work_manager._current_in_flight_count = 2

    # When: The control-plane behavior is exercised for get min in flight offset updates after completion cleanup.
    mock_execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=first_item.id,
            tp=mock_dto_topic_partition,
            offset=first_item.offset,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    # Then: The expected get min in flight offset updates after completion cleanup behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) == 5

    await work_manager.poll_completed_events()

    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) == 8

    mock_execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=second_item.id,
            tp=mock_dto_topic_partition,
            offset=second_item.offset,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    await work_manager.poll_completed_events()

    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None


def test_get_min_in_flight_offset_clears_revoked_partition_dispatches(
    work_manager,
    mock_dto_topic_partition,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset clears revoked partition dispatches.
    work_manager.on_assign([mock_dto_topic_partition])

    # When: The control-plane behavior is exercised for get min in flight offset clears revoked partition dispatches.
    work_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=13,
        epoch=1,
        key=b"revoked",
        payload=b"payload",
    )
    work_manager._in_flight_work_items[work_item.id] = work_item
    work_manager._dispatch_timestamps[work_item.id] = 1.0
    work_manager._work_item_ids_by_tp_offset[
        (work_item.tp, work_item.offset)
    ] = work_item.id
    work_manager._current_in_flight_count = 1

    # Then: The expected get min in flight offset clears revoked partition dispatches behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) == 13

    work_manager.on_revoke([mock_dto_topic_partition])

    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None


@pytest.mark.asyncio
async def test_get_min_in_flight_offset_clears_stale_epoch_dispatches(
    work_manager,
    mock_dto_topic_partition,
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for get min in flight offset clears stale epoch dispatches.
    tracker = MagicMock(spec=OffsetTracker)
    tracker.get_current_epoch.return_value = 2
    tracker.get_gaps.return_value = []
    work_manager.on_assign({mock_dto_topic_partition: tracker})

    stale_item = WorkItem(
        id=str(uuid.uuid4()),
        tp=mock_dto_topic_partition,
        offset=34,
        epoch=1,
        key=b"stale",
        payload=b"payload",
    )
    work_manager._in_flight_work_items[stale_item.id] = stale_item
    work_manager._dispatch_timestamps[stale_item.id] = 1.0
    work_manager._work_item_ids_by_tp_offset[
        (stale_item.tp, stale_item.offset)
    ] = stale_item.id
    work_manager._current_in_flight_count = 1
    # When: The control-plane behavior is exercised for get min in flight offset clears stale epoch dispatches.
    mock_execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=stale_item.id,
            tp=mock_dto_topic_partition,
            offset=stale_item.offset,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    # Then: The expected get min in flight offset clears stale epoch dispatches behavior is asserted.
    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) == 34

    await work_manager.poll_completed_events()

    assert work_manager.get_min_in_flight_offset(mock_dto_topic_partition) is None
