from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def test_dlq_cache_support_respects_mode_and_byte_budget() -> None:
    # Given: inputs for `dlq cache support respects mode and byte budget` are prepared.
    from pyrallel_consumer.control_plane.broker_support import DlqCacheSupport

    cache: OrderedDict[
        tuple[DtoTopicPartition, int], tuple[object, object]
    ] = OrderedDict()
    support = DlqCacheSupport()
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    # When: the broker support code path is exercised.
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
    # Then: the expected `dlq cache support respects mode and byte budget` behavior is asserted.
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
    # Given: inputs for `commit planner decodes snapshot from committe...` are prepared.
    from pyrallel_consumer.control_plane.broker_support import BrokerCommitPlanner
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    planner = BrokerCommitPlanner(
        metadata_encoder=MetadataEncoder(),
        max_completed_offsets=2048,
    )
    metadata = planner.metadata_encoder.encode_metadata({103, 105}, 100)

    # When: the broker support code path is exercised.
    decoded = planner.decode_assignment_completed_offsets(
        strategy="metadata_snapshot",
        partition=KafkaTopicPartition("test-topic", 0, 100),
        committed_partition=KafkaTopicPartition(
            "test-topic", 0, 100, metadata=metadata
        ),
        last_committed=99,
    )

    # Then: the expected `commit planner decodes snapshot from committe...` behavior is asserted.
    assert decoded == {103, 105}


