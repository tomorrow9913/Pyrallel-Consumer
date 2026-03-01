"""Tests for WorkManager ordering mode behavior."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, OrderingMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def tp():
    return DtoTopicPartition(topic="test-topic", partition=0)


def _make_tracker_mock(tp):
    mock = MagicMock(
        spec=OffsetTracker(
            topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
        )
    )
    mock.get_gaps.return_value = []
    mock.advance_high_water_mark.return_value = None
    return mock


def _setup_wm(engine, tp, ordering_mode, max_in_flight=100):
    wm = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=max_in_flight,
        ordering_mode=ordering_mode,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker
    return wm, tracker


@pytest.mark.asyncio
async def test_key_hash_blocks_same_key_concurrent(mock_engine, tp):
    """KEY_HASH: second item with same key must NOT be submitted while first is in-flight."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_key_hash_allows_different_keys_concurrent(mock_engine, tp):
    """KEY_HASH: items with different keys CAN run concurrently."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_key_hash_unblocks_after_completion(mock_engine, tp):
    """KEY_HASH: after completion, the next item for that key is eligible."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    first_item = mock_engine.submit.call_args_list[0].args[0]

    completion = CompletionEvent(
        id=first_item.id,
        tp=tp,
        offset=first_item.offset,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_engine.poll_completed_events.return_value = [completion]
    await wm.poll_completed_events()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_unordered_allows_same_key_concurrent(mock_engine, tp):
    """UNORDERED: same-key items CAN run concurrently."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.UNORDERED)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_default_ordering_mode_is_unordered(mock_engine):
    """WorkManager default ordering_mode should be UNORDERED for backward compat."""
    wm = WorkManager(execution_engine=mock_engine)
    assert wm._ordering_mode == OrderingMode.UNORDERED


@pytest.mark.asyncio
async def test_key_hash_on_revoke_clears_keys_in_flight(mock_engine, tp):
    """on_revoke must clear keys_in_flight for revoked partitions."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1

    wm.on_revoke([tp])

    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        new_tracker = _make_tracker_mock(tp)
        MockOT.return_value = new_tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = new_tracker

    mock_engine.submit.reset_mock()
    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_partition_blocks_same_partition_concurrent(mock_engine, tp):
    """PARTITION: second item on same partition must NOT be submitted while first is in-flight."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_partition_allows_different_partitions_concurrent(mock_engine, tp):
    """PARTITION: items on different partitions CAN run concurrently."""
    tp2 = DtoTopicPartition(topic="test-topic", partition=1)
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker2 = _make_tracker_mock(tp2)
        MockOT.return_value = tracker2
        wm.on_assign([tp2])
        wm._offset_trackers[tp2] = tracker2

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp2, 0, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_partition_unblocks_after_completion(mock_engine, tp):
    """PARTITION: after completion, the next item on that partition is eligible."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    first_item = mock_engine.submit.call_args_list[0].args[0]

    completion = CompletionEvent(
        id=first_item.id,
        tp=tp,
        offset=first_item.offset,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_engine.poll_completed_events.return_value = [completion]
    await wm.poll_completed_events()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_partition_on_revoke_clears_partitions_in_flight(mock_engine, tp):
    """on_revoke must clear partitions_in_flight for revoked partitions."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1

    wm.on_revoke([tp])

    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        new_tracker = _make_tracker_mock(tp)
        MockOT.return_value = new_tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = new_tracker

    mock_engine.submit.reset_mock()
    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1
