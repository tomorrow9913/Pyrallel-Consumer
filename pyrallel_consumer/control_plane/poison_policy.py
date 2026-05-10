# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/poison_policy.py
# Role: Provides immutable poison-policy snapshots and pure batch classification.
# Extend here for engine-owned retry/deferred poison policy decisions.
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, OrderingMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem

PoisonPolicyKey = tuple[DtoTopicPartition, Any]


@dataclass(frozen=True)
class PoisonPolicySnapshot:
    """Immutable force-fail decisions captured from WorkManager-owned poison state."""

    forced_fail_keys: frozenset[PoisonPolicyKey]
    forced_failure_attempt: int
    reason_template: str
    captured_at: float | None = None


PoisonPolicySnapshotProvider = Callable[[], PoisonPolicySnapshot]


@dataclass(frozen=True)
class PoisonPolicyDecision:
    """Pure classification result for a pending ordered batch."""

    accepted_items: list[WorkItem]
    forced_failure_events: list[CompletionEvent]
    truncated_tail_items: list[WorkItem]


def disabled_poison_policy_snapshot() -> PoisonPolicySnapshot:
    """Return an immutable empty snapshot for absent or disabled circuits."""
    return PoisonPolicySnapshot(
        forced_fail_keys=frozenset(),
        forced_failure_attempt=1,
        reason_template="Poison message circuit open for {topic_partition}@{offset} key",
        captured_at=None,
    )


def _poison_key(work_item: WorkItem) -> PoisonPolicyKey:
    """Return the immutable poison-policy lookup key for a work item."""
    circuit_key = PoisonMessageCircuitBreaker._circuit_key(work_item)
    return (circuit_key[0], circuit_key[1])


def capture_poison_policy_snapshot(
    circuit: PoisonMessageCircuitBreaker | None,
    *,
    now: float,
) -> PoisonPolicySnapshot:
    """Capture immutable force-fail state from the live WorkManager-owned circuit."""
    if circuit is None or not circuit.enabled:
        return disabled_poison_policy_snapshot()

    forced_fail_keys: set[PoisonPolicyKey] = set()
    for circuit_key, state in list(circuit._states.items()):
        open_until = state.open_until
        if open_until is None:
            continue
        if now < open_until:
            forced_fail_keys.add(circuit_key)
        else:
            circuit._states.pop(circuit_key, None)

    return PoisonPolicySnapshot(
        forced_fail_keys=frozenset(forced_fail_keys),
        forced_failure_attempt=circuit._forced_failure_attempt,
        reason_template=(
            "Poison message circuit open for {topic_partition}@{offset} key"
        ),
        captured_at=now,
    )


def apply_poison_policy(
    items: list[WorkItem],
    *,
    ordering_mode: OrderingMode,
    snapshot: PoisonPolicySnapshot,
) -> PoisonPolicyDecision:
    """Classify pending items using only an immutable poison-policy snapshot."""
    accepted_items: list[WorkItem] = []
    forced_failure_events: list[CompletionEvent] = []
    truncated_tail_items: list[WorkItem] = []

    for index, item in enumerate(items):
        if _poison_key(item) not in snapshot.forced_fail_keys:
            accepted_items.append(item)
            continue

        forced_failure_events.append(
            CompletionEvent(
                id=item.id,
                tp=item.tp,
                offset=item.offset,
                epoch=item.epoch,
                status=CompletionStatus.FAILURE,
                error=snapshot.reason_template.format(
                    topic_partition=item.tp,
                    offset=item.offset,
                ),
                attempt=snapshot.forced_failure_attempt,
            )
        )
        if ordering_mode in (OrderingMode.KEY_HASH, OrderingMode.PARTITION):
            truncated_tail_items = items[index + 1 :]
            break

    return PoisonPolicyDecision(
        accepted_items=accepted_items,
        forced_failure_events=forced_failure_events,
        truncated_tail_items=truncated_tail_items,
    )
