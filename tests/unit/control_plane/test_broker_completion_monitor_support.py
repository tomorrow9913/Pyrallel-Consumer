# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_completion_monitor_support.py
# Role: Verifies completion monitor cadence outside BrokerPoller.
# Extend here when worker-completion monitoring moves between poller and support modules.

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.control_plane.broker_completion_monitor_support import (
    BrokerCompletionMonitorSupport,
)


def _make_support(
    *,
    running: bool = True,
    total_in_flight: int = 0,
    has_pending_dlq: bool = False,
    max_blocking_duration_ms: int = 0,
    wait_for_completion=None,
    drain_completion_events_once=None,
    maybe_commit_ready_offsets=None,
    sleep=None,
):
    """Create BrokerCompletionMonitorSupport with mutable monitor state."""
    state: dict[str, object] = {
        "running": running,
        "fatal_error": None,
        "has_pending_dlq": has_pending_dlq,
        "total_in_flight": total_in_flight,
    }
    if wait_for_completion is None:
        wait_for_completion = AsyncMock(return_value=False)
    if drain_completion_events_once is None:
        drain_completion_events_once = AsyncMock(return_value=False)
    if maybe_commit_ready_offsets is None:
        maybe_commit_ready_offsets = AsyncMock()
    if sleep is None:

        async def sleep(_seconds: float) -> None:
            state["running"] = False

    support = BrokerCompletionMonitorSupport(
        control_lock=asyncio.Lock(),
        get_running=lambda: bool(state["running"]),
        set_running=lambda value: state.__setitem__("running", value),
        get_total_in_flight_count=lambda: cast(int, state["total_in_flight"]),
        has_pending_dlq_events=lambda: bool(state["has_pending_dlq"]),
        get_idle_consume_timeout_seconds=lambda: 0.1,
        get_max_blocking_duration_ms=lambda: max_blocking_duration_ms,
        wait_for_completion=wait_for_completion,
        drain_completion_events_once=drain_completion_events_once,
        maybe_commit_ready_offsets=maybe_commit_ready_offsets,
        set_fatal_error=lambda value: state.__setitem__("fatal_error", value),
        logger=MagicMock(),
        sleep=sleep,
    )
    return support, {
        "state": state,
        "wait_for_completion": wait_for_completion,
        "drain_completion_events_once": drain_completion_events_once,
        "maybe_commit_ready_offsets": maybe_commit_ready_offsets,
        "sleep": sleep,
    }


@pytest.mark.asyncio
async def test_completion_monitor_support_sleeps_when_idle() -> None:
    # Given: no in-flight work or pending DLQ event exists.
    sleep = AsyncMock()
    support, doubles = _make_support(sleep=sleep)

    async def stop_after_sleep(_seconds: float) -> None:
        doubles["state"]["running"] = False

    sleep.side_effect = stop_after_sleep

    # When: the monitor loop runs one idle cadence.
    await support.run()

    # Then: it sleeps without waiting on engine completion or draining events.
    sleep.assert_awaited_once_with(0.1)
    doubles["wait_for_completion"].assert_not_awaited()
    doubles["drain_completion_events_once"].assert_not_awaited()


@pytest.mark.asyncio
async def test_completion_monitor_support_drains_and_commits_after_completion() -> None:
    # Given: in-flight work completes and draining sees completion events.
    wait_for_completion = AsyncMock(return_value=True)

    async def drain_once() -> bool:
        state["running"] = False
        return True

    state = {"running": True}
    support, doubles = _make_support(
        running=True,
        total_in_flight=1,
        wait_for_completion=wait_for_completion,
        drain_completion_events_once=AsyncMock(side_effect=drain_once),
    )
    doubles["state"]["running"] = state["running"]

    async def stop_after_drain() -> bool:
        doubles["state"]["running"] = False
        return True

    doubles["drain_completion_events_once"].side_effect = stop_after_drain

    # When: the monitor observes an engine completion.
    await support.run()

    # Then: drained completions trigger commit cadence with completion-monitor source.
    wait_for_completion.assert_awaited_once_with(timeout_seconds=0.1)
    doubles["maybe_commit_ready_offsets"].assert_awaited_once_with(
        had_pending_dlq_events=False,
        source="completion_monitor",
    )


@pytest.mark.asyncio
async def test_completion_monitor_support_retries_pending_dlq_without_engine_wait() -> (
    None
):
    # Given: pending DLQ publication needs retry but no engine completion is required.
    async def drain_once() -> bool:
        doubles["state"]["running"] = False
        return True

    support, doubles = _make_support(
        total_in_flight=0,
        has_pending_dlq=True,
        drain_completion_events_once=AsyncMock(side_effect=drain_once),
    )

    # When: the monitor loop runs.
    await support.run()

    # Then: DLQ retry drains immediately and commit cadence records pending-DLQ activity.
    doubles["wait_for_completion"].assert_not_awaited()
    doubles["drain_completion_events_once"].assert_awaited_once()
    doubles["maybe_commit_ready_offsets"].assert_awaited_once_with(
        had_pending_dlq_events=True,
        source="completion_monitor",
    )


@pytest.mark.asyncio
async def test_completion_monitor_support_records_fatal_error_and_stops() -> None:
    # Given: waiting for worker completion raises unexpectedly.
    failure = RuntimeError("monitor failed")
    support, doubles = _make_support(
        total_in_flight=1,
        wait_for_completion=AsyncMock(side_effect=failure),
    )

    # When/Then: the monitor reraises and stores the fatal failure state.
    with pytest.raises(RuntimeError, match="monitor failed"):
        await support.run()

    assert doubles["state"]["fatal_error"] is failure
    assert doubles["state"]["running"] is False
