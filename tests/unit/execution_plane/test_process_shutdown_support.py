# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_shutdown_support.py
# Role: Verifies process shutdown support worker escalation, drain stability, and cleanup.
# Extend here for shutdown mechanics split from ProcessExecutionEngine.
from __future__ import annotations

import logging
from collections import deque
from typing import Any, cast
from unittest.mock import Mock

import pytest

from pyrallel_consumer.execution_plane.process_shutdown_support import (
    ProcessShutdownContext,
    ProcessShutdownSupport,
)


class _EscalatingWorker:
    pid = 1234

    def __init__(self) -> None:
        self.join_calls: list[float | None] = []
        self.terminate_calls = 0
        self.kill_calls = 0
        self._alive_states = [True, True, False]

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)

    def is_alive(self) -> bool:
        return self._alive_states.pop(0)

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class _Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _Listener:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.mark.asyncio
async def test_drain_until_stable_empty_waits_for_consecutive_empty_passes() -> None:
    # Given: shutdown IPC exposes one late event after an initial empty pass.
    support = ProcessShutdownSupport(logging.getLogger(__name__))
    drain_results = [(0, 0), (0, 1), (0, 0), (0, 0)]
    sleeps: list[float] = []

    def drain_once() -> tuple[int, int]:
        return drain_results.pop(0)

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monotonic_values = iter([0.0, 0.01, 0.02, 0.03])

    # When: stable-empty draining runs with a two-empty-pass requirement.
    result = await support.drain_until_stable_empty(
        drain_once=drain_once,
        max_seconds=0.05,
        stable_empty_passes=2,
        monotonic=lambda: next(monotonic_values, 0.04),
        sleep=sleep,
    )

    # Then: the late event resets the empty counter and all passes are counted.
    assert result == (0, 1, 4)
    assert sleeps == [0.01, 0.01, 0.01]


def test_join_worker_with_escalation_terminates_then_kills_alive_worker() -> None:
    # Given: a worker remains alive after the first join and terminate pass.
    support = ProcessShutdownSupport(logging.getLogger(__name__))
    worker = _EscalatingWorker()

    # When: shutdown support joins the worker with escalation.
    support.join_worker_with_escalation(worker, timeout_seconds=0.05)

    # Then: terminate and kill are used only after earlier joins still report alive.
    assert worker.join_calls == [0.05, 0.05, 0.05]
    assert worker.terminate_calls == 1
    assert worker.kill_calls == 1


@pytest.mark.asyncio
async def test_shutdown_clears_registry_closes_resources_and_preserves_prefetch() -> (
    None
):
    # Given: shutdown context has one prefetched completion and one residual registry entry.
    support = ProcessShutdownSupport(logging.getLogger(__name__))
    order: list[str] = []
    task_queue = _Closable()
    completion_queue = _Closable()
    registry_event_queue = _Closable()
    listener = _Listener()
    transport = Mock()
    batch_accumulator = Mock()
    prefetched_events = cast(Any, deque([Mock()]))
    in_flight_registry: dict[tuple[int, str, int, int], dict[str, Any]] = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
        }
    }
    in_flight_counts: list[int] = []

    def drain_once() -> tuple[int, int]:
        order.append("drain")
        return (0, 0)

    async def sleep(_delay: float) -> None:
        order.append("sleep")

    context = ProcessShutdownContext(
        workers=[Mock()],
        batch_accumulator=batch_accumulator,
        transport=transport,
        task_queue=task_queue,
        completion_queue=completion_queue,
        registry_event_queue=registry_event_queue,
        log_listener=listener,
        prefetched_completion_events=prefetched_events,
        in_flight_registry=in_flight_registry,
        worker_pid_by_index={0: 999},
        drain_registry_events=lambda: order.append("registry"),
        drain_shutdown_ipc_once=drain_once,
        join_worker=lambda _worker: order.append("join"),
        set_in_flight_count=in_flight_counts.append,
    )

    monotonic_values = iter([10.0, 10.0, 12.0, 12.0, 12.01])

    # When: support performs the shutdown sequence.
    await support.shutdown(
        context,
        monotonic=lambda: next(monotonic_values, 12.02),
        sleep=sleep,
    )

    # Then: local cleanup happens after joins and preserves the prefetched count.
    assert order == ["registry", "drain", "sleep", "join", "drain", "sleep", "drain"]
    assert in_flight_registry == {}
    assert in_flight_counts == [1]
    assert task_queue.closed is True
    assert completion_queue.closed is True
    assert registry_event_queue.closed is True
    assert listener.stopped is True
    batch_accumulator.close.assert_called_once_with()
    transport.signal_shutdown.assert_called_once_with(1)
    transport.clear_pending_dispatches.assert_called_once_with()
    transport.close.assert_called_once_with()
