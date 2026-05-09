# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_completion_buffer.py
# Role: Buffers process completion queue items, expansion, and duplicate suppression.
# Extend here for parent-side completion visibility rules; keep worker lifecycle in process_engine.
from __future__ import annotations

import logging
import queue
from collections.abc import Callable
from typing import Any, Deque

from pyrallel_consumer.dto import CompletionEvent, CompletionStatus

CompletionIdentity = tuple[str, str, int, int, int]

_MAX_SEEN_COMPLETION_IDENTITIES = 100_000


class ProcessCompletionBuffer:
    """Parent-side completion queue buffer for process execution."""

    def __init__(
        self,
        *,
        completion_queue: Any,
        prefetched_events: Deque[CompletionEvent],
        seen_identities: set[CompletionIdentity],
        seen_identity_order: Deque[CompletionIdentity],
        decode_queue_item_events: Callable[[Any], list[CompletionEvent]],
        discard_registry_entry_for_completion: Callable[[CompletionEvent], None],
        max_seen_identities: int = _MAX_SEEN_COMPLETION_IDENTITIES,
    ) -> None:
        self._completion_queue = completion_queue
        self._prefetched_events = prefetched_events
        self._seen_identities = seen_identities
        self._seen_identity_order = seen_identity_order
        self._decode_queue_item_events = decode_queue_item_events
        self._discard_registry_entry_for_completion = (
            discard_registry_entry_for_completion
        )
        self._max_seen_identities = max(1, max_seen_identities)

    def uses(
        self,
        *,
        completion_queue: Any,
        prefetched_events: Deque[CompletionEvent],
        seen_identities: set[CompletionIdentity],
        seen_identity_order: Deque[CompletionIdentity],
    ) -> bool:
        """Return whether this buffer still wraps the current engine state."""
        return (
            self._completion_queue is completion_queue
            and self._prefetched_events is prefetched_events
            and self._seen_identities is seen_identities
            and self._seen_identity_order is seen_identity_order
        )

    @property
    def has_prefetched_events(self) -> bool:
        """Return whether any completion event is ready to surface."""
        return bool(self._prefetched_events)

    def drain_queue(self) -> int:
        """Prefetch all immediately visible queue items."""
        if self._completion_queue is None:
            return 0
        prefetched = 0
        while True:
            try:
                raw_event = self._completion_queue.get_nowait()
            except queue.Empty:
                return prefetched
            if self.prefetch_queue_item(raw_event):
                prefetched += 1

    def prefetch_queue_item(self, raw_event: Any) -> bool:
        """Decode one queue item and prefetch any non-duplicate events."""
        accepted = False
        for event in self._decode_queue_item_events(raw_event):
            accepted = self.prefetch_event(event) or accepted
        return accepted

    def prefetch_event(self, event: CompletionEvent) -> bool:
        """Append a visible completion unless it has already been surfaced."""
        if self.is_duplicate_event(event):
            return False
        self._prefetched_events.append(event)
        self._discard_registry_entry_for_completion(event)
        return True

    def poll_prefetched(
        self,
        *,
        batch_limit: int,
        decrement_in_flight: Callable[[], None],
    ) -> list[CompletionEvent]:
        """Pop already-prefetched events up to the requested batch limit."""
        completed_events: list[CompletionEvent] = []
        while len(completed_events) < batch_limit and self._prefetched_events:
            completed_events.append(self._prefetched_events.popleft())
            decrement_in_flight()
        return completed_events

    def poll_available(
        self,
        *,
        batch_limit: int,
        decrement_in_flight: Callable[[], None],
        logger: logging.Logger,
    ) -> list[CompletionEvent]:
        """Poll immediately visible completion queue items."""
        completed_events: list[CompletionEvent] = []
        if self._completion_queue is None:
            return completed_events
        while len(completed_events) < batch_limit:
            try:
                raw_event = self._completion_queue.get_nowait()
            except queue.Empty:
                break
            except Exception as exc:
                logger.error(
                    "Error getting item from completion queue: %r",
                    exc,
                    exc_info=True,
                )
                break
            for event in self._decode_queue_item_events(raw_event):
                if self.is_duplicate_event(event):
                    continue
                if len(completed_events) >= batch_limit:
                    self._prefetched_events.append(event)
                    self._discard_registry_entry_for_completion(event)
                    continue
                self._discard_registry_entry_for_completion(event)
                completed_events.append(event)
                decrement_in_flight()
        return completed_events

    def is_duplicate_event(self, event: CompletionEvent) -> bool:
        """Return True when this item completion was already surfaced."""
        if self.is_synthetic_failure_event(event):
            return False
        identity = (
            event.id,
            event.tp.topic,
            event.tp.partition,
            event.offset,
            event.epoch,
        )
        if identity in self._seen_identities:
            return True
        self._seen_identities.add(identity)
        self._seen_identity_order.append(identity)
        while len(self._seen_identity_order) > self._max_seen_identities:
            self._seen_identities.discard(self._seen_identity_order.popleft())
        return False

    @staticmethod
    def is_synthetic_failure_event(event: CompletionEvent) -> bool:
        """Return whether a failure has no stable work identity to dedupe by."""
        return (
            event.status == CompletionStatus.FAILURE
            and event.id == ""
            and event.tp.topic == ""
            and event.offset < 0
            and event.epoch == 0
        )
