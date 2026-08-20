# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_commit_settlement_support.py
# Role: Tracks completed-but-unsettled offsets and commit-settlement cleanup.
# Extend here for commit settlement bookkeeping; keep Kafka commit execution in broker_poller.py.
from __future__ import annotations

import time
from collections.abc import Callable, MutableMapping
from typing import Any

from pyrallel_consumer.control_plane.broker_completion_support import (
    CompletionProcessingResult,
)
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class BrokerCommitSettlementSupport:
    """Maintain commit settlement ledgers for completed offsets."""

    def __init__(
        self,
        *,
        offset_trackers: MutableMapping[DtoTopicPartition, OffsetTracker],
        dirty_commit_partitions: set[DtoTopicPartition],
        unsettled_completions_by_partition: MutableMapping[DtoTopicPartition, int],
        unsettled_completion_timestamps_by_partition: MutableMapping[
            DtoTopicPartition, dict[int, float]
        ],
        get_completions_since_last_commit: Callable[[], int],
        set_completions_since_last_commit: Callable[[int], None],
        get_metrics_exporter: Callable[[], Any | None],
        get_pipeline_engine_type: Callable[[], str],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize commit settlement bookkeeping support."""
        self._offset_trackers = offset_trackers
        self._dirty_commit_partitions = dirty_commit_partitions
        self._unsettled_completions_by_partition = unsettled_completions_by_partition
        self._unsettled_completion_timestamps_by_partition = (
            unsettled_completion_timestamps_by_partition
        )
        self._get_completions_since_last_commit = get_completions_since_last_commit
        self._set_completions_since_last_commit = set_completions_since_last_commit
        self._get_metrics_exporter = get_metrics_exporter
        self._get_pipeline_engine_type = get_pipeline_engine_type
        self._now = now

    def record_processed_completions(
        self,
        processing_result: CompletionProcessingResult,
        *,
        pending_retry_partitions: set[DtoTopicPartition],
    ) -> None:
        """Record newly processed completions as pending commit settlement."""
        processed_count = processing_result.processed_count
        if processed_count <= 0:
            return

        completed_at = self._now()
        dirty_partitions = (
            set(processing_result.completed_partitions) | pending_retry_partitions
        )
        self._dirty_commit_partitions.update(dirty_partitions)
        for tp, count in processing_result.completed_counts_by_partition.items():
            self._unsettled_completions_by_partition[tp] = (
                self._unsettled_completions_by_partition.get(tp, 0) + count
            )
        for tp, offsets in processing_result.completed_offsets_by_partition.items():
            timestamp_ledger = (
                self._unsettled_completion_timestamps_by_partition.setdefault(tp, {})
            )
            for offset in offsets:
                timestamp_ledger[offset] = completed_at
        self._set_completions_since_last_commit(
            self._get_completions_since_last_commit() + processed_count
        )

    def clear_committed_dirty_partitions(
        self, commits_to_make: list[tuple[DtoTopicPartition, int]]
    ) -> None:
        """Clear or retain settlement ledgers after successful Kafka commit."""
        for tp, safe_offset in commits_to_make:
            tracker = self._offset_trackers.get(tp)
            remaining_unsettled = (
                len(tracker.completed_offsets) if tracker is not None else 0
            )
            self.observe_completion_to_commit_latency(tp, tracker, safe_offset)
            if remaining_unsettled > 0:
                self._dirty_commit_partitions.add(tp)
                self._unsettled_completions_by_partition[tp] = remaining_unsettled
            else:
                self._dirty_commit_partitions.discard(tp)
                self._unsettled_completions_by_partition.pop(tp, None)
                self._unsettled_completion_timestamps_by_partition.pop(tp, None)
        if not self._dirty_commit_partitions:
            self._set_completions_since_last_commit(0)

    def observe_completion_to_commit_latency(
        self,
        tp: DtoTopicPartition,
        tracker: OffsetTracker | None,
        safe_offset: int,
    ) -> None:
        """Observe latency for settled completion offsets and prune old timestamps."""
        timestamp_ledger = self._unsettled_completion_timestamps_by_partition.get(tp)
        if not timestamp_ledger:
            return

        metrics_exporter = self._get_metrics_exporter()
        observer = getattr(
            metrics_exporter,
            "observe_completion_to_commit_latency",
            None,
        )
        now = self._now()
        if callable(observer):
            engine_type = self._get_pipeline_engine_type()
            for offset, completed_at in tuple(timestamp_ledger.items()):
                if offset <= safe_offset:
                    observer(
                        engine_type=engine_type,
                        duration_seconds=max(0.0, now - completed_at),
                    )

        retained_offsets = (
            set(tracker.completed_offsets) if tracker is not None else set()
        )
        retained_timestamps = {
            offset: completed_at
            for offset, completed_at in timestamp_ledger.items()
            if offset in retained_offsets and offset > safe_offset
        }
        if retained_timestamps:
            self._unsettled_completion_timestamps_by_partition[tp] = retained_timestamps
        else:
            self._unsettled_completion_timestamps_by_partition.pop(tp, None)
