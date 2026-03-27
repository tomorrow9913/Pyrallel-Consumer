from __future__ import annotations

from unittest.mock import MagicMock

from confluent_kafka import OFFSET_INVALID
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def test_build_assignments_hydrates_metadata_snapshot() -> None:
    from pyrallel_consumer.control_plane.broker_rebalance_support import (
        BrokerRebalanceSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    consumer = MagicMock()
    metadata_encoder = MetadataEncoder()
    metadata = metadata_encoder.encode_metadata({103, 105}, 100)
    consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, 100, metadata=metadata)
    ]

    support = BrokerRebalanceSupport(metadata_encoder=metadata_encoder)
    assignments = support.build_assignments(
        consumer=consumer,
        partitions=[KafkaTopicPartition("test-topic", 0, 100)],
        strategy="metadata_snapshot",
        max_revoke_grace_ms=321,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = assignments[tp]
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105
    assert tracker.max_revoke_grace_ms == 321


def test_handle_revoke_commits_metadata_and_removes_trackers() -> None:
    from pyrallel_consumer.control_plane.broker_rebalance_support import (
        BrokerRebalanceSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    consumer = MagicMock()
    work_manager = MagicMock()
    metadata_encoder = MetadataEncoder()
    support = BrokerRebalanceSupport(metadata_encoder=metadata_encoder)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 4
    tracker.last_fetched_offset = 7
    tracker.mark_complete(6)
    tracker.mark_complete(7)
    trackers = {tp: tracker}
    dropped: list[DtoTopicPartition] = []

    support.handle_revoke(
        consumer=consumer,
        partitions=[KafkaTopicPartition("test-topic", 0)],
        work_manager=work_manager,
        offset_trackers=trackers,
        drop_cached_partition_messages=dropped.append,
        strategy="metadata_snapshot",
        logger=MagicMock(),
    )

    work_manager.on_revoke.assert_called_once_with([tp])
    assert dropped == [tp]
    offsets_arg = consumer.commit.call_args.kwargs["offsets"]
    assert len(offsets_arg) == 1
    assert offsets_arg[0].offset == 5
    assert offsets_arg[0].metadata == metadata_encoder.encode_metadata({6, 7}, 5)
    assert trackers == {}


def test_build_assignments_uses_negative_one_hwm_for_zero_offset_assignment() -> None:
    from pyrallel_consumer.control_plane.broker_rebalance_support import (
        BrokerRebalanceSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    consumer = MagicMock()
    consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, OFFSET_INVALID)
    ]

    support = BrokerRebalanceSupport(metadata_encoder=MetadataEncoder())
    assignments = support.build_assignments(
        consumer=consumer,
        partitions=[KafkaTopicPartition("test-topic", 0, 0)],
        strategy="contiguous_only",
        max_revoke_grace_ms=500,
    )

    tracker = assignments[DtoTopicPartition("test-topic", 0)]
    assert tracker.last_committed_offset == -1
    assert tracker.last_fetched_offset == -1


def test_build_assignments_bounds_committed_lookup_timeout() -> None:
    from pyrallel_consumer.control_plane.broker_rebalance_support import (
        BrokerRebalanceSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    consumer = MagicMock()
    consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, OFFSET_INVALID)
    ]

    support = BrokerRebalanceSupport(
        metadata_encoder=MetadataEncoder(),
        committed_lookup_timeout_seconds=7.5,
    )
    support.build_assignments(
        consumer=consumer,
        partitions=[KafkaTopicPartition("test-topic", 0, 0)],
        strategy="contiguous_only",
        max_revoke_grace_ms=500,
    )

    consumer.committed.assert_called_once_with(
        [KafkaTopicPartition("test-topic", 0, 0)],
        timeout=7.5,
    )
