# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_poison_policy.py
# Role: Verifies immutable poison-policy snapshots and pure classification helpers.
# Extend here for control-plane-owned poison policy snapshot behavior.

import importlib

from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, OrderingMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem


def test_capture_poison_policy_snapshot_and_apply_ordered_prefix_truncation() -> None:
    # Given: the WorkManager-owned poison circuit has an open circuit for one key.
    now = 100.0
    tp = DtoTopicPartition("topic", 1)
    circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=30_000,
        forced_failure_attempt=3,
        clock=lambda: now,
    )
    poison_item = WorkItem(
        "poison",
        tp,
        43,
        7,
        b"same-route",
        b"bad",
        poison_key=b"poison-key",
    )
    circuit.record_completion(
        CompletionEvent(
            id=poison_item.id,
            tp=poison_item.tp,
            offset=poison_item.offset,
            epoch=poison_item.epoch,
            status=CompletionStatus.FAILURE,
            error="terminal",
            attempt=3,
        ),
        poison_item,
    )
    first_item = WorkItem("first", tp, 42, 7, b"same-route", b"ok")
    tail_item = WorkItem("tail", tp, 44, 7, b"same-route", b"later")
    poison_policy = importlib.import_module(
        "pyrallel_consumer.control_plane.poison_policy"
    )

    snapshot = poison_policy.capture_poison_policy_snapshot(circuit, now=now)
    decision = poison_policy.apply_poison_policy(
        [first_item, poison_item, tail_item],
        ordering_mode=OrderingMode.KEY_HASH,
        snapshot=snapshot,
    )

    # Then: classification is snapshot-only and keeps ordered tails unresolved.
    assert snapshot.forced_fail_keys == frozenset({(tp, b"poison-key")})
    assert decision.accepted_items == [first_item]
    assert [event.id for event in decision.forced_failure_events] == ["poison"]
    assert decision.forced_failure_events[0].attempt == 3
    assert decision.truncated_tail_items == [tail_item]
