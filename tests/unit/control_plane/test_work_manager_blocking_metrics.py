# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_work_manager_blocking_metrics.py
# Role: Verifies WorkManager blocking offset priority, rebalance submission guards, lag, gap, queue, and cleanup metrics.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._work_manager_support import (
    CompletionEvent,
    CompletionStatus,
    DtoTopicPartition,
    MagicMock,
    OffsetRange,
    OffsetTracker,
    WorkItem,
    patch,
    pytest,
)


@pytest.mark.asyncio
async def test_prioritize_blocking_offset(
    work_manager,
    mock_dto_topic_partition,
    mock_dto_topic_partition_1,
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for prioritize blocking offset.
    # When: The control-plane behavior is exercised for prioritize blocking offset.
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        # Mock OffsetTracker for tp0
        mock_tracker_tp0 = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        # tp0 has a blocking offset at 10
        mock_tracker_tp0.get_gaps.return_value = [OffsetRange(10, 10)]
        mock_tracker_tp0.advance_high_water_mark.return_value = (
            None  # Mock this to avoid errors
        )

        # Mock OffsetTracker for tp1
        mock_tracker_tp1 = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition_1,
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        # tp1 has no blocking offset
        mock_tracker_tp1.get_gaps.return_value = []
        mock_tracker_tp1.advance_high_water_mark.return_value = (
            None  # Mock this to avoid errors
        )

        # Configure the patch to return specific mocks based on topic_partition
        MockOffsetTrackerClass.side_effect = lambda topic_partition, **kwargs: (
            mock_tracker_tp0
            if topic_partition == mock_dto_topic_partition
            else mock_tracker_tp1
        )

        work_manager.on_assign([mock_dto_topic_partition, mock_dto_topic_partition_1])
        # Ensure work_manager internal trackers are set to our mocks
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_tp0
        work_manager._offset_trackers[mock_dto_topic_partition_1] = mock_tracker_tp1

        # Set max_in_flight_messages to allow multiple submissions
        work_manager._max_in_flight_messages = 3
        mock_execution_engine.submit.reset_mock()  # Reset mock calls for this test

        # Submit messages
        # Message 1 (tp0, offset 11 - non-blocking for tp0, but general queue)
        await work_manager.submit_message(
            mock_dto_topic_partition, 11, 1, b"key0-1", b"payload0-1"
        )
        # Message 2 (tp1, offset 20 - non-blocking for tp1, general queue)
        await work_manager.submit_message(
            mock_dto_topic_partition_1, 20, 1, b"key1-1", b"payload1-1"
        )
        # Message 3 (tp0, offset 10 - this is the blocking offset for tp0)
        await work_manager.submit_message(
            mock_dto_topic_partition, 10, 1, b"key0-0", b"payload0-0"
        )

        # Manually trigger the submission process if not all messages were submitted by submit_message calls
        # (they should be, due to recursive nature, but good to be explicit for testing).
        await work_manager.schedule()

        # Verify calls to submit
        # Then: The expected prioritize blocking offset behavior is asserted.
        assert mock_execution_engine.submit.call_count == 3

        # Assert the order of submission
        # The blocking message (tp0, offset 10) should be submitted first.
        # The exact order of the other two might depend on dictionary iteration order,
        # but the blocking one must be first.
        calls = mock_execution_engine.submit.call_args_list
        submitted_work_item_1 = calls[0].args[0]

        # Message 3 (tp0, offset 10) should be submitted first.
        assert submitted_work_item_1.tp == mock_dto_topic_partition
        assert submitted_work_item_1.offset == 10

        # The other two should be the remaining messages, order may vary.
        # Collect all submitted (tp, offset) pairs excluding the first one.
        actual_submitted = [(call.args[0].tp, call.args[0].offset) for call in calls]

        # Remove the first submitted item to compare the remaining two.
        actual_submitted.remove((mock_dto_topic_partition, 10))

        expected_remaining = sorted(
            [
                (mock_dto_topic_partition, 11),
                (mock_dto_topic_partition_1, 20),
            ],
            key=lambda x: (x[0].topic, x[0].partition, x[1]),
        )
        assert (
            sorted(actual_submitted, key=lambda x: (x[0].topic, x[0].partition, x[1]))
            == expected_remaining
        )


@pytest.mark.asyncio
async def test_no_submission_during_rebalance(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for no submission during rebalance.
    # When: The control-plane behavior is exercised for no submission during rebalance.
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
        MockOffsetTrackerClass.return_value = mock_tracker_instance
        # Ensure no blocking offsets by default
        mock_tracker_instance.get_gaps.return_value = []

        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        # Submit a message - it should be queued internally
        await work_manager.submit_message(
            mock_dto_topic_partition, 10, 1, b"key", b"payload"
        )
        # Then: The expected no submission during rebalance behavior is asserted.
        assert (
            work_manager._virtual_partition_queues[mock_dto_topic_partition][
                b"key"
            ].qsize()
            == 1
        )
        mock_execution_engine.submit.assert_not_awaited()  # Should not be submitted yet

        # Simulate rebalancing
        work_manager._rebalancing = True

        # Try to submit - it should be blocked
        await work_manager.schedule()
        mock_execution_engine.submit.assert_not_awaited()  # Still should not be submitted

        # Simulate rebalancing ends
        work_manager._rebalancing = False

        # Try to submit again - it should now go through
        await work_manager.schedule()
        mock_execution_engine.submit.assert_awaited_once()  # Now it should be submitted
        assert work_manager._current_in_flight_count == 1


@pytest.mark.asyncio
async def test_get_gaps(work_manager, mock_dto_topic_partition):
    # Given: Inputs and test doubles are prepared for get gaps.
    # When: The control-plane behavior is exercised for get gaps.
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
        MockOffsetTrackerClass.return_value = mock_tracker_instance

        expected_gaps = [OffsetRange(1, 3), OffsetRange(5, 5)]
        mock_tracker_instance.get_gaps.return_value = expected_gaps

        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        gaps = work_manager.get_gaps()

        # Then: The expected get gaps behavior is asserted.
        assert gaps == {mock_dto_topic_partition: expected_gaps}
        mock_tracker_instance.get_gaps.assert_called_once()


@pytest.mark.asyncio
async def test_get_true_lag(work_manager, mock_dto_topic_partition):
    # Given: Inputs and test doubles are prepared for get true lag.
    # When: The control-plane behavior is exercised for get true lag.
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
        MockOffsetTrackerClass.return_value = mock_tracker_instance

        mock_tracker_instance.last_fetched_offset = 100
        mock_tracker_instance.last_committed_offset = 50

        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        true_lag = work_manager.get_true_lag()

        # Then: The expected get true lag behavior is asserted.
        assert true_lag == {mock_dto_topic_partition: 50}


@pytest.mark.asyncio
async def test_get_virtual_queue_sizes(work_manager, mock_dto_topic_partition):
    # Given: Inputs and test doubles are prepared for get virtual queue sizes.
    # When: The control-plane behavior is exercised for get virtual queue sizes.
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
        MockOffsetTrackerClass.return_value = mock_tracker_instance

        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        # Submit messages with different keys
        await work_manager.submit_message(
            mock_dto_topic_partition, 1, 1, b"key1", b"payload1"
        )
        await work_manager.submit_message(
            mock_dto_topic_partition, 2, 1, b"key1", b"payload2"
        )
        await work_manager.submit_message(
            mock_dto_topic_partition, 3, 1, b"key2", b"payload3"
        )

        queue_sizes = work_manager.get_virtual_queue_sizes()

        expected_sizes = {
            mock_dto_topic_partition: {
                b"key1": 2,
                b"key2": 1,
            }
        }

        # Then: The expected get virtual queue sizes behavior is asserted.
        assert queue_sizes == expected_sizes


@pytest.mark.asyncio
async def test_cleanup_removes_empty_virtual_queue(work_manager):
    # Given: Inputs and test doubles are prepared for cleanup removes empty virtual queue.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    work_manager.on_assign([tp])
    work_manager._execution_engine.poll_completed_events.return_value = []

    await work_manager.submit_message(tp, offset=0, epoch=0, key="k1", payload=b"p")

    queue = work_manager._virtual_partition_queues[tp]["k1"]
    work_item: WorkItem = await queue.get()
    work_manager._total_queued_messages = 0
    work_manager._current_in_flight_count = 1
    work_manager._dispatch_timestamps[work_item.id] = 1.0

    event = CompletionEvent(
        id=work_item.id,
        tp=tp,
        offset=work_item.offset,
        epoch=work_item.epoch,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )

    # When: The control-plane behavior is exercised for cleanup removes empty virtual queue.
    await work_manager._completion_queue.put(event)

    # Then: The expected cleanup removes empty virtual queue behavior is asserted.
    assert work_manager.get_min_in_flight_offset(tp) == 0

    await work_manager.poll_completed_events()

    assert tp in work_manager._virtual_partition_queues
    assert work_manager._virtual_partition_queues[tp] == {}
    assert work_manager.get_total_queued_messages() == 0
    assert work_manager.get_min_in_flight_offset(tp) is None


@pytest.mark.asyncio
async def test_schedule_preserves_queue_order_after_submit_failure(
    work_manager, mock_dto_topic_partition
):
    # Given: Inputs and test doubles are prepared for schedule preserves queue order after submit failure.
    # When: The control-plane behavior is exercised for schedule preserves queue order after submit failure.
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
            mock_dto_topic_partition, 10, 1, b"key", b"payload-10"
        )
        await work_manager.submit_message(
            mock_dto_topic_partition, 11, 1, b"key", b"payload-11"
        )

        work_manager._execution_engine.submit.side_effect = RuntimeError("boom")

        await work_manager.schedule()

        queue = work_manager._virtual_partition_queues[mock_dto_topic_partition][b"key"]
        # Then: The expected schedule preserves queue order after submit failure behavior is asserted.
        assert [item.offset for item in list(queue._queue)] == [10, 11]


@pytest.mark.asyncio
async def test_schedule_logs_submit_failures(
    work_manager, mock_dto_topic_partition, caplog
):
    # Given: Inputs and test doubles are prepared for schedule logs submit failures.
    # When: The control-plane behavior is exercised for schedule logs submit failures.
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
        work_manager._execution_engine.submit.side_effect = RuntimeError("boom")

        await work_manager.submit_message(
            mock_dto_topic_partition, 10, 1, b"key", b"payload"
        )

        with caplog.at_level("ERROR"):
            await work_manager.schedule()

        # Then: The expected schedule logs submit failures behavior is asserted.
        assert "Error submitting work item" in caplog.text
        queue = work_manager._virtual_partition_queues[mock_dto_topic_partition][b"key"]
        assert queue.qsize() == 1