def test_commit_planner_builds_commit_payload_with_metadata_snapshot() -> None:
    # Given: inputs for `commit planner builds commit payload with met...` are prepared.
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

    # When: the broker support code path is exercised.
    # Then: the expected `commit planner builds commit payload with met...` behavior is asserted.
    assert len(offsets) == 1
    kafka_tp = offsets[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata == planner.metadata_encoder.encode_metadata({6, 7}, 5)


def test_commit_planner_omits_metadata_in_contiguous_only_mode() -> None:
    # Given: inputs for `commit planner omits metadata in contiguous o...` are prepared.
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

    # When: the broker support code path is exercised.
    # Then: the expected `commit planner omits metadata in contiguous o...` behavior is asserted.
    assert len(offsets) == 1
    kafka_tp = offsets[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata in (None, "")


@pytest.mark.asyncio
async def test_commit_support_builds_commit_payload_and_advances_tracker() -> None:
    # Given: inputs for `commit support builds commit payload and adva...` are prepared.
    from pyrallel_consumer.control_plane.broker_support import (
        BrokerCommitPlanner,
        BrokerCommitSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.mark_complete(0)
    tracker.mark_complete(1)
    tracker.mark_complete(3)
    consumer = MagicMock()
    support = BrokerCommitSupport(
        commit_planner=BrokerCommitPlanner(
            metadata_encoder=MetadataEncoder(),
            max_completed_offsets=2048,
        ),
        logger=logging.getLogger(__name__),
    )

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    await support.commit_offsets(
        consumer=consumer,
        offset_trackers={tp: tracker},
        control_lock=asyncio.Lock(),
        commits_to_make=[(tp, 1)],
        strategy="metadata_snapshot",
        to_thread=passthrough_to_thread,
    )

    _, kwargs = consumer.commit.call_args
    offsets_arg = kwargs["offsets"]
    # When: the broker support code path is exercised.
    # Then: the expected `commit support builds commit payload and adva...` behavior is asserted.
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert isinstance(kafka_tp, KafkaTopicPartition)
    assert (
        kafka_tp.metadata
        == support._commit_planner.metadata_encoder.encode_metadata({3}, 2)
    )
    assert tracker.last_committed_offset == 1
    assert tracker.completed_offsets == {3}


@pytest.mark.asyncio
async def test_commit_support_skips_removed_tracker_candidates() -> None:
    # Given: inputs for `commit support skips removed tracker candidates` are prepared.
    # When: the broker support code path is exercised.
    from pyrallel_consumer.control_plane.broker_support import (
        BrokerCommitPlanner,
        BrokerCommitSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    consumer = MagicMock()
    support = BrokerCommitSupport(
        commit_planner=BrokerCommitPlanner(
            metadata_encoder=MetadataEncoder(),
            max_completed_offsets=2048,
        ),
        logger=logging.getLogger(__name__),
    )

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    await support.commit_offsets(
        consumer=consumer,
        offset_trackers={},
        control_lock=asyncio.Lock(),
        commits_to_make=[(tp, 0)],
        strategy="contiguous_only",
        to_thread=passthrough_to_thread,
    )

    # Then: the expected `commit support skips removed tracker candidates` behavior is asserted.
    consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_commit_support_serializes_commit_ready_offsets_and_releases_control_lock():
    # Given: inputs for `commit support serializes commit ready offset...` are prepared.
    from pyrallel_consumer.control_plane.broker_support import (
        BrokerCommitPlanner,
        BrokerCommitSupport,
    )
    from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder

    topic_partition = DtoTopicPartition(topic="test-topic", partition=0)
    support = BrokerCommitSupport(
        commit_planner=BrokerCommitPlanner(
            metadata_encoder=MetadataEncoder(),
            max_completed_offsets=2048,
        ),
        logger=logging.getLogger(__name__),
    )
    control_lock = asyncio.Lock()
    commit_lock = asyncio.Lock()
    active_commits = 0
    max_active_commits = 0

    async def fake_commit_offsets(commits_to_make):
        nonlocal active_commits, max_active_commits
        assert commits_to_make == [(topic_partition, 0)]
        assert not control_lock.locked()
        active_commits += 1
        max_active_commits = max(max_active_commits, active_commits)
        await asyncio.sleep(0)
        active_commits -= 1

    # When: the broker support code path is exercised.
    await asyncio.gather(
        support.commit_ready_offsets(
            commit_lock=commit_lock,
            control_lock=control_lock,
            build_commit_candidates=lambda: [(topic_partition, 0)],
            commit_offsets=AsyncMock(side_effect=fake_commit_offsets),
        ),
        support.commit_ready_offsets(
            commit_lock=commit_lock,
            control_lock=control_lock,
            build_commit_candidates=lambda: [(topic_partition, 0)],
            commit_offsets=AsyncMock(side_effect=fake_commit_offsets),
        ),
    )

    # Then: the expected `commit support serializes commit ready offset...` behavior is asserted.
    assert max_active_commits == 1


@pytest.mark.asyncio
async def test_drain_support_merges_timeout_events_and_schedules_once() -> None:
    # Given: inputs for `drain support merges timeout events and sched...` are prepared.
    from pyrallel_consumer.control_plane.broker_support import BrokerDrainSupport

    completion_event = object()
    timeout_event = object()
    process_completed_events = AsyncMock()
    schedule = AsyncMock()
    support = BrokerDrainSupport()

    # When: the broker support code path is exercised.
    has_completion = await support.drain_completion_events_once(
        poll_completed_events=AsyncMock(return_value=[completion_event]),
        handle_blocking_timeouts=AsyncMock(return_value=[timeout_event]),
        process_completed_events=process_completed_events,
        schedule=schedule,
    )

    # Then: the expected `drain support merges timeout events and sched...` behavior is asserted.
    assert has_completion is True
    process_completed_events.assert_awaited_once_with([completion_event, timeout_event])
    schedule.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_drain_support_returns_false_when_no_completion_events() -> None:
    # Given: inputs for `drain support returns false when no completio...` are prepared.
    from pyrallel_consumer.control_plane.broker_support import BrokerDrainSupport

    process_completed_events = AsyncMock()
    schedule = AsyncMock()
    support = BrokerDrainSupport()

    # When: the broker support code path is exercised.
    has_completion = await support.drain_completion_events_once(
        poll_completed_events=AsyncMock(return_value=[]),
        handle_blocking_timeouts=AsyncMock(return_value=[]),
        process_completed_events=process_completed_events,
        schedule=schedule,
    )

    # Then: the expected `drain support returns false when no completio...` behavior is asserted.
    assert has_completion is False
    process_completed_events.assert_not_awaited()
    schedule.assert_not_awaited()
