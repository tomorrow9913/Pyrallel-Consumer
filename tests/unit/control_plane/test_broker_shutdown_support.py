# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_shutdown_support.py
# Role: Verifies graceful shutdown drain support outside BrokerPoller.
# Extend here when shutdown drain decisions move between BrokerPoller and support modules.

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.control_plane.broker_shutdown_support import (
    BrokerShutdownSupport,
)


def _make_support(
    *,
    total_in_flight: int = 0,
    total_queued: int = 0,
    pending_dlq_count: int = 0,
    drained_completion: bool = False,
    drain_commit_coordinator=None,
    wait_for_completion=None,
    sleep=None,
):
    """Create BrokerShutdownSupport with mutable test doubles."""
    control_lock = asyncio.Lock()
    schedule_work = AsyncMock()
    drain_completion_events_once = AsyncMock(return_value=drained_completion)
    commit_ready_offsets = AsyncMock()
    get_total_in_flight_count = MagicMock(return_value=total_in_flight)
    get_total_queued_messages = AsyncMock(return_value=total_queued)
    get_pending_dlq_count = MagicMock(return_value=pending_dlq_count)
    if drain_commit_coordinator is None:
        drain_commit_coordinator = AsyncMock(return_value=True)
    if wait_for_completion is None:
        wait_for_completion = AsyncMock(return_value=False)
    if sleep is None:
        sleep = AsyncMock()
    support = BrokerShutdownSupport(
        control_lock=control_lock,
        schedule_work=schedule_work,
        drain_completion_events_once=drain_completion_events_once,
        commit_ready_offsets=commit_ready_offsets,
        get_total_in_flight_count=get_total_in_flight_count,
        get_total_queued_messages=get_total_queued_messages,
        get_pending_dlq_count=get_pending_dlq_count,
        drain_commit_coordinator=drain_commit_coordinator,
        wait_for_completion=wait_for_completion,
        idle_consume_timeout_seconds=0.1,
        logger=MagicMock(),
        sleep=sleep,
    )
    return support, {
        "schedule_work": schedule_work,
        "drain_completion_events_once": drain_completion_events_once,
        "commit_ready_offsets": commit_ready_offsets,
        "get_total_in_flight_count": get_total_in_flight_count,
        "get_pending_dlq_count": get_pending_dlq_count,
        "drain_commit_coordinator": drain_commit_coordinator,
        "wait_for_completion": wait_for_completion,
        "sleep": sleep,
    }


@pytest.mark.asyncio
async def test_shutdown_support_drains_commit_coordinator_when_work_is_empty():
    # Given: no in-flight work, queued work, or pending DLQ publication remains.
    support, doubles = _make_support()

    # When: graceful shutdown drain is executed.
    drained = await support.drain(timeout_seconds=0.0)

    # Then: commit readiness and coordinator draining complete the shutdown.
    assert drained is True
    doubles["schedule_work"].assert_awaited_once()
    doubles["drain_completion_events_once"].assert_awaited_once()
    doubles["commit_ready_offsets"].assert_awaited_once_with(
        force=True,
        source="stop_drain",
    )
    doubles["drain_commit_coordinator"].assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_support_commits_after_drained_completion_before_timeout():
    # Given: a completion event is drained but pending DLQ prevents full shutdown.
    support, doubles = _make_support(
        pending_dlq_count=1,
        drained_completion=True,
    )

    # When: graceful shutdown drain reaches the deadline.
    drained = await support.drain(timeout_seconds=0.0)

    # Then: drained completion triggers a forced commit scan before abort fallback.
    assert drained is False
    doubles["commit_ready_offsets"].assert_awaited_once_with(
        force=True,
        source="stop_drain",
    )
    doubles["drain_commit_coordinator"].assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_support_waits_for_worker_completion_while_in_flight():
    # Given: in-flight work exists and no DLQ publication is pending.
    completion_seen = {"value": False}

    async def wait_for_completion(*, timeout_seconds):
        completion_seen["value"] = True
        assert timeout_seconds == pytest.approx(0.1)
        return True

    support, doubles = _make_support(
        total_in_flight=1,
        wait_for_completion=AsyncMock(side_effect=wait_for_completion),
    )
    doubles["get_total_in_flight_count"].side_effect = [1, 0]

    # When: graceful shutdown drain has time to wait for worker completion.
    drained = await support.drain(timeout_seconds=0.2)

    # Then: worker completion wait is used before the final drained shutdown path.
    assert drained is True
    assert completion_seen["value"] is True
    doubles["wait_for_completion"].assert_awaited_once()
    doubles["drain_commit_coordinator"].assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_support_sleeps_for_pending_dlq_retry_window():
    # Given: pending DLQ publication should throttle the drain loop briefly.
    pending_counts = [1, 0]

    def get_pending_dlq_count():
        return pending_counts.pop(0)

    sleep = AsyncMock()
    support, doubles = _make_support(
        pending_dlq_count=1,
        sleep=sleep,
    )
    support._get_pending_dlq_count = get_pending_dlq_count
    support._sleep = sleep

    # When: graceful shutdown drain observes pending DLQ and then a clear state.
    drained = await support.drain(timeout_seconds=1.0)

    # Then: it sleeps on the DLQ retry cadence before completing shutdown.
    assert drained is True
    sleep.assert_awaited_once()
    sleep_duration = sleep.await_args.args[0]
    assert 0 < sleep_duration <= 0.1
    doubles["drain_commit_coordinator"].assert_awaited_once()
