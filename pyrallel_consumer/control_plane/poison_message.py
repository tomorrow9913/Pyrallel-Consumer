# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/poison_message.py
# Role: Detects repeated poison-message failures and decides when to force-fail matching work.
# Extend here for poison-message circuit policy; keep DLQ publication elsewhere.
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from pyrallel_consumer.dto import (
    WORK_ITEM_POISON_KEY_UNSET,
    CompletionEvent,
    CompletionStatus,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem

CircuitKey = tuple[DtoTopicPartition, Any]


@dataclass
class _CircuitState:
    """Represent circuit state data used by poison-message protection."""

    failure_count: int = 0
    open_until: Optional[float] = None


class PoisonMessageCircuitBreaker:
    """Represent poison message circuit breaker data used by poison-message protection."""

    def __init__(
        self,
        *,
        enabled: bool,
        failure_threshold: int,
        cooldown_ms: int,
        forced_failure_attempt: int,
        clock: Any = None,
    ) -> None:
        """Initialize this component.

        Args:
            enabled: Whether the feature is enabled.
            failure_threshold: Failures needed before opening the circuit.
            cooldown_ms: Circuit cooldown in milliseconds.
            forced_failure_attempt: Attempt number from which forced failure may be emitted.
            clock: Clock callable used for deterministic tests.

        Raises:
            ValueError: If initialization receives invalid configuration.

        """
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if cooldown_ms < 0:
            raise ValueError("cooldown_ms must be >= 0")
        self.enabled = enabled
        self.failure_threshold = failure_threshold
        self.cooldown_ms = cooldown_ms
        self._cooldown_seconds = cooldown_ms / 1000.0
        self._forced_failure_attempt = max(1, int(forced_failure_attempt))
        self._clock = clock or time.monotonic
        self._states: dict[CircuitKey, _CircuitState] = {}

    def record_completion(self, event: CompletionEvent, work_item: WorkItem) -> None:
        """Record completion for poison-message protection.

        Args:
            event: Completion or registry event being processed.
            work_item: Work item being scheduled or processed.

        """
        if not self.enabled:
            return

        circuit_key = self._circuit_key(work_item)
        if event.status == CompletionStatus.SUCCESS:
            self._states.pop(circuit_key, None)
            return

        if event.attempt < self._forced_failure_attempt:
            return

        state = self._states.setdefault(circuit_key, _CircuitState())
        state.failure_count += 1
        if state.failure_count >= self.failure_threshold:
            state.open_until = self._clock() + self._cooldown_seconds

    def should_force_fail(self, work_item: WorkItem) -> bool:
        """Return whether force fail should run in poison-message protection.

        Args:
            work_item: Work item being scheduled or processed.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        """
        return self._should_force_fail(work_item, prune_expired=True)

    def would_force_fail(self, work_item: WorkItem) -> bool:
        """Return force-fail eligibility without mutating circuit state.

        Args:
            work_item: Work item being inspected.

        Returns:
            True when the work item currently matches an open circuit.

        """
        return self._should_force_fail(work_item, prune_expired=False)

    def _should_force_fail(self, work_item: WorkItem, *, prune_expired: bool) -> bool:
        """Return force-fail eligibility with optional expired-state cleanup."""
        if not self.enabled:
            return False

        circuit_key = self._circuit_key(work_item)
        state = self._states.get(circuit_key)
        if state is None or state.open_until is None:
            return False

        now = self._clock()
        if now < state.open_until:
            return True

        if prune_expired:
            self._states.pop(circuit_key, None)
        return False

    def build_forced_failure_event(self, work_item: WorkItem) -> CompletionEvent:
        """Build forced failure event for poison-message protection.

        Args:
            work_item: Work item being scheduled or processed.

        Returns:
            Completion event produced by the operation.

        """
        state = self._states.get(self._circuit_key(work_item))
        failure_count = state.failure_count if state is not None else 0
        error = "Poison message circuit open for %s@%d key after %d failure(s)" % (
            work_item.tp,
            work_item.offset,
            failure_count,
        )
        return CompletionEvent(
            id=work_item.id,
            tp=work_item.tp,
            offset=work_item.offset,
            epoch=work_item.epoch,
            status=CompletionStatus.FAILURE,
            error=error,
            attempt=self._forced_failure_attempt,
        )

    def clear_partition(self, tp: DtoTopicPartition) -> None:
        """Clear partition for poison-message protection.

        Args:
            tp: Topic-partition affected by the operation.

        """
        for circuit_key in list(self._states):
            if circuit_key[0] == tp:
                self._states.pop(circuit_key, None)

    @staticmethod
    def _circuit_key(work_item: WorkItem) -> CircuitKey:
        """Handle circuit key within poison-message protection.

        Args:
            work_item: Work item being scheduled or processed.

        Returns:
            CircuitKey result produced by this function.

        """
        poison_key = work_item.poison_key
        if poison_key is WORK_ITEM_POISON_KEY_UNSET:
            poison_key = work_item.key
        return (work_item.tp, poison_key)

    def get_open_circuit_count(self) -> int:
        """Return open circuit count for poison-message protection.

        Returns:
            Computed integer value.

        """
        if not self.enabled:
            return 0

        now = self._clock()
        open_count = 0
        for circuit_key, state in list(self._states.items()):
            if state.open_until is None:
                continue
            if now < state.open_until:
                open_count += 1
            else:
                self._states.pop(circuit_key, None)
        return open_count
