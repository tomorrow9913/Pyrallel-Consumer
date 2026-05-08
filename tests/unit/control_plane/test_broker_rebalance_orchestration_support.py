# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_rebalance_orchestration_support.py
# Role: Verifies rebalance callback orchestration outside BrokerPoller.
# Extend here when assign/revoke bridge flow moves between poller and support modules.

from unittest.mock import ANY, MagicMock

import pytest
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.broker_rebalance_orchestration_support import (
    BrokerRebalanceOrchestrationSupport,
)
from pyrallel_consumer.control_plane.commit_coordinator import CommitCandidate
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def _make_support(
    *,
    bridge=None,
    rebalance_support=None,
    commit_coordinator=None,
    work_manager=None,
    offset_trackers=None,
    dirty_commit_partitions=None,
    unsettled_completions=None,
    unsettled_timestamps=None,
    pending_dlq_events=None,
    dropped_partitions=None,
    recorded_failures=None,
):
    """Create BrokerRebalanceOrchestrationSupport with mutable ledgers."""
    if bridge is None:
        bridge = MagicMock()
    if rebalance_support is None:
        rebalance_support = MagicMock()
    if work_manager is None:
        work_manager = MagicMock()
    if offset_trackers is None:
        offset_trackers = {}
    if dirty_commit_partitions is None:
        dirty_commit_partitions = set()
    if unsettled_completions is None:
        unsettled_completions = {}
    if unsettled_timestamps is None:
        unsettled_timestamps = {}
    if pending_dlq_events is None:
        pending_dlq_events = {}
    if dropped_partitions is None:
        dropped_partitions = []
    if recorded_failures is None:
        recorded_failures = []

    support = BrokerRebalanceOrchestrationSupport(
        rebalance_support=rebalance_support,
        rebalance_bridge=bridge,
        get_rebalance_state_strategy=lambda: "metadata_snapshot",
        get_max_revoke_grace_ms=lambda: 123,
        get_commit_coordinator=lambda: commit_coordinator,
        get_work_manager=lambda: work_manager,
        get_offset_trackers=lambda: offset_trackers,
        get_dirty_commit_partitions=lambda: dirty_commit_partitions,
        get_unsettled_completions_by_partition=lambda: unsettled_completions,
        get_unsettled_completion_timestamps_by_partition=lambda: unsettled_timestamps,
        get_pending_dlq_events=lambda: pending_dlq_events,
        drop_cached_partition_messages=dropped_partitions.append,
        encode_revoke_metadata=lambda tracker, base_offset: f"meta:{base_offset}",
        record_commit_failure_for_partition=(
            lambda tp, reason: recorded_failures.append((tp, reason))
        ),
        consumer_operation_guard=MagicMock(),
        logger=MagicMock(),
    )
    return support, {
        "bridge": bridge,
        "rebalance_support": rebalance_support,
        "commit_coordinator": commit_coordinator,
        "work_manager": work_manager,
        "offset_trackers": offset_trackers,
        "dirty_commit_partitions": dirty_commit_partitions,
        "unsettled_completions": unsettled_completions,
        "unsettled_timestamps": unsettled_timestamps,
        "pending_dlq_events": pending_dlq_events,
        "dropped_partitions": dropped_partitions,
        "recorded_failures": recorded_failures,
    }


def test_rebalance_orchestration_builds_assignments_before_bridge_install() -> None:
    # Given: rebalance support can hydrate assignments and the bridge accepts them.
    bridge = MagicMock()
    bridge.assign_from_callback.return_value = True
    rebalance_support = MagicMock()
    assignments = {DtoTopicPartition("test-topic", 0): MagicMock()}
    rebalance_support.build_assignments.return_value = assignments
    support, doubles = _make_support(
        bridge=bridge,
        rebalance_support=rebalance_support,
    )
    consumer = MagicMock()
    partitions = [KafkaTopicPartition("test-topic", 0, 100)]

    # When: assignment is built from the Kafka callback.
    assigned = support.assign_from_callback(consumer=consumer, partitions=partitions)

    # Then: hydrated assignment state is passed through the bounded bridge.
    assert assigned is True
    doubles["rebalance_support"].build_assignments.assert_called_once_with(
        consumer=consumer,
        partitions=partitions,
        strategy="metadata_snapshot",
        max_revoke_grace_ms=123,
        logger=ANY,
    )
    bridge.assign_from_callback.assert_called_once_with(assignments)


