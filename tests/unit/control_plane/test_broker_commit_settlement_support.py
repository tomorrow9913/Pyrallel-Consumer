# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_commit_settlement_support.py
# Role: Verifies commit settlement bookkeeping outside BrokerPoller.
# Extend here when completed-offset settlement ledgers move between poller and support.

from unittest.mock import MagicMock

from pyrallel_consumer.control_plane.broker_commit_settlement_support import (
    BrokerCommitSettlementSupport,
)
from pyrallel_consumer.control_plane.broker_completion_support import (
    CompletionProcessingResult,
)
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def _make_support(
    *,
    offset_trackers=None,
    dirty_commit_partitions=None,
    unsettled_completions=None,
    unsettled_timestamps=None,
    completions_since_last_commit: int = 0,
    metrics_exporter=None,
    now=None,
):
    """Create BrokerCommitSettlementSupport with mutable ledgers."""
    if offset_trackers is None:
        offset_trackers = {}
    if dirty_commit_partitions is None:
        dirty_commit_partitions = set()
    if unsettled_completions is None:
        unsettled_completions = {}
    if unsettled_timestamps is None:
        unsettled_timestamps = {}
    if now is None:

        def now() -> float:
            return 100.0

    state = {"completions_since_last_commit": completions_since_last_commit}
    support = BrokerCommitSettlementSupport(
        offset_trackers=offset_trackers,
        dirty_commit_partitions=dirty_commit_partitions,
        unsettled_completions_by_partition=unsettled_completions,
        unsettled_completion_timestamps_by_partition=unsettled_timestamps,
        get_completions_since_last_commit=lambda: state[
            "completions_since_last_commit"
        ],
        set_completions_since_last_commit=(
            lambda value: state.__setitem__("completions_since_last_commit", value)
        ),
        get_metrics_exporter=lambda: metrics_exporter,
        get_pipeline_engine_type=lambda: "async",
        now=now,
    )
    return support, {
        "offset_trackers": offset_trackers,
        "dirty_commit_partitions": dirty_commit_partitions,
        "unsettled_completions": unsettled_completions,
        "unsettled_timestamps": unsettled_timestamps,
        "state": state,
        "metrics_exporter": metrics_exporter,
    }


def test_commit_settlement_records_processed_completion_ledgers() -> None:
    # Given: a completion result includes one processed offset and one DLQ retry partition.
    completed_tp = DtoTopicPartition("test-topic", 0)
    retry_tp = DtoTopicPartition("test-topic", 1)
    support, doubles = _make_support(completions_since_last_commit=2)
    result = CompletionProcessingResult(
        processed_count=1,
        completed_partitions=frozenset({completed_tp}),
        completed_counts_by_partition={completed_tp: 1},
        completed_offsets_by_partition={completed_tp: (10,)},
    )

    # When: processed completions are recorded for commit settlement.
    support.record_processed_completions(
        result,
        pending_retry_partitions={retry_tp},
    )

    # Then: dirty partitions, unsettled counts, timestamps, and cadence count are updated.
    assert doubles["dirty_commit_partitions"] == {completed_tp, retry_tp}
    assert doubles["unsettled_completions"] == {completed_tp: 1}
    assert doubles["unsettled_timestamps"] == {completed_tp: {10: 100.0}}
    assert doubles["state"]["completions_since_last_commit"] == 3


def test_commit_settlement_observes_latency_and_retains_gap_timestamp() -> None:
    # Given: a committed offset leaves a later completed gap unsettled.
    tp = DtoTopicPartition("test-topic", 0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=10,
        max_revoke_grace_ms=1000,
    )
    tracker.mark_complete(10)
    tracker.mark_complete(12)
    tracker.commit_through(10)
    metrics_exporter = MagicMock()
    support, doubles = _make_support(
        offset_trackers={tp: tracker},
        dirty_commit_partitions={tp},
        unsettled_timestamps={tp: {10: 100.0, 12: 101.0}},
        metrics_exporter=metrics_exporter,
        now=lambda: 105.0,
    )

    # When: commit settlement clears through the committed safe offset.
    support.clear_committed_dirty_partitions([(tp, 10)])

    # Then: latency is observed for the settled offset and the gap timestamp is retained.
    metrics_exporter.observe_completion_to_commit_latency.assert_called_once_with(
        engine_type="async",
        duration_seconds=5.0,
    )
    assert doubles["dirty_commit_partitions"] == {tp}
    assert doubles["unsettled_completions"] == {tp: 1}
    assert doubles["unsettled_timestamps"] == {tp: {12: 101.0}}


def test_commit_settlement_clears_clean_partition_and_resets_cadence_count() -> None:
    # Given: a committed partition has no remaining completed gaps.
    tp = DtoTopicPartition("test-topic", 0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=1000,
    )
    tracker.mark_complete(0)
    tracker.commit_through(0)
    support, doubles = _make_support(
        offset_trackers={tp: tracker},
        dirty_commit_partitions={tp},
        unsettled_completions={tp: 1},
        unsettled_timestamps={tp: {0: 100.0}},
        completions_since_last_commit=1,
    )

    # When: commit settlement clears the committed partition.
    support.clear_committed_dirty_partitions([(tp, 0)])

    # Then: all settlement ledgers are cleared and cadence count resets.
    assert doubles["dirty_commit_partitions"] == set()
    assert doubles["unsettled_completions"] == {}
    assert doubles["unsettled_timestamps"] == {}
    assert doubles["state"]["completions_since_last_commit"] == 0


def test_commit_settlement_prunes_timestamps_without_metrics_observer() -> None:
    # Given: no metrics observer is installed for completion-to-commit latency.
    tp = DtoTopicPartition("test-topic", 0)
    support, doubles = _make_support(
        unsettled_timestamps={tp: {10: 100.0}},
        metrics_exporter=None,
    )

    # When: latency observation runs after commit settlement.
    support.observe_completion_to_commit_latency(tp, None, 10)

    # Then: timestamp cleanup still happens even without emitting a metric.
    assert doubles["unsettled_timestamps"] == {}
