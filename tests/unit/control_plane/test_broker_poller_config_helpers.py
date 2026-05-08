# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_config_helpers.py
# Role: Verifies pure BrokerPoller configuration coercion helpers.
# Extend here when BrokerPoller config parsing moves out of the orchestrator.

from types import SimpleNamespace

import pytest

from pyrallel_consumer.control_plane.broker_poller_config import (
    coerce_adaptive_backpressure_config,
    coerce_adaptive_concurrency_config,
    resolve_commit_debounce_completion_threshold,
    resolve_commit_debounce_interval_seconds,
    resolve_configured_max_in_flight,
    resolve_ordering_mode,
    resolve_shutdown_drain_timeout_seconds,
    resolve_shutdown_policy,
)
from pyrallel_consumer.dto import OrderingMode


def test_resolve_ordering_mode_accepts_string_and_falls_back_for_invalid_type():
    # Given: ordering mode inputs include a valid string and an invalid object.
    valid_config = SimpleNamespace(ordering_mode="partition")
    invalid_config = SimpleNamespace(ordering_mode=object())

    # When: BrokerPoller ordering-mode config coercion is exercised.
    resolved_valid = resolve_ordering_mode(valid_config)
    resolved_invalid = resolve_ordering_mode(invalid_config)

    # Then: valid strings are normalized and invalid values fall back safely.
    assert resolved_valid is OrderingMode.PARTITION
    assert resolved_invalid is OrderingMode.KEY_HASH


@pytest.mark.parametrize(
    ("raw_value", "expected"),
    [
        (250, 250),
        (1.9, 1),
        (0, 1),
        (True, 1000),
        ("many", 1000),
    ],
)
def test_resolve_configured_max_in_flight_matches_broker_poller_defaults(
    raw_value, expected
):
    # Given: execution config exposes a raw max_in_flight value.
    execution_config = SimpleNamespace(max_in_flight=raw_value)

    # When: BrokerPoller max-in-flight config coercion is exercised.
    resolved = resolve_configured_max_in_flight(execution_config)

    # Then: numeric values are clamped and invalid values use the legacy default.
    assert resolved == expected


def test_resolve_commit_debounce_values_coerce_invalid_and_boundary_values():
    # Given: commit debounce config contains boundary and invalid values.
    pc_conf = SimpleNamespace(
        commit_debounce_completion_threshold=0,
        commit_debounce_interval_ms=-5,
    )
    bool_threshold_conf = SimpleNamespace(commit_debounce_completion_threshold=True)
    invalid_interval_conf = SimpleNamespace(commit_debounce_interval_ms="slow")

    # When: BrokerPoller commit debounce config coercion is exercised.
    threshold = resolve_commit_debounce_completion_threshold(pc_conf)
    interval = resolve_commit_debounce_interval_seconds(pc_conf)
    bool_threshold = resolve_commit_debounce_completion_threshold(bool_threshold_conf)
    invalid_interval = resolve_commit_debounce_interval_seconds(invalid_interval_conf)

    # Then: boundary values are clamped and invalid values use safe defaults.
    assert threshold == 1
    assert interval == 0.0
    assert bool_threshold == 100
    assert invalid_interval == 0.1


def test_resolve_shutdown_policy_and_drain_timeout_prefer_execution_resolver():
    # Given: execution config exposes both a raw timeout and resolver method.
    execution_config = SimpleNamespace(
        shutdown_policy="abort",
        shutdown_drain_timeout_ms=9999,
        resolve_shutdown_drain_timeout_ms=lambda: 250,
    )
    fallback_config = SimpleNamespace(shutdown_drain_timeout_ms=125)

    # When: BrokerPoller shutdown config coercion is exercised.
    policy = resolve_shutdown_policy(execution_config)
    resolved_timeout = resolve_shutdown_drain_timeout_seconds(execution_config)
    fallback_timeout = resolve_shutdown_drain_timeout_seconds(fallback_config)

    # Then: policy is stringified and resolver output wins over raw timeout.
    assert policy == "abort"
    assert resolved_timeout == pytest.approx(0.25)
    assert fallback_timeout == pytest.approx(0.125)


def test_coerce_adaptive_backpressure_config_rejects_bool_numbers():
    # Given: adaptive backpressure config contains mixed valid and invalid fields.
    raw_config = SimpleNamespace(
        enabled="yes",
        min_in_flight=True,
        scale_up_step=7.9,
        scale_down_step="bad",
        cooldown_ms=25,
        lag_scale_up_threshold=3,
        low_latency_threshold_ms=12,
        high_latency_threshold_ms=False,
    )

    # When: BrokerPoller adaptive backpressure config coercion is exercised.
    config = coerce_adaptive_backpressure_config(raw_config)

    # Then: valid numeric fields are kept and bool-as-number fields use defaults.
    assert config.enabled is False
    assert config.min_in_flight == 1
    assert config.scale_up_step == 7
    assert config.scale_down_step == 16
    assert config.cooldown_ms == 25
    assert config.lag_scale_up_threshold == 3
    assert config.low_latency_threshold_ms == 12.0
    assert config.high_latency_threshold_ms == 100.0


def test_coerce_adaptive_concurrency_config_reads_named_child_config():
    # Given: adaptive concurrency settings live under a named child config.
    parent = SimpleNamespace(
        adaptive_concurrency=SimpleNamespace(
            enabled=True,
            min_in_flight=4.9,
            scale_up_step=False,
            scale_down_step=12,
            cooldown_ms="later",
        )
    )

    # When: BrokerPoller adaptive concurrency config coercion is exercised.
    config = coerce_adaptive_concurrency_config(parent, "adaptive_concurrency")

    # Then: the named child is read and invalid values fall back per field.
    assert config.enabled is True
    assert config.min_in_flight == 4
    assert config.scale_up_step == 32
    assert config.scale_down_step == 12
    assert config.cooldown_ms == 1000
