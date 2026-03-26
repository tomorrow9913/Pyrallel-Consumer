"""Tests for WorkManager queue topology extraction."""


import pytest

from pyrallel_consumer.dto import OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem


def _make_item(tp: DtoTopicPartition, offset: int, key: object) -> WorkItem:
    return WorkItem(
        id=f"work-{offset}",
        tp=tp,
        offset=offset,
        epoch=1,
        key=key,
        payload=f"payload-{offset}".encode(),
    )


@pytest.mark.asyncio
async def test_topology_enqueue_batch_tracks_head_and_active_queue_key() -> None:
    from pyrallel_consumer.control_plane.work_queue_topology import WorkQueueTopology

    topology = WorkQueueTopology()
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    queue_key = (tp, b"key-a")

    topology.assign(tp)
    topology.enqueue_batch(
        tp,
        b"key-a",
        [_make_item(tp, 7, b"key-a"), _make_item(tp, 8, b"key-a")],
    )

    queue = topology.get_queue(tp, b"key-a")
    assert queue is not None
    assert queue.qsize() == 2
    assert topology.head_offsets[queue_key] == 7
    assert topology.head_queue_keys_by_offset[(tp, 7)] == queue_key
    assert queue_key in topology.active_runnable_queue_keys
    assert topology.runnable_queue_keys.count(queue_key) == 1


@pytest.mark.asyncio
async def test_topology_revoke_clears_queue_and_returns_removed_count() -> None:
    from pyrallel_consumer.control_plane.work_queue_topology import WorkQueueTopology

    topology = WorkQueueTopology()
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    topology.assign(tp)
    topology.enqueue_batch(
        tp,
        b"key-a",
        [_make_item(tp, 1, b"key-a"), _make_item(tp, 2, b"key-a")],
    )
    topology.enqueue_batch(
        tp,
        b"key-b",
        [_make_item(tp, 3, b"key-b")],
    )

    removed_count = topology.revoke(tp)

    assert removed_count == 3
    assert topology.virtual_partition_queues.get(tp) is None
    assert not topology.head_offsets
    assert not topology.head_queue_keys_by_offset
    assert not topology.active_runnable_queue_keys


@pytest.mark.asyncio
async def test_topology_pick_next_queue_prefers_blocking_offset() -> None:
    from pyrallel_consumer.control_plane.work_queue_topology import WorkQueueTopology

    topology = WorkQueueTopology()
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    blocking_queue_key = (tp, b"key-a")
    other_queue_key = (tp, b"key-b")

    topology.assign(tp)
    topology.enqueue_batch(tp, b"key-a", [_make_item(tp, 10, b"key-a")])
    topology.enqueue_batch(tp, b"key-b", [_make_item(tp, 20, b"key-b")])

    selected_queue_key = topology.pop_next_queue_key(
        {tp: OffsetRange(10, 10)},
        is_queue_eligible=lambda _queue_key: True,
    )

    assert selected_queue_key == blocking_queue_key
    assert selected_queue_key != other_queue_key
