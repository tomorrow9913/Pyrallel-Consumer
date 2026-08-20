from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from ..dto import TopicPartition as DtoTopicPartition
from ..logger import LogManager
from .broker_operation_guard import BrokerOperationGuard
from .broker_support import BrokerCommitPlanner
from .commit_coordinator import (
    CommitBatchAborted,
    CommitCandidate,
    CommitCoordinator,
    CommitSettlement,
)
from .offset_tracker import OffsetTracker

logger = LogManager.get_logger(__name__)


class BrokerCommitCoordinatorSupport:
    """BrokerPoller adapter for async commit-coordinator integration."""

    def __init__(
        self,
        *,
        control_lock: asyncio.Lock,
        offset_trackers: MutableMapping[DtoTopicPartition, OffsetTracker],
        dirty_commit_partitions: set[DtoTopicPartition],
        commit_planner: BrokerCommitPlanner,
        operation_guard: BrokerOperationGuard,
        get_consumer: Callable[[], Any | None],
        get_coordinator: Callable[[], CommitCoordinator | None],
        rebalance_state_strategy: Callable[[], Any],
        clear_committed_dirty_partitions: Callable[
            [list[tuple[DtoTopicPartition, int]]], None
        ],
        record_commit_failure: Callable[
            [list[tuple[DtoTopicPartition, int]], str], None
        ],
        record_pending_partitions: Callable[[], None],
        kafka_exception_reason: str,
    ) -> None:
        self._control_lock = control_lock
        self._offset_trackers = offset_trackers
        self._dirty_commit_partitions = dirty_commit_partitions
        self._commit_planner = commit_planner
        self._operation_guard = operation_guard
        self._get_consumer = get_consumer
        self._get_coordinator = get_coordinator
        self._rebalance_state_strategy = rebalance_state_strategy
        self._clear_committed_dirty_partitions = clear_committed_dirty_partitions
        self._record_commit_failure = record_commit_failure
        self._record_pending_partitions = record_pending_partitions
        self._kafka_exception_reason = kafka_exception_reason

    async def build_candidates(
        self, commits_to_make: list[tuple[DtoTopicPartition, int]]
    ) -> list[CommitCandidate]:
        """Build enriched coordinator candidates from tracker state."""
        async with self._control_lock:
            candidates: list[CommitCandidate] = []
            now = time.monotonic()
            for tp, safe_offset in commits_to_make:
                tracker = self._offset_trackers.get(tp)
                if tracker is None:
                    continue
                candidates.append(
                    CommitCandidate(
                        tp=tp,
                        safe_offset=safe_offset,
                        assignment_epoch=tracker.get_current_epoch(),
                        lease_id=0,
                        enqueued_at=now,
                    )
                )
            return candidates

    async def commit_sync(self, candidates: list[CommitCandidate]) -> None:
        """Submit coordinator candidates to the broker after lease revalidation."""
        consumer = self._get_consumer()
        if consumer is None:
            raise RuntimeError("Kafka consumer must be initialized")
        async with self._control_lock:
            tracked_commits: list[tuple[DtoTopicPartition, int]] = []
            tracker_snapshot: dict[DtoTopicPartition, OffsetTracker] = {}
            tracked_candidates: list[CommitCandidate] = []
            stale_tps: list[DtoTopicPartition] = []
            for candidate in candidates:
                tracker = self._offset_trackers.get(candidate.tp)
                if tracker is None:
                    stale_tps.append(candidate.tp)
                    continue
                if tracker.get_current_epoch() != candidate.assignment_epoch:
                    stale_tps.append(candidate.tp)
                    continue
                tracked_commits.append((candidate.tp, candidate.safe_offset))
                tracker_snapshot[candidate.tp] = tracker
                tracked_candidates.append(candidate)
            coordinator = self._get_coordinator()
            if coordinator is not None and stale_tps:
                coordinator.cancel_leases(stale_tps)

        if not tracked_commits:
            return

        offsets_to_commit = self._commit_planner.build_offsets_to_commit(
            commits_to_make=tracked_commits,
            trackers=tracker_snapshot,
            strategy=self._rebalance_state_strategy(),
        )

        def commit_if_active() -> bool:
            """Commit only if every candidate lease still belongs to this batch."""
            coordinator = self._get_coordinator()
            if coordinator is None:
                return False
            for candidate in tracked_candidates:
                if not coordinator.is_active_lease(
                    candidate.tp,
                    candidate.assignment_epoch,
                    candidate.lease_id,
                ):
                    return False
            consumer.commit(offsets=offsets_to_commit, asynchronous=False)
            return True

        if not await self._operation_guard.run_off_event_loop(commit_if_active):
            coordinator = self._get_coordinator()
            if coordinator is not None:
                coordinator.cancel_leases(
                    [candidate.tp for candidate in tracked_candidates]
                )
            raise CommitBatchAborted("coordinator lease became inactive before commit")

    async def settle_committed_offsets(
        self, settlements: list[CommitSettlement]
    ) -> None:
        """Advance trackers only for successful active-lease settlements."""
        commits_to_clear: list[tuple[DtoTopicPartition, int]] = []
        async with self._control_lock:
            for settlement in settlements:
                tracker = self._offset_trackers.get(settlement.tp)
                if tracker is None:
                    continue
                if tracker.get_current_epoch() != settlement.assignment_epoch:
                    continue
                coordinator = self._get_coordinator()
                if coordinator is None or not coordinator.is_active_lease(
                    settlement.tp,
                    settlement.assignment_epoch,
                    settlement.lease_id,
                ):
                    continue
                if settlement.safe_offset <= tracker.last_committed_offset:
                    continue
                tracker.commit_through(settlement.safe_offset)
                commits_to_clear.append((settlement.tp, settlement.safe_offset))
        if commits_to_clear:
            self._clear_committed_dirty_partitions(commits_to_clear)
        self._record_pending_partitions()

    async def retain_failed_commit_offsets(
        self, settlements: list[CommitSettlement], reason: str
    ) -> None:
        """Retain dirty retry intent for failed active-lease settlements."""
        async with self._control_lock:
            for settlement in settlements:
                coordinator = self._get_coordinator()
                if coordinator is None or not coordinator.is_active_lease(
                    settlement.tp,
                    settlement.assignment_epoch,
                    settlement.lease_id,
                ):
                    continue
                self._dirty_commit_partitions.add(settlement.tp)
                if reason != self._kafka_exception_reason:
                    self._record_commit_failure(
                        [(settlement.tp, settlement.safe_offset)],
                        reason,
                    )
        self._record_pending_partitions()

    async def drain_or_sync_fallback_for_shutdown(
        self,
        *,
        deadline: float,
        timeout_ms: int,
        sync_fallback: Callable[[list[tuple[DtoTopicPartition, int]]], Awaitable[bool]],
    ) -> bool:
        """Drain coordinator work, then route remaining candidates to sync fallback."""
        coordinator = self._get_coordinator()
        if coordinator is None:
            return True
        coordinator.stop_accepting()
        remaining_seconds = max(0.0, deadline - time.monotonic())
        drain_timeout = min(remaining_seconds, max(0.0, timeout_ms / 1000.0))
        if await coordinator.drain(timeout=drain_timeout):
            return True
        remaining = coordinator.remaining_candidates()
        coordinator.cancel_leases(remaining.keys())
        if remaining and self._get_consumer() is not None:
            return await sync_fallback(
                [
                    (candidate.tp, candidate.safe_offset)
                    for candidate in remaining.values()
                ]
            )
        return not remaining


