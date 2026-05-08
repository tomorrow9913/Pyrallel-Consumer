# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_work_manager_assignment.py
# Role: Verifies WorkManager initialization, assignment, shared trackers, completion hooks, and blocking offset reads.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._work_manager_support import (
    CompletionEvent,
    CompletionStatus,
    MagicMock,
    OffsetRange,
    OffsetTracker,
    OrderingMode,
    WorkItem,
    WorkManager,
    asyncio,
    patch,
    pytest,
    uuid,
)


@pytest.mark.asyncio
async def test_work_manager_initialization(work_manager):
    # Given: Inputs and test doubles are prepared for work manager initialization.
    # When: The control-plane behavior is exercised for work manager initialization.
    # Then: The expected work manager initialization behavior is asserted.
    assert isinstance(work_manager, WorkManager)
    assert work_manager._offset_trackers == {}
    assert work_manager._virtual_partition_queues == {}
    assert isinstance(work_manager._completion_queue, asyncio.Queue)
    assert work_manager._in_flight_work_items == {}
    assert work_manager._current_in_flight_count == 0
    assert work_manager.get_total_queued_messages() == 0


def test_work_manager_route_batch_size_defaults_to_one(mock_execution_engine):
    # Given: Inputs and test doubles are prepared for work manager route batch size defaults to one.
    # When: The control-plane behavior is exercised for work manager route batch size defaults to one.
    work_manager = WorkManager(execution_engine=mock_execution_engine)

    # Then: The expected work manager route batch size defaults to one behavior is asserted.
    assert work_manager.get_route_batch_size() == 1


def test_work_manager_rejects_invalid_route_batch_size(mock_execution_engine):
    # Given: Inputs and test doubles are prepared for work manager rejects invalid route batch size.
    # When: The control-plane behavior is exercised for work manager rejects invalid route batch size.
    # Then: The expected work manager rejects invalid route batch size behavior is asserted.
    with pytest.raises(ValueError, match="route_batch_size must be >= 1"):
        WorkManager(execution_engine=mock_execution_engine, route_batch_size=0)


def test_work_manager_rejects_bool_route_batch_size(mock_execution_engine):
    # Given: Inputs and test doubles are prepared for work manager rejects bool route batch size.
    # When: The control-plane behavior is exercised for work manager rejects bool route batch size.
    # Then: The expected work manager rejects bool route batch size behavior is asserted.
    with pytest.raises(ValueError, match="route_batch_size must be >= 1"):
        WorkManager(execution_engine=mock_execution_engine, route_batch_size=True)


@pytest.mark.asyncio
async def test_on_assign_uses_configured_max_revoke_grace_ms(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for on assign uses configured max revoke grace ms.
    # When: The control-plane behavior is exercised for on assign uses configured max revoke grace ms.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        max_revoke_grace_ms=321,
    )

    # Then: The expected on assign uses configured max revoke grace ms behavior is asserted.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        MockOffsetTrackerClass.return_value = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,
                starting_offset=0,
                max_revoke_grace_ms=321,
            )
        )

        work_manager.on_assign([mock_dto_topic_partition])

        MockOffsetTrackerClass.assert_called_once_with(
            topic_partition=mock_dto_topic_partition,
            starting_offset=0,
            max_revoke_grace_ms=321,
        )


@pytest.mark.asyncio
async def test_on_assign_and_on_revoke(
    work_manager, mock_dto_topic_partition, mock_dto_topic_partition_1
):
    # Given: Inputs and test doubles are prepared for on assign and on revoke.
    # When: The control-plane behavior is exercised for on assign and on revoke.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        # Configure the mock to return a mock instance when called
        MockOffsetTrackerClass.return_value = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )

        assigned_tps = [mock_dto_topic_partition, mock_dto_topic_partition_1]
        work_manager.on_assign(assigned_tps)

        # Then: The expected on assign and on revoke behavior is asserted.
        assert MockOffsetTrackerClass.call_count == 2  # Called twice for each partition
        MockOffsetTrackerClass.assert_any_call(
            topic_partition=mock_dto_topic_partition,
            starting_offset=0,
            max_revoke_grace_ms=500,
        )
        MockOffsetTrackerClass.assert_any_call(
            topic_partition=mock_dto_topic_partition_1,
            starting_offset=0,
            max_revoke_grace_ms=500,
        )

        assert len(work_manager._offset_trackers) == 2
        assert mock_dto_topic_partition in work_manager._offset_trackers
        assert mock_dto_topic_partition_1 in work_manager._offset_trackers
        assert isinstance(
            work_manager._offset_trackers[mock_dto_topic_partition], MagicMock
        )  # It's a mock now
        assert isinstance(
            work_manager._offset_trackers[mock_dto_topic_partition_1], MagicMock
        )

        assert len(work_manager._virtual_partition_queues) == 2
        assert mock_dto_topic_partition in work_manager._virtual_partition_queues
        assert work_manager._virtual_partition_queues[mock_dto_topic_partition] == {}

        revoked_tps = [mock_dto_topic_partition]
        work_manager.on_revoke(revoked_tps)

        assert len(work_manager._offset_trackers) == 1
        assert mock_dto_topic_partition not in work_manager._offset_trackers
        assert mock_dto_topic_partition_1 in work_manager._offset_trackers

        assert len(work_manager._virtual_partition_queues) == 1
        assert mock_dto_topic_partition not in work_manager._virtual_partition_queues
        assert mock_dto_topic_partition_1 in work_manager._virtual_partition_queues


