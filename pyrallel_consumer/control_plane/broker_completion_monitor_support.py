# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_completion_monitor_support.py
# Role: Runs completion-monitor cadence outside the Kafka poller facade.
# Extend here for completion queue monitoring; keep Kafka polling in broker_poller.py.
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any


class BrokerCompletionMonitorSupport:
    """Monitor worker completions and trigger commit cadence outside BrokerPoller."""

    def __init__(
        self,
        *,
        control_lock: asyncio.Lock,
        get_running: Callable[[], bool],
        set_running: Callable[[bool], None],
        get_total_in_flight_count: Callable[[], int],
        has_pending_dlq_events: Callable[[], bool],
        get_idle_consume_timeout_seconds: Callable[[], float],
        get_max_blocking_duration_ms: Callable[[], int],
        wait_for_completion: Callable[..., Awaitable[bool]],
        drain_completion_events_once: Callable[[], Awaitable[bool]],
        maybe_commit_ready_offsets: Callable[..., Awaitable[None]],
        set_fatal_error: Callable[[Exception | None], None],
        logger: Any,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        """Initialize completion monitor support."""
        self._control_lock = control_lock
        self._get_running = get_running
        self._set_running = set_running
        self._get_total_in_flight_count = get_total_in_flight_count
        self._has_pending_dlq_events = has_pending_dlq_events
        self._get_idle_consume_timeout_seconds = get_idle_consume_timeout_seconds
        self._get_max_blocking_duration_ms = get_max_blocking_duration_ms
        self._wait_for_completion = wait_for_completion
        self._drain_completion_events_once = drain_completion_events_once
        self._maybe_commit_ready_offsets = maybe_commit_ready_offsets
        self._set_fatal_error = set_fatal_error
        self._logger = logger
        self._sleep = sleep

    async def run(self) -> None:
        """Run the completion monitor loop until stopped or cancelled."""
        timeout_seconds = self._get_idle_consume_timeout_seconds()
        max_blocking_duration_ms = self._get_max_blocking_duration_ms()
        if max_blocking_duration_ms > 0:
            timeout_seconds = min(
                timeout_seconds,
                max_blocking_duration_ms / 1000.0,
            )

        try:
            while self._get_running():
                if (
                    self._get_total_in_flight_count() <= 0
                    and not self._has_pending_dlq_events()
                ):
                    await self._sleep(timeout_seconds)
                    continue

                has_completion = self._has_pending_dlq_events()
                had_pending_dlq_events = has_completion
                if not has_completion:
                    has_completion = await self._wait_for_completion(
                        timeout_seconds=timeout_seconds,
                    )
                    if not has_completion and max_blocking_duration_ms <= 0:
                        continue

                async with self._control_lock:
                    has_completion = await self._drain_completion_events_once()
                if has_completion:
                    await self._maybe_commit_ready_offsets(
                        had_pending_dlq_events=had_pending_dlq_events,
                        source="completion_monitor",
                    )
                    if self._has_pending_dlq_events():
                        await self._sleep(timeout_seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set_fatal_error(exc)
            self._set_running(False)
            self._logger.error("Completion monitor error: %s", exc, exc_info=True)
            raise