def test_rebalance_orchestration_installs_assignments_and_starts_coordinator() -> None:
    # Given: assignment trackers are ready and a commit coordinator is active.
    commit_coordinator = MagicMock()
    support, doubles = _make_support(commit_coordinator=commit_coordinator)
    tp = DtoTopicPartition("test-topic", 0)
    tracker = MagicMock()
    assignments = {tp: tracker}

    # When: bridge-protected assignment state is installed.
    support.assign_sync(assignments)

    # Then: coordinator acceptance, tracker ledger, and WorkManager all share it.
    commit_coordinator.start_accepting_partitions.assert_called_once()
    assert doubles["offset_trackers"] == assignments
    doubles["work_manager"].on_assign.assert_called_once_with(assignments)


def test_rebalance_orchestration_prepares_revoke_with_coordinator_candidate() -> None:
    # Given: a partition tracker and newer coordinator candidate exist for revoke.
    tp = DtoTopicPartition("test-topic", 0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    tracker.last_committed_offset = 4
    candidate = CommitCandidate(
        tp=tp,
        safe_offset=7,
        assignment_epoch=tracker.get_current_epoch(),
        lease_id=1,
        enqueued_at=0.0,
    )
    commit_coordinator = MagicMock()
    commit_coordinator.remaining_candidates.return_value = {tp: candidate}
    offset_trackers = {tp: tracker}
    support, doubles = _make_support(
        commit_coordinator=commit_coordinator,
        offset_trackers=offset_trackers,
    )

    # When: revoke preparation is built under the bridge lock.
    preparation = support.prepare_revoke_sync([KafkaTopicPartition("test-topic", 0)])

    # Then: the prepared commit uses the coordinator-safe offset and metadata.
    assert preparation.revoked_tps == [tp]
    assert len(preparation.offsets_to_commit) == 1
    assert preparation.offsets_to_commit[0].offset == 8
    assert preparation.offsets_to_commit[0].metadata == "meta:8"
    commit_coordinator.stop_accepting_partitions.assert_called_once_with([tp])
    doubles["work_manager"].on_revoke.assert_called_once_with([tp])
    assert doubles["dropped_partitions"] == [tp]


def test_rebalance_orchestration_cleans_revoked_partition_ledgers() -> None:
    # Given: revoked partition state exists across tracker, commit, and DLQ ledgers.
    revoked_tp = DtoTopicPartition("test-topic", 0)
    retained_tp = DtoTopicPartition("test-topic", 1)
    pending_event = MagicMock(spec=CompletionEvent)
    support, doubles = _make_support(
        offset_trackers={revoked_tp: MagicMock(), retained_tp: MagicMock()},
        dirty_commit_partitions={revoked_tp, retained_tp},
        unsettled_completions={revoked_tp: 2, retained_tp: 1},
        unsettled_timestamps={revoked_tp: 1.0, retained_tp: 2.0},
        pending_dlq_events={
            (revoked_tp, 10): pending_event,
            (retained_tp, 11): pending_event,
        },
        recorded_failures=[],
    )

    # When: revoke cleanup runs after broker commit attempts finish.
    support.cleanup_revoke_sync([revoked_tp], [revoked_tp])

    # Then: only revoked partition state is removed and failures are recorded.
    assert revoked_tp not in doubles["dirty_commit_partitions"]
    assert retained_tp in doubles["dirty_commit_partitions"]
    assert revoked_tp not in doubles["offset_trackers"]
    assert retained_tp in doubles["offset_trackers"]
    assert (revoked_tp, 10) not in doubles["pending_dlq_events"]
    assert (retained_tp, 11) in doubles["pending_dlq_events"]
    assert doubles["recorded_failures"] == [(revoked_tp, "kafka_exception")]


def test_rebalance_orchestration_records_bridge_failures_before_raising() -> None:
    # Given: revoke preparation cannot cross the bridge.
    support, doubles = _make_support()
    partitions = [KafkaTopicPartition("test-topic", 0)]

    # When/Then: the callback raises and records a replay-safe failure reason.
    with pytest.raises(RuntimeError, match="Revoke bridge failed"):
        support.handle_revoke_callback(
            consumer=MagicMock(),
            partitions=partitions,
            prepare_revoke_from_callback=MagicMock(return_value=None),
            cleanup_revoke_from_callback=MagicMock(return_value=True),
        )

    assert doubles["recorded_failures"] == [
        (DtoTopicPartition("test-topic", 0), "rebalance_bridge_failed")
    ]
