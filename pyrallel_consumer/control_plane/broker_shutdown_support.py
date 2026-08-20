# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_shutdown_support.py
# Role: Drains broker work during graceful shutdown without owning poller lifecycle.
# Extend here for shutdown drain decisions; keep start/stop entrypoints in broker_poller.py.
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any


class BrokerShutdownSupport:
    """Coordinate graceful shutdown draining for BrokerPoller."""

    def __init__(
        self,
        *,
        control_lock: asyncio.Lock,
        schedule_work: Callable[[], Awaitable[Any]],
        drain_completion_events_once: Callable[[], Awaitable[bool]],
        commit_ready_offsets: Callable[..., Awaitable[None]],
        get_total_in_flight_count: Callable[[], int],
        get_total_queued_messages: Callable[[], Awaitable[int]],
        get_pending_dlq_count: Callable[[], int],
        drain_commit_coordinator: Callable[[float], Awaitable[bool]],
        wait_for_completion: Callable[..., Awaitable[bool]],
        idle_consume_timeout_seconds: float,
        logger: Any,
        now: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        """Initialize shutdown drain support."""
        self._control_lock = control_lock
        self._schedule_work = schedule_work
        self._drain_completion_events_once = drain_completion_events_once
        self._commit_ready_offsets = commit_ready_offsets
        self._get_total_in_flight_count = get_total_in_flight_count
        self._get_total_queued_messages = get_total_queued_messages
        self._get_pending_dlq_count = get_pending_dlq_count
        self._drain_commit_coordinator = drain_commit_coordinator
        self._wait_for_completion = wait_for_completion
        self._idle_consume_timeout_seconds = idle_consume_timeout_seconds
        self._logger = logger
        self._now = now
        self._sleep = sleep

    async def drain(self, *, timeout_seconds: float) -> bool:
        """Drain scheduled work, completions, DLQ retries, and commits."""
        deadline = self._now() + max(0.0, timeout_seconds)

        while True:
            async with self._control_lock:
                await self._schedule_work()
                drained_completion = await self._drain_completion_events_once()

            if drained_completion:
                await self._commit_ready_offsets(force=True, source="stop_drain")

            total_in_flight = self._get_total_in_flight_count()
            total_queued = await self._get_total_queued_messages()
            pending_dlq_count = self._get_pending_dlq_count()
            if total_in_flight <= 0 and total_queued <= 0 and pending_dlq_count <= 0:
                await self._commit_ready_offsets(force=True, source="stop_drain")
                if not await self._drain_commit_coordinator(deadline):
                    return False
                self._logger.debug(
                    "Graceful shutdown drain completed with in_flight=%d queued=%d pending_dlq=%d",
                    total_in_flight,
                    total_queued,
                    pending_dlq_count,
                )
                return True

            remaining_seconds = deadline - self._now()
            if remaining_seconds <= 0:
                self._logger.warning(
                    "Graceful shutdown drain timed out after %.3fs; continuing with forced abort path (in_flight=%d queued=%d pending_dlq=%d)",
                    max(0.0, timeout_seconds),
                    total_in_flight,
                    total_queued,
                    pending_dlq_count,
                )
                await self._drain_commit_coordinator(deadline)
                return False

            if total_in_flight > 0 and pending_dlq_count <= 0:
                has_completion = await self._wait_for_completion(
                    timeout_seconds=min(
                        remaining_seconds,
                        self._idle_consume_timeout_seconds,
                    ),
                )
                if has_completion:
                    continue
            else:
                sleep_seconds = (
                    self._idle_consume_timeout_seconds
                    if pending_dlq_count > 0
                    else 0.01
                )
                await self._sleep(min(remaining_seconds, sleep_seconds))
