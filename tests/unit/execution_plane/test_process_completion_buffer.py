# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_completion_buffer.py
# Role: Verifies process completion queue buffering, prefetch, and duplicate filtering.
# Extend here for parent-side completion visibility rules split from ProcessExecutionEngine.
from __future__ import annotations

import logging
import queue
from collections import deque
from typing import Any

from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, TopicPartition
from pyrallel_consumer.execution_plane.process_completion_buffer import (
    ProcessCompletionBuffer,
)


def _completion_event(
    offset: int,
    *,
    id: str | None = None,
    status: CompletionStatus = CompletionStatus.SUCCESS,
    topic: str = "topic",
    partition: int = 0,
    epoch: int = 1,
) -> CompletionEvent:
    return CompletionEvent(
        id=id if id is not None else f"work-{offset}",
        tp=TopicPartition(topic, partition),
        offset=offset,
        epoch=epoch,
        status=status,
        error=None,
        attempt=1,
    )


def _buffer(
    completion_queue: queue.Queue[Any],
    decode_result: list[CompletionEvent],
    discarded: list[CompletionEvent],
) -> ProcessCompletionBuffer:
    return ProcessCompletionBuffer(
        completion_queue=completion_queue,
        prefetched_events=deque(),
        seen_identities=set(),
        seen_identity_order=deque(),
        decode_queue_item_events=lambda _raw: list(decode_result),
        discard_registry_entry_for_completion=discarded.append,
    )


def test_poll_available_prefetches_batch_tail_when_batch_limit_is_reached() -> None:
    # Given: a queue item expands to more completions than one poll can return.
    completion_queue: queue.Queue[Any] = queue.Queue()
    completion_queue.put("raw-batch")
    discarded: list[CompletionEvent] = []
    first_event = _completion_event(0)
    second_event = _completion_event(1)
    buffer = _buffer(completion_queue, [first_event, second_event], discarded)
    decremented = 0

    def _decrement_in_flight() -> None:
        nonlocal decremented
        decremented += 1

    # When: the caller polls with a batch limit smaller than the expanded payload.
    completed_events = buffer.poll_available(
        batch_limit=1,
        decrement_in_flight=_decrement_in_flight,
        logger=logging.getLogger(__name__),
    )

    # Then: the first completion is surfaced and the tail remains prefetched.
    assert completed_events == [first_event]
    assert buffer.poll_prefetched(
        batch_limit=1,
        decrement_in_flight=_decrement_in_flight,
    ) == [second_event]
    assert discarded == [first_event, second_event]
    assert decremented == 2


def test_poll_available_suppresses_duplicate_completion_identity() -> None:
    # Given: two queue items decode to the same stable completion identity.
    completion_queue: queue.Queue[Any] = queue.Queue()
    completion_queue.put("raw-first")
    completion_queue.put("raw-duplicate")
    discarded: list[CompletionEvent] = []
    event = _completion_event(42)
    buffer = _buffer(completion_queue, [event], discarded)

    # When: the buffer drains both visible queue items.
    completed_events = buffer.poll_available(
        batch_limit=10,
        decrement_in_flight=lambda: None,
        logger=logging.getLogger(__name__),
    )

    # Then: only the first completion reaches the engine.
    assert completed_events == [event]
    assert discarded == [event]


def test_synthetic_failure_completion_is_not_deduplicated() -> None:
    # Given: two synthetic failures have no stable work identity.
    completion_queue: queue.Queue[Any] = queue.Queue()
    completion_queue.put("raw-failures")
    discarded: list[CompletionEvent] = []
    failure = _completion_event(
        -1,
        id="",
        topic="",
        status=CompletionStatus.FAILURE,
        epoch=0,
    )
    buffer = _buffer(completion_queue, [failure, failure], discarded)

    # When: both failures are decoded from the same queue item.
    completed_events = buffer.poll_available(
        batch_limit=10,
        decrement_in_flight=lambda: None,
        logger=logging.getLogger(__name__),
    )

    # Then: both failures are surfaced so recovery errors are not hidden.
    assert completed_events == [failure, failure]
    assert discarded == [failure, failure]
