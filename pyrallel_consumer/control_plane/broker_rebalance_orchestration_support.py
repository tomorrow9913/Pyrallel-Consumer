# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_rebalance_orchestration_support.py
# Role: Coordinates BrokerPoller rebalance bridge, tracker install, and revoke cleanup.
# Extend here for assign/revoke callback orchestration; keep offset hydration policies elsewhere.
from __future__ import annotations

from collections.abc import Callable, MutableMapping
from typing import Any

from confluent_kafka import Consumer, KafkaException
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.broker_rebalance_bridge import (
    BrokerRebalanceBridge,
    RevokePreparation,
)
from pyrallel_consumer.control_plane.broker_rebalance_support import (
    BrokerRebalanceSupport,
)
from pyrallel_consumer.control_plane.commit_coordinator import CommitCandidate
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class BrokerRebalanceOrchestrationSupport:
    """Coordinate callback bridge orchestration for BrokerPoller rebalances."""

    def __init__(
        self,
        *,
        rebalance_support: BrokerRebalanceSupport,
        rebalance_bridge: BrokerRebalanceBridge,
        get_rebalance_state_strategy: Callable[[], str],
        get_max_revoke_grace_ms: Callable[[], int],
        get_commit_coordinator: Callable[[], Any | None],
        get_work_manager: Callable[[], Any],
        get_offset_trackers: Callable[
            [], MutableMapping[DtoTopicPartition, OffsetTracker]
        ],
        get_dirty_commit_partitions: Callable[[], set[DtoTopicPartition]],
        get_unsettled_completions_by_partition: Callable[
            [], MutableMapping[DtoTopicPartition, int]
        ],
        get_unsettled_completion_timestamps_by_partition: Callable[
            [], MutableMapping[DtoTopicPartition, dict[int, float]]
        ],
        get_pending_dlq_events: Callable[
            [], MutableMapping[tuple[DtoTopicPartition, int], CompletionEvent]
        ],
        drop_cached_partition_messages: Callable[[DtoTopicPartition], None],
        encode_revoke_metadata: Callable[[OffsetTracker, int], str],
        record_commit_failure_for_partition: Callable[[DtoTopicPartition, str], None],
        consumer_operation_guard: Any,
        logger: Any,
    ) -> None:
        """Initialize rebalance orchestration support."""
        self._rebalance_support = rebalance_support
        self._rebalance_bridge = rebalance_bridge
        self._get_rebalance_state_strategy = get_rebalance_state_strategy
        self._get_max_revoke_grace_ms = get_max_revoke_grace_ms
        self._get_commit_coordinator = get_commit_coordinator
        self._get_work_manager = get_work_manager
        self._get_offset_trackers = get_offset_trackers
        self._get_dirty_commit_partitions = get_dirty_commit_partitions
        self._get_unsettled_completions_by_partition = (
            get_unsettled_completions_by_partition
        )
        self._get_unsettled_completion_timestamps_by_partition = (
            get_unsettled_completion_timestamps_by_partition
        )
        self._get_pending_dlq_events = get_pending_dlq_events
        self._drop_cached_partition_messages = drop_cached_partition_messages
        self._encode_revoke_metadata = encode_revoke_metadata
        self._record_commit_failure_for_partition = record_commit_failure_for_partition
        self._consumer_operation_guard = consumer_operation_guard
        self._logger = logger

    def handle_assign_callback(
        self,
        *,
        consumer: Consumer,
        partitions: list[KafkaTopicPartition],
        assign_from_callback: Callable[[Consumer, list[KafkaTopicPartition]], bool],
    ) -> None:
        """Handle assign callback logging and bridge failure escalation."""
        self._logger.debug(
            "Partitions assigned: %s",
            ", ".join(f"{tp.topic}-{tp.partition}@{tp.offset}" for tp in partitions),
        )

        if not assign_from_callback(consumer, partitions):
            raise RuntimeError("Assign bridge failed")

    def assign_from_callback(
        self,
        *,
        consumer: Consumer,
        partitions: list[KafkaTopicPartition],
    ) -> bool:
        """Build assignment state off-loop and install it through the bridge."""
        work_manager_assignments = self._rebalance_support.build_assignments(
            consumer=consumer,
            partitions=partitions,
            strategy=self._get_rebalance_state_strategy(),
            max_revoke_grace_ms=self._get_max_revoke_grace_ms(),
            logger=self._logger,
        )
        return self._rebalance_bridge.assign_from_callback(work_manager_assignments)

    def assign_sync(
        self, work_manager_assignments: dict[DtoTopicPartition, OffsetTracker]
    ) -> None:
        """Install assignment trackers and notify WorkManager."""
        commit_coordinator = self._get_commit_coordinator()
        if commit_coordinator is not None:
            commit_coordinator.start_accepting_partitions(
                work_manager_assignments.keys()
            )
        self._get_offset_trackers().update(work_manager_assignments)
        self._get_work_manager().on_assign(work_manager_assignments)

    def handle_revoke_callback(
        self,
        *,
        consumer: Consumer,
        partitions: list[KafkaTopicPartition],
        prepare_revoke_from_callback: Callable[
            [list[KafkaTopicPartition]], RevokePreparation | None
        ],
        cleanup_revoke_from_callback: Callable[
            [list[DtoTopicPartition], list[DtoTopicPartition]], bool
        ],
    ) -> None:
        """Handle revoke callback bridge phases and commit failure accounting."""
        self._logger.warning(
            "Partitions revoked: %s",
            ", ".join(f"{tp.topic}-{tp.partition}" for tp in partitions),
        )

        preparation = prepare_revoke_from_callback(partitions)
        if preparation is None:
            self.record_commit_failure_for_rebalance_bridge(partitions)
            raise RuntimeError("Revoke bridge failed")

        failed_tps: list[DtoTopicPartition] = []
        if preparation.offsets_to_commit:
            failed_tps = self.commit_prepared_revoke_offsets(
                consumer=consumer,
                offsets_to_commit=preparation.offsets_to_commit,
            )

        if not cleanup_revoke_from_callback(preparation.revoked_tps, failed_tps):
            self.record_commit_failure_for_rebalance_bridge(partitions)
            raise RuntimeError("Revoke bridge failed")

    def prepare_revoke_from_callback(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation | None:
        """Bridge revoke preparation onto the event loop."""
        return self._rebalance_bridge.prepare_revoke_from_callback(partitions)

    def prepare_revoke_sync(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation:
        """Prepare revoke payloads and state transitions under control lock."""
        revoked_tps = [
            DtoTopicPartition(
                topic=str(partition.topic), partition=int(partition.partition)
            )
            for partition in partitions
        ]
        coordinator_candidates: dict[DtoTopicPartition, CommitCandidate] = {}
        commit_coordinator = self._get_commit_coordinator()
        if commit_coordinator is not None:
            coordinator_candidates = commit_coordinator.remaining_candidates(
                revoked_tps
            )
            commit_coordinator.stop_accepting_partitions(revoked_tps)
        self._get_work_manager().on_revoke(revoked_tps)

        offsets_to_commit: list[KafkaTopicPartition] = []
        offset_trackers = self._get_offset_trackers()
        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(tp_kafka.topic, tp_kafka.partition)
            self._drop_cached_partition_messages(tp_dto)
            tracker = offset_trackers.get(tp_dto)
            if tracker is None:
                continue
            tracker.advance_high_water_mark()
            safe_offset = tracker.last_committed_offset
            coordinator_candidate = coordinator_candidates.get(tp_dto)
            if (
                coordinator_candidate is not None
                and coordinator_candidate.assignment_epoch
                == tracker.get_current_epoch()
            ):
                safe_offset = max(safe_offset, coordinator_candidate.safe_offset)
            if safe_offset < 0:
                continue
            metadata = self._encode_revoke_metadata(tracker, safe_offset + 1)
            offsets_to_commit.append(
                KafkaTopicPartition(
                    tp_dto.topic,
                    tp_dto.partition,
                    safe_offset + 1,
                    metadata=metadata,  # type: ignore[call-arg]
                )
            )
        return RevokePreparation(
            revoked_tps=revoked_tps, offsets_to_commit=offsets_to_commit
        )

    def commit_prepared_revoke_offsets(
        self,
        *,
        consumer: Consumer,
        offsets_to_commit: list[KafkaTopicPartition],
    ) -> list[DtoTopicPartition]:
        """Commit prepared revoke offsets under the broker operation guard."""
        failed_tps: list[DtoTopicPartition] = []
        for offset in offsets_to_commit:

            def commit_offset(offset: KafkaTopicPartition = offset) -> None:
                """Commit one revoke offset synchronously."""
                consumer.commit(offsets=[offset], asynchronous=False)

            try:
                self._consumer_operation_guard.run(commit_offset)
            except KafkaException as exc:
                failed_tp = DtoTopicPartition(str(offset.topic), int(offset.partition))
                failed_tps.append(failed_tp)
                self._logger.warning(
                    "Revoke commit failed for %s-%d at offset %d: %s",
                    failed_tp.topic,
                    failed_tp.partition,
                    int(offset.offset),
                    exc,
                )
        return failed_tps

    def cleanup_revoke_from_callback(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> bool:
        """Bridge revoke cleanup onto the event loop."""
        return self._rebalance_bridge.cleanup_revoke_from_callback(
            revoked_tps, failed_tps
        )

    def cleanup_revoke_sync(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> None:
        """Remove revoked partition state after broker revoke commit finishes."""
        for failed_tp in failed_tps:
            self._record_commit_failure_for_partition(failed_tp, "kafka_exception")
        dirty_commit_partitions = self._get_dirty_commit_partitions()
        offset_trackers = self._get_offset_trackers()
        unsettled_completions = self._get_unsettled_completions_by_partition()
        unsettled_timestamps = self._get_unsettled_completion_timestamps_by_partition()
        pending_dlq_events = self._get_pending_dlq_events()
        for revoked_tp in revoked_tps:
            dirty_commit_partitions.discard(revoked_tp)
            offset_trackers.pop(revoked_tp, None)
            unsettled_completions.pop(revoked_tp, None)
            unsettled_timestamps.pop(revoked_tp, None)
            for pending_key in list(pending_dlq_events):
                pending_tp, _ = pending_key
                if pending_tp == revoked_tp:
                    pending_dlq_events.pop(pending_key, None)

    def record_commit_failure_for_rebalance_bridge(
        self, partitions: list[KafkaTopicPartition]
    ) -> None:
        """Record replay-safe failures when rebalance bridge phases fail."""
        for partition in partitions:
            self._record_commit_failure_for_partition(
                DtoTopicPartition(str(partition.topic), int(partition.partition)),
                "rebalance_bridge_failed",
            )
