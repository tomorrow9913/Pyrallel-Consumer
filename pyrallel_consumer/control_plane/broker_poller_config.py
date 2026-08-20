# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_poller_config.py
# Role: Resolves BrokerPoller configuration values into runtime primitives.
# Extend here for BrokerPoller config coercion; keep runtime state transitions in broker_poller.py.
from __future__ import annotations

from typing import Any

from pyrallel_consumer.config import (
    AdaptiveBackpressureConfig,
    AdaptiveConcurrencyConfig,
)
from pyrallel_consumer.dto import OrderingMode


def resolve_ordering_mode(pc_conf: Any) -> OrderingMode:
    """Resolve the configured ordering mode with BrokerPoller fallback semantics."""
    ordering_mode = getattr(pc_conf, "ordering_mode", OrderingMode.KEY_HASH)
    if isinstance(ordering_mode, str):
        ordering_mode = OrderingMode(ordering_mode)
    if not isinstance(ordering_mode, OrderingMode):
        return OrderingMode.KEY_HASH
    return ordering_mode


def resolve_configured_max_in_flight(execution_config: Any) -> int:
    """Resolve BrokerPoller's configured max in-flight message cap."""
    raw_value = getattr(execution_config, "max_in_flight", 1000)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        raw_value = 1000
    return max(1, int(raw_value))


def resolve_commit_debounce_completion_threshold(pc_conf: Any) -> int:
    """Resolve the completion-count threshold for commit debounce."""
    raw_value = getattr(pc_conf, "commit_debounce_completion_threshold", 100)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return 100
    return max(1, int(raw_value))


def resolve_commit_debounce_interval_seconds(pc_conf: Any) -> float:
    """Resolve the time threshold for commit debounce in seconds."""
    raw_value = getattr(pc_conf, "commit_debounce_interval_ms", 100)
    if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
        return 0.1
    return max(0.0, float(raw_value) / 1000.0)


def resolve_shutdown_policy(execution_config: Any) -> str:
    """Resolve BrokerPoller's shutdown policy string."""
    return str(getattr(execution_config, "shutdown_policy", "graceful"))


def resolve_shutdown_drain_timeout_seconds(execution_config: Any) -> float:
    """Resolve the graceful shutdown drain timeout in seconds."""
    resolve_timeout = getattr(
        execution_config, "resolve_shutdown_drain_timeout_ms", None
    )
    if callable(resolve_timeout):
        resolved_timeout = resolve_timeout()
        if isinstance(resolved_timeout, (int, float)):
            return max(0.0, float(resolved_timeout) / 1000.0)
        return 0.0
    timeout_ms = getattr(execution_config, "shutdown_drain_timeout_ms", 0)
    return max(0.0, float(timeout_ms) / 1000.0)


def coerce_adaptive_backpressure_config(
    raw_config: object,
) -> AdaptiveBackpressureConfig:
    """Coerce a loose adaptive-backpressure object into the typed config."""

    def _bool(name: str, default: bool) -> bool:
        """Coerce a boolean adaptive-backpressure field."""
        value = getattr(raw_config, name, default)
        return value if isinstance(value, bool) else default

    def _int(name: str, default: int) -> int:
        """Coerce an integer adaptive-backpressure field."""
        value = getattr(raw_config, name, default)
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        return default

    def _float(name: str, default: float) -> float:
        """Coerce a floating-point adaptive-backpressure field."""
        value = getattr(raw_config, name, default)
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return default

    return AdaptiveBackpressureConfig(
        enabled=_bool("enabled", False),
        min_in_flight=_int("min_in_flight", 1),
        scale_up_step=_int("scale_up_step", 16),
        scale_down_step=_int("scale_down_step", 16),
        cooldown_ms=_int("cooldown_ms", 1000),
        lag_scale_up_threshold=_int("lag_scale_up_threshold", 0),
        low_latency_threshold_ms=_float("low_latency_threshold_ms", 25.0),
        high_latency_threshold_ms=_float("high_latency_threshold_ms", 100.0),
    )


def coerce_adaptive_concurrency_config(
    raw_parent: object,
    attribute_name: str,
) -> AdaptiveConcurrencyConfig:
    """Coerce a loose adaptive-concurrency child object into the typed config."""
    raw_config = getattr(raw_parent, attribute_name, None)

    def _bool(name: str, default: bool) -> bool:
        """Coerce a boolean adaptive-concurrency field."""
        value = getattr(raw_config, name, default)
        return value if isinstance(value, bool) else default

    def _int(name: str, default: int) -> int:
        """Coerce an integer adaptive-concurrency field."""
        value = getattr(raw_config, name, default)
        if isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return int(value)
        return default

    return AdaptiveConcurrencyConfig(
        enabled=_bool("enabled", False),
        min_in_flight=_int("min_in_flight", 0),
        scale_up_step=_int("scale_up_step", 32),
        scale_down_step=_int("scale_down_step", 64),
        cooldown_ms=_int("cooldown_ms", 1000),
    )
