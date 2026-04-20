from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from pyrallel_consumer.config import AdaptiveConcurrencyConfig
from pyrallel_consumer.dto import AdaptiveConcurrencyRuntimeSnapshot


@dataclass(frozen=True)
class AdaptiveConcurrencySample:
    current_limit: int
    total_in_flight: int
    total_queued: int
    total_true_lag: int
    is_paused: bool
    queue_max_messages: int


class AdaptiveConcurrencyController:
    def __init__(
        self,
        config: AdaptiveConcurrencyConfig,
        *,
        configured_max_in_flight: int,
    ) -> None:
        self._config = config
        self._configured_max_in_flight = max(1, int(configured_max_in_flight))
        self._min_in_flight = self._resolve_min_in_flight(config.min_in_flight)
        self._cooldown_seconds = max(0.0, float(config.cooldown_ms) / 1000.0)
        self._last_adjusted_at: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return bool(self._config.enabled)

    def _resolve_min_in_flight(self, raw_min_in_flight: int) -> int:
        if int(raw_min_in_flight) <= 0:
            return max(1, self._configured_max_in_flight // 4)
        return min(self._configured_max_in_flight, max(1, int(raw_min_in_flight)))

    def evaluate(
        self,
        sample: AdaptiveConcurrencySample,
        *,
        now_seconds: Optional[float] = None,
    ) -> Optional[int]:
        if not self._config.enabled:
            return None

        now = time.monotonic() if now_seconds is None else float(now_seconds)
        if (
            self._last_adjusted_at is not None
            and now - self._last_adjusted_at < self._cooldown_seconds
        ):
            return None

        total_load = int(sample.total_in_flight) + int(sample.total_queued)
        queue_full = int(sample.queue_max_messages) > 0 and int(
            sample.total_queued
        ) >= int(sample.queue_max_messages)

        new_limit: Optional[int] = None
        if sample.is_paused or queue_full or total_load > int(sample.current_limit):
            new_limit = max(
                self._min_in_flight,
                int(sample.current_limit) - int(self._config.scale_down_step),
            )
        elif (
            int(sample.total_true_lag) >= int(sample.current_limit)
            and int(sample.total_queued) == 0
        ):
            new_limit = min(
                self._configured_max_in_flight,
                int(sample.current_limit) + int(self._config.scale_up_step),
            )

        if new_limit is None or new_limit == int(sample.current_limit):
            return None

        self._last_adjusted_at = now
        return new_limit

    def build_runtime_snapshot(
        self,
        *,
        effective_max_in_flight: int,
    ) -> AdaptiveConcurrencyRuntimeSnapshot:
        return AdaptiveConcurrencyRuntimeSnapshot(
            configured_max_in_flight=self._configured_max_in_flight,
            effective_max_in_flight=max(
                1,
                min(self._configured_max_in_flight, int(effective_max_in_flight)),
            ),
            min_in_flight=self._min_in_flight,
            scale_up_step=int(self._config.scale_up_step),
            scale_down_step=int(self._config.scale_down_step),
            cooldown_ms=int(self._config.cooldown_ms),
        )