@pytest.mark.asyncio
async def test_on_assign_uses_provided_starting_offsets(
    work_manager, mock_dto_topic_partition, mock_dto_topic_partition_1
):
    # Given: Inputs and test doubles are prepared for on assign uses provided starting offsets.
    # When: The control-plane behavior is exercised for on assign uses provided starting offsets.
    # Then: The expected on assign uses provided starting offsets behavior is asserted.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        MockOffsetTrackerClass.return_value = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )

        work_manager.on_assign(
            {
                mock_dto_topic_partition: 100,
                mock_dto_topic_partition_1: 200,
            }
        )

        MockOffsetTrackerClass.assert_any_call(
            topic_partition=mock_dto_topic_partition,
            starting_offset=100,
            max_revoke_grace_ms=500,
        )
        MockOffsetTrackerClass.assert_any_call(
            topic_partition=mock_dto_topic_partition_1,
            starting_offset=200,
            max_revoke_grace_ms=500,
        )


@pytest.mark.asyncio
async def test_on_assign_uses_shared_offset_trackers_when_provided(
    work_manager, mock_dto_topic_partition, mock_dto_topic_partition_1
):
    # Given: Inputs and test doubles are prepared for on assign uses shared offset trackers when provided.
    shared_tracker_0 = MagicMock(spec=OffsetTracker)
    shared_tracker_1 = MagicMock(spec=OffsetTracker)

    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        work_manager.on_assign(
            {
                mock_dto_topic_partition: shared_tracker_0,
                mock_dto_topic_partition_1: shared_tracker_1,
            }
        )

    # When: The control-plane behavior is exercised for on assign uses shared offset trackers when provided.
    MockOffsetTrackerClass.assert_not_called()
    # Then: The expected on assign uses shared offset trackers when provided behavior is asserted.
    assert work_manager._offset_trackers[mock_dto_topic_partition] is shared_tracker_0
    assert work_manager._offset_trackers[mock_dto_topic_partition_1] is shared_tracker_1