class BrokerCommitCadenceSupport:
    """Track commit cadence gates and diagnostic counters for BrokerPoller."""

    def __init__(
        self,
        *,
        get_dirty_commit_partitions: Callable[[], set[DtoTopicPartition]],
        has_pending_dlq_events: Callable[[], bool],
        get_total_in_flight_count: Callable[[], int],
        get_total_queued_messages: Callable[[], Awaitable[int]],
        now: Callable[[], float] = time.monotonic,
    ) -> None:
        """Initialize commit cadence support."""
        self._get_dirty_commit_partitions = get_dirty_commit_partitions
        self._has_pending_dlq_events = has_pending_dlq_events
        self._get_total_in_flight_count = get_total_in_flight_count
        self._get_total_queued_messages = get_total_queued_messages
        self._now = now
        self._invocations_total = 0
        self._empty_candidate_scans_total = 0
        self._commit_calls_total = 0
        self._partitions_advanced_total = 0
        self._invocations_by_source: dict[str, int] = {}
        self._empty_candidate_scans_by_source: dict[str, int] = {}
        self._commit_calls_by_source: dict[str, int] = {}
        self._partitions_advanced_by_source: dict[str, int] = {}

    def record_invocation(self, source: str) -> None:
        """Record that commit-ready evaluation was invoked."""
        self._invocations_total += 1
        self._invocations_by_source[source] = (
            self._invocations_by_source.get(source, 0) + 1
        )

    def record_empty_candidate_scan(self, source: str) -> None:
        """Record a cadence-gated commit scan that produced no commit attempt."""
        self._empty_candidate_scans_total += 1
        self._empty_candidate_scans_by_source[source] = (
            self._empty_candidate_scans_by_source.get(source, 0) + 1
        )

    def record_commit_success(self, source: str, partition_count: int) -> None:
        """Record a successful synchronous commit attempt."""
        self._commit_calls_total += 1
        self._commit_calls_by_source[source] = (
            self._commit_calls_by_source.get(source, 0) + 1
        )
        self._partitions_advanced_total += partition_count
        self._partitions_advanced_by_source[source] = (
            self._partitions_advanced_by_source.get(source, 0) + partition_count
        )

    def get_stats(self) -> dict[str, Any]:
        """Return commit cadence diagnostic counters."""
        return {
            "invocations_total": self._invocations_total,
            "empty_candidate_scans_total": self._empty_candidate_scans_total,
            "commit_calls_total": self._commit_calls_total,
            "partitions_advanced_total": self._partitions_advanced_total,
            "invocations_by_source": dict(self._invocations_by_source),
            "empty_candidate_scans_by_source": dict(
                self._empty_candidate_scans_by_source
            ),
            "commit_calls_by_source": dict(self._commit_calls_by_source),
            "partitions_advanced_by_source": dict(self._partitions_advanced_by_source),
        }

    def should_attempt_ready_commit(
        self,
        *,
        completions_since_last_commit: int,
        completion_threshold: int,
        interval_seconds: float,
        last_attempt_monotonic: float,
    ) -> bool:
        """Return whether ready offsets should be scanned for commit."""
        if not self._get_dirty_commit_partitions():
            return False
        if completions_since_last_commit >= completion_threshold:
            return True
        if interval_seconds <= 0:
            return True
        elapsed = self._now() - last_attempt_monotonic
        return elapsed >= interval_seconds

    async def should_force_idle_commit(self) -> bool:
        """Return whether idle broker state should force a final commit scan."""
        if not self._get_dirty_commit_partitions():
            return False
        if self._has_pending_dlq_events():
            return False
        if self._get_total_in_flight_count() > 0:
            return False
        return await self._get_total_queued_messages() <= 0
