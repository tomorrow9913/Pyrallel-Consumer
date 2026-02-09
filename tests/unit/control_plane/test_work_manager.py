import asyncio
import re
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
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
    # get_blocking_offset is removed from OffsetTracker, so we don't mock it here
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
        MockOffsetTrackerClass.return_value = MagicMock(
            spec=OffsetTracker(
                topic_partition=mock_dto_topic_partition,  # dummy args for spec
                starting_offset=0,
                max_revoke_grace_ms=500,
            )
        )
        # Assign the topic-partition first
        work_manager.on_assign([mock_dto_topic_partition])

        offset = 10
        epoch = 1
        key = b"message-key"
        payload = b"test-payload"

        await work_manager.submit_message(
            mock_dto_topic_partition, offset, epoch, key, payload
        )

        # Verify that an item was submitted to the execution engine
        mock_execution_engine.submit.assert_awaited_once()
        submitted_work_item = mock_execution_engine.submit.call_args[0][0]
        assert isinstance(submitted_work_item, WorkItem)
        assert isinstance(submitted_work_item.id, str)
        assert submitted_work_item.tp == mock_dto_topic_partition
        assert submitted_work_item.offset == offset
        assert submitted_work_item.epoch == epoch
        assert submitted_work_item.key == key
        assert submitted_work_item.payload == payload

        # Verify in_flight_count incremented and WorkItem tracked
        assert work_manager._current_in_flight_count == 1
        assert submitted_work_item.id in work_manager._in_flight_work_items
        assert (
            work_manager._in_flight_work_items[submitted_work_item.id]
            == submitted_work_item
        )


@pytest.mark.asyncio
async def test_submit_message_unassigned_tp_raises_error(
    work_manager, mock_dto_topic_partition
):
    with pytest.raises(
        ValueError,
        match=re.escape(
            f"TopicPartition {mock_dto_topic_partition} is not assigned to WorkManager."
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
        assert mock_execution_engine.submit.call_count == 0


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