@pytest.mark.asyncio
async def test_poll_completed_events_does_not_mark_complete_for_shared_trackers(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for poll completed events does not mark complete for shared trackers.
    shared_tracker = MagicMock(spec=OffsetTracker)
    shared_tracker.get_gaps.return_value = []
    shared_tracker.get_current_epoch.return_value = 1
    work_manager.on_assign({mock_dto_topic_partition: shared_tracker})

    work_item_id = str(uuid.uuid4())
    work_manager._current_in_flight_count = 1
    work_manager._in_flight_work_items[work_item_id] = WorkItem(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=1,
        key=b"",
        payload=b"",
    )
    work_manager._dispatch_timestamps[work_item_id] = 0.0

    event = CompletionEvent(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [event]

    # When: The control-plane behavior is exercised for poll completed events does not mark complete for shared trackers.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events does not mark complete for shared trackers behavior is asserted.
    assert completed_events == [event]
    shared_tracker.mark_complete.assert_not_called()
    assert work_manager._current_in_flight_count == 0
    assert work_item_id not in work_manager._in_flight_work_items


@pytest.mark.asyncio
async def test_poll_completed_events_processes_shutdown_preserved_completion_normally(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poll completed events processes shutdown preserved completion normally.
    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    work_manager.on_assign([mock_dto_topic_partition])

    work_item_id = str(uuid.uuid4())
    work_manager._current_in_flight_count = 1
    work_manager._in_flight_work_items[work_item_id] = WorkItem(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=0,
        key=b"key",
        payload=b"payload",
    )
    work_manager._dispatch_timestamps[work_item_id] = 0.0
    work_manager._keys_in_flight.add((mock_dto_topic_partition, b"key"))

    preserved_completion = CompletionEvent(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=0,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [preserved_completion]

    # When: The control-plane behavior is exercised for poll completed events processes shutdown preserved completion normally.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events processes shutdown preserved completion normally behavior is asserted.
    assert completed_events == [preserved_completion]
    assert work_manager._current_in_flight_count == 0
    assert work_item_id not in work_manager._in_flight_work_items
    assert work_item_id not in work_manager._dispatch_timestamps
    assert (mock_dto_topic_partition, b"key") not in work_manager._keys_in_flight


@pytest.mark.asyncio
async def test_poll_completed_events_uses_work_completion_observer_hook(
    mock_execution_engine, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for poll completed events uses work completion observer hook.
    observed: list[tuple[CompletionEvent, WorkItem, float]] = []

    class _Exporter:
        def observe_work_completion(
            self,
            event: CompletionEvent,
            work_item: WorkItem,
            duration_seconds: float,
        ) -> None:
            observed.append((event, work_item, duration_seconds))

        def observe_completion(self, *args, **kwargs) -> None:
            raise AssertionError("observe_completion should not be called")

    work_manager = WorkManager(
        execution_engine=mock_execution_engine,
        metrics_exporter=_Exporter(),
    )
    work_manager.on_assign([mock_dto_topic_partition])

    work_item_id = str(uuid.uuid4())
    work_item = WorkItem(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=0,
        key=b"",
        payload=b"",
    )
    work_manager._current_in_flight_count = 1
    work_manager._in_flight_work_items[work_item_id] = work_item
    work_manager._dispatch_timestamps[work_item_id] = 0.0

    event = CompletionEvent(
        id=work_item_id,
        tp=mock_dto_topic_partition,
        offset=10,
        epoch=0,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_execution_engine.poll_completed_events.return_value = [event]

    # When: The control-plane behavior is exercised for poll completed events uses work completion observer hook.
    completed_events = await work_manager.poll_completed_events()

    # Then: The expected poll completed events uses work completion observer hook behavior is asserted.
    assert completed_events == [event]
    assert len(observed) == 1
    observed_event, observed_item, observed_duration = observed[0]
    assert observed_event == event
    assert observed_item == work_item
    assert observed_duration >= 0.0


@pytest.mark.asyncio
async def test_get_blocking_offsets_does_not_advance_shared_tracker_commit_state(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for get blocking offsets does not advance shared tracker commit state.
    shared_tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    shared_tracker.update_last_fetched_offset(1)
    shared_tracker.mark_complete(0)
    shared_tracker.mark_complete(1)
    work_manager.on_assign({mock_dto_topic_partition: shared_tracker})

    # When: The control-plane behavior is exercised for get blocking offsets does not advance shared tracker commit state.
    blocking_offsets = work_manager.get_blocking_offsets()

    # Then: The expected get blocking offsets does not advance shared tracker commit state behavior is asserted.
    assert blocking_offsets == {mock_dto_topic_partition: None}
    assert shared_tracker.last_committed_offset == -1
    assert list(shared_tracker.completed_offsets) == [0, 1]


@pytest.mark.asyncio
async def test_get_blocking_offsets_uses_first_gap_head_without_full_gap_scan(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for get blocking offsets uses first gap head without full gap scan.
    work_manager.on_assign([mock_dto_topic_partition])

    tracker = OffsetTracker(
        topic_partition=mock_dto_topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.update_last_fetched_offset(5)
    tracker.mark_complete(0)
    work_manager._offset_trackers[mock_dto_topic_partition] = tracker

    # When: The control-plane behavior is exercised for get blocking offsets uses first gap head without full gap scan.
    with patch.object(
        tracker,
        "get_gaps",
        side_effect=AssertionError("get_gaps should not be used"),
    ):
        blocking_offsets = work_manager.get_blocking_offsets()

    # Then: The expected get blocking offsets uses first gap head without full gap scan behavior is asserted.
    assert blocking_offsets == {mock_dto_topic_partition: OffsetRange(start=1, end=1)}
