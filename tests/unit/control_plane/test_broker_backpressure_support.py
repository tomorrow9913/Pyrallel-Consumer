# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_backpressure_support.py
# Role: Verifies BrokerPoller backpressure support outside the poller facade.
# Extend here when adaptive concurrency or broker pause orchestration moves.

from unittest.mock import MagicMock

import pytest

from pyrallel_consumer.config import (
    AdaptiveBackpressureConfig,
    AdaptiveConcurrencyConfig,
)
from pyrallel_consumer.control_plane.adaptive_backpressure import (
    AdaptiveBackpressureController,
)
from pyrallel_consumer.control_plane.adaptive_concurrency import (
    AdaptiveConcurrencyController,
)
from pyrallel_consumer.control_plane.broker_backpressure_support import (
    BrokerBackpressureSupport,
)

_MISSING = object()


def _make_support(
    *,
    configured_max_in_flight: int = 100,
    current_limit: int = 100,
    total_in_flight: int = 0,
    total_queued: int = 0,
    total_true_lag: int = 0,
    paused: bool = False,
    queue_max_messages: int = 0,
    consumer=_MISSING,
    backpressure_config=None,
    concurrency_config=None,
    avg_completion_latency_seconds=None,
    check_runtime_backpressure=None,
):
    """Create BrokerBackpressureSupport with mutable state and test doubles."""
    state = {
        "current_limit": current_limit,
        "resume_limit": max(1, int(current_limit * 0.7)),
        "paused": paused,
    }
    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = total_in_flight
    work_manager.get_average_completion_latency_seconds.return_value = (
        avg_completion_latency_seconds
    )
    if backpressure_config is None:
        backpressure_config = AdaptiveBackpressureConfig(enabled=False)
    if concurrency_config is None:
        concurrency_config = AdaptiveConcurrencyConfig(enabled=False)
    if consumer is _MISSING:
        consumer = MagicMock()
    if check_runtime_backpressure is None:
        check_runtime_backpressure = MagicMock(return_value=paused)

    support = BrokerBackpressureSupport(
        configured_max_in_flight=configured_max_in_flight,
        adaptive_backpressure_controller=AdaptiveBackpressureController(
            configured_max_in_flight=configured_max_in_flight,
            config=backpressure_config,
        ),
        adaptive_concurrency_controller=AdaptiveConcurrencyController(
            concurrency_config,
            configured_max_in_flight=configured_max_in_flight,
        ),
        work_manager=work_manager,
        get_consumer=lambda: consumer,
        get_total_queued_messages=lambda: _async_int(total_queued),
        get_total_true_lag=lambda: total_true_lag,
        get_current_limit=lambda: state["current_limit"],
        set_current_limit=lambda value: state.__setitem__("current_limit", value),
        set_resume_limit=lambda value: state.__setitem__("resume_limit", value),
        get_queue_max_messages=lambda: queue_max_messages,
        get_is_paused=lambda: bool(state["paused"]),
        set_is_paused=lambda value: state.__setitem__("paused", value),
        check_runtime_backpressure=check_runtime_backpressure,
        logger=MagicMock(),
    )
    return support, state, work_manager, check_runtime_backpressure


def test_backpressure_support_updates_runtime_limit_and_resume_threshold() -> None:
    # Given: the live in-flight limit differs from the requested adaptive limit.
    support, state, work_manager, _ = _make_support(current_limit=100)

    # When: backpressure support installs the new runtime limit.
    support.set_runtime_max_in_flight(80)

    # Then: the poller-facing limit, resume threshold, and work manager limit sync.
    assert state["current_limit"] == 80
    assert state["resume_limit"] == 56
    work_manager.set_max_in_flight_messages.assert_called_once_with(80)


def test_backpressure_support_clamps_runtime_limit_to_configured_max() -> None:
    # Given: an adaptive policy asks for more than the configured ceiling.
    support, state, work_manager, _ = _make_support(
        configured_max_in_flight=100,
        current_limit=50,
    )

    # When: the oversized runtime limit is applied.
    support.set_runtime_max_in_flight(200)

    # Then: the effective limit is clamped before updating downstream state.
    assert state["current_limit"] == 100
    assert state["resume_limit"] == 70
    work_manager.set_max_in_flight_messages.assert_called_once_with(100)


def test_backpressure_support_applies_adaptive_backpressure_limit() -> None:
    # Given: adaptive backpressure observes completion latency above the threshold.
    support, state, work_manager, _ = _make_support(
        configured_max_in_flight=100,
        current_limit=100,
        avg_completion_latency_seconds=0.075,
        backpressure_config=AdaptiveBackpressureConfig(
            enabled=True,
            min_in_flight=40,
            scale_down_step=20,
            cooldown_ms=0,
            high_latency_threshold_ms=50.0,
        ),
    )

    # When: adaptive backpressure evaluates the current queue pressure.
    support.maybe_adjust_adaptive_backpressure(total_queued=0)

    # Then: the runtime limit is reduced and propagated to the work manager.
    assert state["current_limit"] == 80
    assert state["resume_limit"] == 56
    work_manager.set_max_in_flight_messages.assert_called_once_with(80)


def test_backpressure_support_applies_adaptive_concurrency_sample() -> None:
    # Given: lag saturates the current adaptive concurrency limit.
    support, state, work_manager, _ = _make_support(
        configured_max_in_flight=128,
        current_limit=64,
        total_in_flight=64,
        total_true_lag=320,
        concurrency_config=AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=32,
            scale_up_step=16,
            scale_down_step=24,
            cooldown_ms=0,
        ),
    )

    # When: adaptive concurrency evaluates a fresh broker sample.
    support.maybe_adjust_adaptive_concurrency(total_queued=0)

    # Then: the support object raises the runtime limit through the shared setter.
    assert state["current_limit"] == 80
    assert state["resume_limit"] == 56
    work_manager.set_max_in_flight_messages.assert_called_once_with(80)


@pytest.mark.asyncio
async def test_backpressure_support_skips_runtime_check_when_no_transition_possible() -> (
    None
):
    # Given: adaptive controllers are disabled and current load is below the limit.
    support, state, _, check_runtime_backpressure = _make_support(
        current_limit=100,
        total_in_flight=10,
        total_queued=0,
        paused=False,
        queue_max_messages=0,
    )

    # When: broker backpressure is checked.
    await support.check_backpressure()

    # Then: broker pause/resume orchestration is not invoked for a no-op state.
    assert state["paused"] is False
    check_runtime_backpressure.assert_not_called()


@pytest.mark.asyncio
async def test_backpressure_support_delegates_runtime_transition_under_load() -> None:
    # Given: current load exceeds the active in-flight limit.
    support, state, _, check_runtime_backpressure = _make_support(
        current_limit=100,
        total_in_flight=101,
        total_queued=0,
        paused=False,
        check_runtime_backpressure=MagicMock(return_value=True),
    )

    # When: broker backpressure is checked.
    await support.check_backpressure()

    # Then: runtime backpressure owns pause/resume decisions and updates paused state.
    check_runtime_backpressure.assert_called_once_with(0)
    assert state["paused"] is True


@pytest.mark.asyncio
async def test_backpressure_support_requires_initialized_consumer() -> None:
    # Given: no Kafka consumer has been installed yet.
    support, _, _, _ = _make_support(consumer=None)

    # When/Then: backpressure checks fail fast instead of hiding setup errors.
    with pytest.raises(RuntimeError, match="Consumer must be initialized"):
        await support.check_backpressure()


async def _async_int(value: int) -> int:
    """Return an integer through an awaitable test helper."""
    return value
