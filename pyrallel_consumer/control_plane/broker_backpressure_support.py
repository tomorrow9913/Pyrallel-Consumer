# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_backpressure_support.py
# Role: Coordinates BrokerPoller adaptive limits and broker pause/resume checks.
# Extend here for backpressure orchestration; keep adaptive policies in adaptive modules.
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from .adaptive_backpressure import AdaptiveBackpressureController
from .adaptive_concurrency import (
    AdaptiveConcurrencyController,
    AdaptiveConcurrencySample,
)


class BrokerBackpressureSupport:
    """Coordinate adaptive concurrency limits and broker backpressure checks."""

    def __init__(
        self,
        *,
        configured_max_in_flight: int,
        adaptive_backpressure_controller: AdaptiveBackpressureController,
        adaptive_concurrency_controller: AdaptiveConcurrencyController,
        work_manager: Any,
        get_consumer: Callable[[], Any | None],
        get_total_queued_messages: Callable[[], Awaitable[int]],
        get_total_true_lag: Callable[[], int],
        get_current_limit: Callable[[], int],
        set_current_limit: Callable[[int], None],
        set_resume_limit: Callable[[int], None],
        get_queue_max_messages: Callable[[], int],
        get_is_paused: Callable[[], bool],
        set_is_paused: Callable[[bool], None],
        check_runtime_backpressure: Callable[[int], bool],
        logger: Any,
    ) -> None:
        """Initialize backpressure support."""
        self._configured_max_in_flight = max(1, int(configured_max_in_flight))
        self._adaptive_backpressure_controller = adaptive_backpressure_controller
        self._adaptive_concurrency_controller = adaptive_concurrency_controller
        self._work_manager = work_manager
        self._get_consumer = get_consumer
        self._get_total_queued_messages = get_total_queued_messages
        self._get_total_true_lag = get_total_true_lag
        self._get_current_limit = get_current_limit
        self._set_current_limit = set_current_limit
        self._set_resume_limit = set_resume_limit
        self._get_queue_max_messages = get_queue_max_messages
        self._get_is_paused = get_is_paused
        self._set_is_paused = set_is_paused
        self._check_runtime_backpressure = check_runtime_backpressure
        self._logger = logger

    def set_runtime_max_in_flight(
        self,
        value: int,
        *,
        log_change: bool = True,
    ) -> None:
        """Install or update the live max-in-flight limit."""
        new_value = max(1, min(self._configured_max_in_flight, int(value)))
        old_value = self._get_current_limit()
        self._set_current_limit(new_value)
        self._set_resume_limit(max(1, int(new_value * 0.7)))
        if old_value != new_value:
            set_max_in_flight_messages = getattr(
                self._work_manager,
                "set_max_in_flight_messages",
                None,
            )
            if callable(set_max_in_flight_messages):
                set_max_in_flight_messages(new_value)
        if log_change and old_value != new_value:
            self._logger.info(
                "Adaptive concurrency adjusted max_in_flight from %d to %d",
                old_value,
                new_value,
            )

    def maybe_adjust_adaptive_backpressure(self, total_queued: int) -> None:
        """Evaluate adaptive backpressure and apply any new limit."""
        if not self._adaptive_backpressure_controller.enabled:
            return
        get_latency = getattr(
            self._work_manager, "get_average_completion_latency_seconds", None
        )
        raw_completion_latency = get_latency() if callable(get_latency) else None
        avg_completion_latency = (
            float(raw_completion_latency)
            if isinstance(raw_completion_latency, (int, float))
            else None
        )
        new_limit = self._adaptive_backpressure_controller.evaluate(
            total_true_lag=self._get_total_true_lag(),
            total_queued=total_queued,
            avg_completion_latency_seconds=avg_completion_latency,
            is_paused=self._get_is_paused(),
        )
        if new_limit == self._get_current_limit():
            return
        self.set_runtime_max_in_flight(new_limit)

    def maybe_adjust_adaptive_concurrency(self, total_queued: int) -> None:
        """Evaluate adaptive concurrency and apply any new limit."""
        new_limit = self._adaptive_concurrency_controller.evaluate(
            AdaptiveConcurrencySample(
                current_limit=self._get_current_limit(),
                total_in_flight=self._work_manager.get_total_in_flight_count(),
                total_queued=total_queued,
                total_true_lag=self._get_total_true_lag(),
                is_paused=self._get_is_paused(),
                queue_max_messages=self._get_queue_max_messages(),
            )
        )
        if new_limit is None:
            return
        self.set_runtime_max_in_flight(new_limit)

    async def check_backpressure(self) -> None:
        """Run adaptive limit checks and broker pause/resume transitions."""
        if self._get_consumer() is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks")

        total_queued = await self._get_total_queued_messages()
        self.maybe_adjust_adaptive_backpressure(total_queued)
        self.maybe_adjust_adaptive_concurrency(total_queued)
        total_in_flight = self._work_manager.get_total_in_flight_count()
        current_load = total_in_flight + total_queued
        queue_full = (
            self._get_queue_max_messages() > 0
            and total_queued >= self._get_queue_max_messages()
        )
        if (
            not self._adaptive_backpressure_controller.enabled
            and not self._adaptive_concurrency_controller.enabled
            and not self._get_is_paused()
            and not queue_full
            and current_load <= self._get_current_limit()
        ):
            return
        self._set_is_paused(self._check_runtime_backpressure(total_queued))
