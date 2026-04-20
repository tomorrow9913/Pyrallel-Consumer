from __future__ import annotations

import time
from typing import Optional

from pyrallel_consumer.config import AdaptiveBackpressureConfig
from pyrallel_consumer.dto import AdaptiveBackpressureSnapshot


class AdaptiveBackpressureController:
    def __init__(
        self,
        *,
        configured_max_in_flight: int,
        config: AdaptiveBackpressureConfig,
    ) -> None:
        self._config = config
        self._configured_max_in_flight = max(1, int(configured_max_in_flight))
        self._min_in_flight = min(
            self._configured_max_in_flight,
            max(1, int(config.min_in_flight)),
        )
        self._effective_max_in_flight = self._configured_max_in_flight
        self._cooldown_seconds = max(0.0, float(config.cooldown_ms) / 1000.0)
        self._last_adjusted_at: Optional[float] = None
        self.last_decision = "disabled" if not config.enabled else "hold"

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    @property
    def effective_max_in_flight(self) -> int:
        return self._effective_max_in_flight

    def evaluate(
        self,
        *,
        total_true_lag: int,
        total_queued: int,
        avg_completion_latency_seconds: Optional[float],
        is_paused: bool,
        now_monotonic: Optional[float] = None,
    ) -> int:
        if not self.enabled:
            self.last_decision = "disabled"
            return self._effective_max_in_flight

        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        if (
            self._last_adjusted_at is not None
            and now - self._last_adjusted_at < self._cooldown_seconds
        ):
            self.last_decision = "cooldown"
            return self._effective_max_in_flight

        latency_ms = (
            avg_completion_latency_seconds * 1000.0
            if avg_completion_latency_seconds is not None
            else None
        )
        should_scale_down = is_paused or (
            latency_ms is not None
            and latency_ms >= float(self._config.high_latency_threshold_ms)
        )
        should_scale_up = (
            int(total_true_lag) >= int(self._config.lag_scale_up_threshold)
            and not is_paused
            and int(total_queued) == 0
            and latency_ms is not None
            and latency_ms <= float(self._config.low_latency_threshold_ms)
        )

        if should_scale_down:
            self._effective_max_in_flight = max(
                self._min_in_flight,
                self._effective_max_in_flight - int(self._config.scale_down_step),
            )
            self._last_adjusted_at = now
            self.last_decision = "scale_down"
            return self._effective_max_in_flight

        if should_scale_up:
            self._effective_max_in_flight = min(
                self._configured_max_in_flight,
                self._effective_max_in_flight + int(self._config.scale_up_step),
            )
            self._last_adjusted_at = now
            self.last_decision = "scale_up"
            return self._effective_max_in_flight

        self.last_decision = "hold"
        return self._effective_max_in_flight

    def build_runtime_snapshot(
        self, *, avg_completion_latency_seconds: Optional[float]
    ) -> AdaptiveBackpressureSnapshot:
        return AdaptiveBackpressureSnapshot(
            configured_max_in_flight=self._configured_max_in_flight,
            effective_max_in_flight=self._effective_max_in_flight,
            min_in_flight=self._min_in_flight,
            scale_up_step=int(self._config.scale_up_step),
            scale_down_step=int(self._config.scale_down_step),
            cooldown_ms=int(self._config.cooldown_ms),
            lag_scale_up_threshold=int(self._config.lag_scale_up_threshold),
            low_latency_threshold_ms=float(self._config.low_latency_threshold_ms),
            high_latency_threshold_ms=float(self._config.high_latency_threshold_ms),
            last_decision=self.last_decision,
            avg_completion_latency_seconds=avg_completion_latency_seconds,
        )
