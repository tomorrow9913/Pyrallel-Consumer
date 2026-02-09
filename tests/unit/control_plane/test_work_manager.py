import asyncio
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def work_manager(mock_execution_engine):
    return WorkManager(execution_engine=mock_execution_engine)


@pytest.fixture
def mock_offset_tracker_instance(mock_dto_topic_partition):
    # This fixture provides an instance of OffsetTracker (or its mock)
    # for direct manipulation or for when WorkManager needs to interact with an existing one.
    tracker = MagicMock(
        spec=OffsetTracker(
            topic_partition=mock_dto_topic_partition,
            starting_offset=0,
            max_revoke_grace_ms=500,
        )
    )
    tracker.get_current_epoch.return_value = 1
    tracker.in_flight_count = 0  # WorkManager will not set this directly on the real tracker anymore, but mock needs it for consistency
    tracker.get_gaps.return_value = []
    return tracker


@pytest.fixture
def mock_dto_topic_partition():
    return DtoTopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def mock_dto_topic_partition_1():
    return DtoTopicPartition(topic="test-topic", partition=1)


@pytest.mark.asyncio
async def test_work_manager_initialization(work_manager):
    assert isinstance(work_manager, WorkManager)
    assert work_manager._offset_trackers == {}
    assert work_manager._virtual_partition_queues == {}
    assert isinstance(work_manager._completion_queue, asyncio.Queue)
    assert work_manager._in_flight_work_items == {}
    assert work_manager._current_in_flight_count == 0


@pytest.mark.asyncio
async def test_on_assign_and_on_revoke(
    work_manager, mock_dto_topic_partition, mock_dto_topic_partition_1
):
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
async def test_submit_message(
    work_manager, mock_dto_topic_partition, mock_execution_engine
):
    with patch(
        "pyrallel_consumer.control_plane.work_manager.OffsetTracker"
    ) as MockOffsetTrackerClass:
        # Configure the mock to return a mock instance when called
        mock_tracker_instance = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        MockOffsetTrackerClass.return_value = mock_tracker_instance
        # Ensure no blocking offsets by default
        mock_tracker_instance.get_gaps.return_value = []

        # Assign the topic-partition first
        work_manager.on_assign([mock_dto_topic_partition])
        work_manager._offset_trackers[mock_dto_topic_partition] = mock_tracker_instance

        offset = 10
        epoch = 1
        key = b"message-key"
        payload = b"test-payload"

        # Submit the message (it only queues it internally now)
        await work_manager.submit_message(
            mock_dto_topic_partition, offset, epoch, key, payload
        )

        # Verify that the message is in the internal queue and tracked
        virtual_queue = work_manager._virtual_partition_queues[
            mock_dto_topic_partition
        ][key]
        assert virtual_queue.qsize() == 1
        queued_work_item = await virtual_queue.get()
        assert queued_work_item.offset == offset
        assert queued_work_item.epoch == epoch
        assert queued_work_item.key == key
        assert queued_work_item.payload == payload
        # Put it back for _try_submit_to_execution_engine to pick up
        await virtual_queue.put(queued_work_item)

        assert len(work_manager._in_flight_work_items) == 1
        assert queued_work_item.id in work_manager._in_flight_work_items
        assert (
            work_manager._in_flight_work_items[queued_work_item.id] == queued_work_item
        )
        assert (
            work_manager._current_in_flight_count == 0
        )  # No messages are in-flight yet

        # Now, explicitly trigger the submission process
        await work_manager._try_submit_to_execution_engine()

        # Verify that the item was now submitted to the execution engine
        mock_execution_engine.submit.assert_awaited_once()
        submitted_work_item = mock_execution_engine.submit.call_args[0][0]
        assert submitted_work_item.id == queued_work_item.id
        assert work_manager._current_in_flight_count == 1


@pytest.mark.asyncio
async def test_submit_message_unassigned_tp_raises_error(
    work_manager, mock_dto_topic_partition
):
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

        # Mock completed events from the execution engine
        event1 = CompletionEvent(
            id=work_item_id_1,
            tp=mock_dto_topic_partition,
            offset=10,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
        )
        event2 = CompletionEvent(
            id=work_item_id_2,
            tp=mock_dto_topic_partition,
            offset=11,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
        )
        mock_execution_engine.poll_completed_events.return_value = [event1, event2]

        completed_events = await work_manager.poll_completed_events()

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

        # Verify _try_submit_to_execution_engine was called after each completion (2 completions)
        # It's called once in on_assign, and twice for each completion, but it finds nothing to submit in the queue.
        # However, due to the _try_submit_to_execution_engine recursion, the submit might be called multiple times during the initial message submission.
        # So we check the call count more generally.
        # Initially, WorkManager is initialized, and on_assign calls _try_submit once, then each of the 2 completions calls it again.
        # This makes it 1 (on_assign) + 2 (poll_completed_events) = 3 calls.
        assert (
            work_manager._current_in_flight_count == 0
        )  # Ensure no messages left in flight
        assert (
            mock_execution_engine.submit.call_count == 0
        )  # No messages should be submitted if the queue is empty.


@pytest.mark.asyncio
async def test_get_total_in_flight_count(
    work_manager,
    mock_dto_topic_partition,
    mock_dto_topic_partition_1,
    mock_execution_engine,
):
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
        assert total_in_flight == 8


@pytest.mark.asyncio
async def test_prioritize_blocking_offset(
    work_manager,
    mock_dto_topic_partition,
    mock_dto_topic_partition_1,
    mock_execution_engine,
):
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
        await work_manager._try_submit_to_execution_engine()

        # Verify calls to submit
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
