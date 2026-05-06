from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from confluent_kafka import KafkaException

from pyrallel_consumer.config import CommitCoordinatorConfig
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition

COMMIT_COORDINATOR_FAILURE_REASONS = (
    "kafka_exception",
    "queue_full",
    "worker_crash",
    "stale_lease",
    "shutdown_timeout",
    "rebalance_bridge_failed",
    "close_commit_failed",
)

CommitLeaseId = int


@dataclass(frozen=True)
class CommitCandidate:
    tp: DtoTopicPartition
    safe_offset: int
    assignment_epoch: int
    lease_id: CommitLeaseId
    enqueued_at: float


@dataclass(frozen=True)
class CommitSettlement:
    tp: DtoTopicPartition
    safe_offset: int
    assignment_epoch: int
    lease_id: CommitLeaseId
    success: bool
    reason: str | None
    latency_seconds: float


@dataclass(frozen=True)
class CommitCoordinatorStats:
    queue_depth: int = 0
    coalesced_count: int = 0
    submitted_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    retry_count: int = 0
    max_pending_age: float = 0.0


MetricCallback = Callable[[str, str | None, int, float | None], None]


@dataclass
class CommitCoordinator:
    config: CommitCoordinatorConfig
    commit_sync: Callable[[list[CommitCandidate]], Awaitable[None]]
    on_commit_success: Callable[[list[CommitSettlement]], object]
    on_commit_failure: Callable[[list[CommitSettlement], str], object]
    record_metrics: MetricCallback
    _pending: dict[DtoTopicPartition, CommitCandidate] = field(default_factory=dict)
    _in_flight: dict[DtoTopicPartition, CommitCandidate] = field(default_factory=dict)
    _cancelled_leases: set[tuple[DtoTopicPartition, int, CommitLeaseId]] = field(
        default_factory=set
    )
    _latest_settled_offsets: dict[DtoTopicPartition, int] = field(default_factory=dict)
    _lease_counter: int = 0
    _worker_task: asyncio.Task[None] | None = None
    _accepting: bool = True
    _healthy: bool = True
    _coalesced_count: int = 0
    _submitted_count: int = 0
    _success_count: int = 0
    _failure_count: int = 0
    _retry_count: int = 0

    @property
    def accepting(self) -> bool:
        return self._accepting

    @property
    def healthy(self) -> bool:
        return self._healthy

    @property
    def latest_settled_offsets(self) -> dict[DtoTopicPartition, int]:
        return dict(self._latest_settled_offsets)

    @property
    def stats(self) -> CommitCoordinatorStats:
        now = time.monotonic()
        ages = [now - candidate.enqueued_at for candidate in self._pending.values()]
        in_flight_depth = sum(
            1
            for candidate in self._in_flight.values()
            if candidate.safe_offset
            > self._latest_settled_offsets.get(candidate.tp, -1)
        )
        return CommitCoordinatorStats(
            queue_depth=len(self._pending) + in_flight_depth,
            coalesced_count=self._coalesced_count,
            submitted_count=self._submitted_count,
            success_count=self._success_count,
            failure_count=self._failure_count,
            retry_count=self._retry_count,
            max_pending_age=max(ages) if ages else 0.0,
        )

    async def enqueue(
        self,
        candidates: Iterable[CommitCandidate],
        *,
        force: bool = False,
        source: str = "unknown",
    ) -> bool:
        del force, source
        if not self._accepting or not self._healthy:
            return False

        changed = False
        for candidate in candidates:
            latest_settled = self._latest_settled_offsets.get(candidate.tp, -1)
            if candidate.safe_offset <= latest_settled:
                continue
            current = self._pending.get(candidate.tp)
            if current is not None and candidate.safe_offset <= current.safe_offset:
                continue
            if current is None and candidate.tp not in self._pending:
                projected_size = len(self._pending) + len(self._in_flight)
                if candidate.tp not in self._in_flight:
                    projected_size += 1
                if projected_size > self.config.queue_max_partitions:
                    self._failure_count += 1
                    self.record_metrics("failure", "queue_full", 1, None)
                    return False
            if current is not None:
                self._cancelled_leases.add(
                    (current.tp, current.assignment_epoch, current.lease_id)
                )
                self._coalesced_count += 1
                self.record_metrics("coalesced", None, 1, None)
            self._lease_counter += 1
            self._pending[candidate.tp] = CommitCandidate(
                tp=candidate.tp,
                safe_offset=candidate.safe_offset,
                assignment_epoch=candidate.assignment_epoch,
                lease_id=self._lease_counter,
                enqueued_at=time.monotonic(),
            )
            changed = True
            self._prune_cancelled_leases()

        if changed:
            self._ensure_worker()
        return True

    def stop_accepting(self) -> None:
        self._accepting = False

    def stop_accepting_partitions(self, tps: Iterable[DtoTopicPartition]) -> None:
        self.cancel_leases(tps)

    def cancel_leases(self, tps: Iterable[DtoTopicPartition]) -> None:
        for tp in tps:
            for source in (self._pending, self._in_flight):
                candidate = source.pop(tp, None)
                if candidate is not None:
                    self._cancelled_leases.add(
                        (candidate.tp, candidate.assignment_epoch, candidate.lease_id)
                    )
        self._prune_cancelled_leases()

    def remaining_candidates(
        self, tps: Iterable[DtoTopicPartition] | None = None
    ) -> dict[DtoTopicPartition, CommitCandidate]:
        allowed = set(tps) if tps is not None else None
        remaining = dict(self._pending)
        remaining.update(self._in_flight)
        if allowed is None:
            return remaining
        return {tp: candidate for tp, candidate in remaining.items() if tp in allowed}

    def is_active_lease(
        self, tp: DtoTopicPartition, assignment_epoch: int, lease_id: CommitLeaseId
    ) -> bool:
        candidate = self._pending.get(tp) or self._in_flight.get(tp)
        if candidate is None:
            return False
        return (
            candidate.assignment_epoch == assignment_epoch
            and candidate.lease_id == lease_id
            and (tp, assignment_epoch, lease_id) not in self._cancelled_leases
        )

    async def drain(
        self,
        timeout: float,
        tps: Iterable[DtoTopicPartition] | None = None,
    ) -> bool:
        deadline = time.monotonic() + max(0.0, timeout)
        while self.remaining_candidates(tps):
            task = self._worker_task
            if task is None or task.done():
                if self._pending and self._healthy:
                    self._ensure_worker()
                else:
                    break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            await asyncio.sleep(min(remaining, 0.01))
        return not self.remaining_candidates(tps)

    def _ensure_worker(self) -> None:
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._worker_task = asyncio.create_task(self._run_worker())

    async def _run_worker(self) -> None:
        while self._pending and self._healthy:
            batch = list(self._pending.values())
            self._pending.clear()
            self._in_flight = {candidate.tp: candidate for candidate in batch}
            started_at = time.monotonic()
            try:
                self._submitted_count += len(batch)
                self.record_metrics("submitted", None, len(batch), None)
                await self.commit_sync(batch)
            except KafkaException:
                latency = max(0.0, time.monotonic() - started_at)
                settlements = self._failure_settlements(
                    batch,
                    reason="kafka_exception",
                    latency_seconds=latency,
                )
                self._retry_count += len(batch)
                self.record_metrics("retry", "kafka_exception", len(batch), None)
                if settlements:
                    await self._invoke_failure_safely(settlements, "kafka_exception")
                for candidate in batch:
                    if self.is_active_lease(
                        candidate.tp, candidate.assignment_epoch, candidate.lease_id
                    ):
                        self._pending[candidate.tp] = candidate
                self._in_flight.clear()
                self._prune_cancelled_leases()
                await asyncio.sleep(self._retry_backoff_seconds())
                continue
            except Exception:
                self._healthy = False
                self._accepting = False
                self._failure_count += len(batch)
                latency = max(0.0, time.monotonic() - started_at)
                settlements = self._failure_settlements(
                    batch,
                    reason="worker_crash",
                    latency_seconds=latency,
                )
                if settlements:
                    await self._invoke_failure_safely(settlements, "worker_crash")
                for candidate in batch:
                    self._cancelled_leases.add(
                        (candidate.tp, candidate.assignment_epoch, candidate.lease_id)
                    )
                self._in_flight.clear()
                self._prune_cancelled_leases()
                self.record_metrics("failure", "worker_crash", len(batch), None)
                return

            latency = max(0.0, time.monotonic() - started_at)
            settlements = [
                CommitSettlement(
                    tp=candidate.tp,
                    safe_offset=candidate.safe_offset,
                    assignment_epoch=candidate.assignment_epoch,
                    lease_id=candidate.lease_id,
                    success=True,
                    reason=None,
                    latency_seconds=latency,
                )
                for candidate in batch
                if self.is_active_lease(
                    candidate.tp, candidate.assignment_epoch, candidate.lease_id
                )
            ]
            if settlements:
                self._success_count += len(settlements)
                for settlement in settlements:
                    self._latest_settled_offsets[settlement.tp] = settlement.safe_offset
                success_recorded = False
                try:
                    await self._invoke_success(settlements)
                    success_recorded = True
                except Exception:
                    self._healthy = False
                    self._accepting = False
                    self._failure_count += len(settlements)
                    self.record_metrics(
                        "failure", "worker_crash", len(settlements), None
                    )
                finally:
                    self._in_flight.clear()
                    self._prune_cancelled_leases()
                    if success_recorded:
                        self.record_metrics("success", None, len(settlements), latency)
            else:
                self._in_flight.clear()
                self._prune_cancelled_leases()

    def _failure_settlements(
        self,
        candidates: list[CommitCandidate],
        *,
        reason: str,
        latency_seconds: float,
    ) -> list[CommitSettlement]:
        return [
            CommitSettlement(
                tp=candidate.tp,
                safe_offset=candidate.safe_offset,
                assignment_epoch=candidate.assignment_epoch,
                lease_id=candidate.lease_id,
                success=False,
                reason=reason,
                latency_seconds=latency_seconds,
            )
            for candidate in candidates
            if self.is_active_lease(
                candidate.tp, candidate.assignment_epoch, candidate.lease_id
            )
        ]

    async def _invoke_success(self, settlements: list[CommitSettlement]) -> None:
        result = self.on_commit_success(settlements)
        if inspect.isawaitable(result):
            await result

    async def _invoke_failure(
        self, settlements: list[CommitSettlement], reason: str
    ) -> None:
        result = self.on_commit_failure(settlements, reason)
        if inspect.isawaitable(result):
            await result

    async def _invoke_failure_safely(
        self, settlements: list[CommitSettlement], reason: str
    ) -> None:
        try:
            await self._invoke_failure(settlements, reason)
        except Exception:
            self._healthy = False
            self._accepting = False
            self._failure_count += len(settlements)
            self.record_metrics("failure", "worker_crash", len(settlements), None)

    def _prune_cancelled_leases(self) -> None:
        active_keys = {
            (candidate.tp, candidate.assignment_epoch, candidate.lease_id)
            for candidate in (*self._pending.values(), *self._in_flight.values())
        }
        self._cancelled_leases.intersection_update(active_keys)

    def _retry_backoff_seconds(self) -> float:
        backoff_ms = min(self.config.retry_backoff_ms, self.config.max_retry_backoff_ms)
        return max(0.0, backoff_ms / 1000.0)
