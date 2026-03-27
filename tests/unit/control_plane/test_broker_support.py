from __future__ import annotations

import logging
from collections import OrderedDict

from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def test_dlq_cache_support_respects_mode_and_byte_budget() -> None:
    from pyrallel_consumer.control_plane.broker_support import DlqCacheSupport

    cache: OrderedDict[
        tuple[DtoTopicPartition, int], tuple[object, object]
    ] = OrderedDict()
    support = DlqCacheSupport()
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    size_bytes = support.cache_message_for_dlq(
        message_cache=cache,
        size_bytes=0,
        should_cache=False,
        max_bytes=10,
        tp=tp,
        offset=1,
        key=b"k",
        value=b"value",
        logger=logging.getLogger(__name__),
    )
    assert cache == {}
    assert size_bytes == 0

    size_bytes = support.cache_message_for_dlq(
        message_cache=cache,
        size_bytes=size_bytes,
        should_cache=True,
        max_bytes=10,
        tp=tp,
        offset=1,
        key=b"k1",
        value=b"1234",
        logger=logging.getLogger(__name__),
    )
    size_bytes = support.cache_message_for_dlq(
        message_cache=cache,
        size_bytes=size_bytes,
        should_cache=True,
        max_bytes=10,
        tp=tp,
        offset=2,
        key=b"k2",
        value=b"5678",
        logger=logging.getLogger(__name__),
    )

    assert (tp, 1) not in cache
    assert cache[(tp, 2)] == (b"k2", b"5678")
    assert size_bytes == 6


def test_commit_planner_decodes_snapshot_from_committed_metadata() -> None:
    from pyrallel_consumer.control_plane.broker_support import BrokerCommitPlanner
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    planner = BrokerCommitPlanner(
        metadata_encoder=MetadataEncoder(),
        max_completed_offsets=2048,
    )
    metadata = planner.metadata_encoder.encode_metadata({103, 105}, 100)

    decoded = planner.decode_assignment_completed_offsets(
        strategy="metadata_snapshot",
        partition=KafkaTopicPartition("test-topic", 0, 100),
        committed_partition=KafkaTopicPartition(
            "test-topic", 0, 100, metadata=metadata
        ),
        last_committed=99,
    )

    assert decoded == {103, 105}


def test_commit_planner_builds_commit_payload_with_metadata_snapshot() -> None:
    from pyrallel_consumer.control_plane.broker_support import BrokerCommitPlanner
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    planner = BrokerCommitPlanner(
        metadata_encoder=MetadataEncoder(),
        max_completed_offsets=2048,
    )
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

    offsets = planner.build_offsets_to_commit(
        commits_to_make=[(tp, 4)],
        trackers={tp: tracker},
        strategy="metadata_snapshot",
    )

    assert len(offsets) == 1
    kafka_tp = offsets[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata == planner.metadata_encoder.encode_metadata({6, 7}, 5)


def test_commit_planner_omits_metadata_in_contiguous_only_mode() -> None:
    from pyrallel_consumer.control_plane.broker_support import BrokerCommitPlanner
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    planner = BrokerCommitPlanner(
        metadata_encoder=MetadataEncoder(),
        max_completed_offsets=2048,
    )
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

    offsets = planner.build_offsets_to_commit(
        commits_to_make=[(tp, 4)],
        trackers={tp: tracker},
        strategy="contiguous_only",
    )

    assert len(offsets) == 1
    kafka_tp = offsets[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata in (None, "")
