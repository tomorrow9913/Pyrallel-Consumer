from pyrallel_consumer.config import AdaptiveBackpressureConfig
from pyrallel_consumer.control_plane.adaptive_backpressure import (
    AdaptiveBackpressureController,
)


def test_controller_scales_down_when_paused_or_latency_is_high() -> None:
    controller = AdaptiveBackpressureController(
        configured_max_in_flight=100,
        config=AdaptiveBackpressureConfig(
            enabled=True,
            min_in_flight=40,
            scale_down_step=15,
            cooldown_ms=0,
            high_latency_threshold_ms=75.0,
        ),
    )

    assert (
        controller.evaluate(
            total_true_lag=0,
            total_queued=0,
            avg_completion_latency_seconds=0.080,
            is_paused=False,
        )
        == 85
    )
    assert controller.last_decision == "scale_down"

    assert (
        controller.evaluate(
            total_true_lag=0,
            total_queued=0,
            avg_completion_latency_seconds=0.010,
            is_paused=True,
        )
        == 70
    )
    assert controller.last_decision == "scale_down"


def test_controller_scales_up_when_lag_is_high_and_latency_is_healthy() -> None:
    controller = AdaptiveBackpressureController(
        configured_max_in_flight=120,
        config=AdaptiveBackpressureConfig(
            enabled=True,
            min_in_flight=40,
            scale_up_step=20,
            cooldown_ms=0,
            lag_scale_up_threshold=200,
            low_latency_threshold_ms=25.0,
        ),
    )
    controller._effective_max_in_flight = 60

    assert (
        controller.evaluate(
            total_true_lag=300,
            total_queued=0,
            avg_completion_latency_seconds=0.015,
            is_paused=False,
        )
        == 80
    )
    assert controller.last_decision == "scale_up"

    assert (
        controller.evaluate(
            total_true_lag=1000,
            total_queued=0,
            avg_completion_latency_seconds=0.010,
            is_paused=False,
        )
        == 100
    )


def test_controller_holds_when_disabled_or_in_cooldown() -> None:
    disabled = AdaptiveBackpressureController(
        configured_max_in_flight=100,
        config=AdaptiveBackpressureConfig(enabled=False),
    )

    assert (
        disabled.evaluate(
            total_true_lag=1000,
            total_queued=0,
            avg_completion_latency_seconds=0.001,
            is_paused=False,
        )
        == 100
    )
    assert disabled.last_decision == "disabled"

    controller = AdaptiveBackpressureController(
        configured_max_in_flight=100,
        config=AdaptiveBackpressureConfig(
            enabled=True,
            min_in_flight=20,
            scale_down_step=10,
            cooldown_ms=10_000,
            high_latency_threshold_ms=50.0,
        ),
    )

    first_limit = controller.evaluate(
        total_true_lag=0,
        total_queued=0,
        avg_completion_latency_seconds=0.090,
        is_paused=False,
        now_monotonic=10.0,
    )
    second_limit = controller.evaluate(
        total_true_lag=1000,
        total_queued=0,
        avg_completion_latency_seconds=0.005,
        is_paused=False,
        now_monotonic=12.0,
    )

    assert first_limit == 90
    assert second_limit == 90
    assert controller.last_decision == "cooldown"
