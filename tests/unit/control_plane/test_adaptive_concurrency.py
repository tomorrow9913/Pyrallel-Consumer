from pyrallel_consumer.config import AdaptiveConcurrencyConfig
from pyrallel_consumer.control_plane.adaptive_concurrency import (
    AdaptiveConcurrencyController,
    AdaptiveConcurrencySample,
)


def test_adaptive_concurrency_scales_up_when_lag_saturates_current_limit() -> None:
    controller = AdaptiveConcurrencyController(
        AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=32,
            scale_up_step=16,
            scale_down_step=24,
            cooldown_ms=0,
        ),
        configured_max_in_flight=128,
    )

    new_limit = controller.evaluate(
        AdaptiveConcurrencySample(
            current_limit=64,
            total_in_flight=64,
            total_queued=0,
            total_true_lag=256,
            is_paused=False,
            queue_max_messages=0,
        )
    )

    assert new_limit == 80


def test_adaptive_concurrency_scales_down_when_paused_under_pressure() -> None:
    controller = AdaptiveConcurrencyController(
        AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=32,
            scale_up_step=16,
            scale_down_step=24,
            cooldown_ms=0,
        ),
        configured_max_in_flight=128,
    )

    new_limit = controller.evaluate(
        AdaptiveConcurrencySample(
            current_limit=80,
            total_in_flight=70,
            total_queued=20,
            total_true_lag=128,
            is_paused=True,
            queue_max_messages=256,
        )
    )

    assert new_limit == 56


def test_adaptive_concurrency_respects_cooldown_between_adjustments() -> None:
    controller = AdaptiveConcurrencyController(
        AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=32,
            scale_up_step=16,
            scale_down_step=24,
            cooldown_ms=5000,
        ),
        configured_max_in_flight=128,
    )
    sample = AdaptiveConcurrencySample(
        current_limit=64,
        total_in_flight=64,
        total_queued=0,
        total_true_lag=256,
        is_paused=False,
        queue_max_messages=0,
    )

    assert controller.evaluate(sample, now_seconds=10.0) == 80
    assert controller.evaluate(sample, now_seconds=12.0) is None


def test_adaptive_concurrency_auto_min_resolves_to_quarter_ceiling() -> None:
    controller = AdaptiveConcurrencyController(
        AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=0,
            scale_up_step=16,
            scale_down_step=64,
            cooldown_ms=0,
        ),
        configured_max_in_flight=128,
    )

    new_limit = controller.evaluate(
        AdaptiveConcurrencySample(
            current_limit=40,
            total_in_flight=40,
            total_queued=10,
            total_true_lag=0,
            is_paused=True,
            queue_max_messages=0,
        )
    )

    assert new_limit == 32


def test_adaptive_concurrency_builds_runtime_snapshot() -> None:
    controller = AdaptiveConcurrencyController(
        AdaptiveConcurrencyConfig(
            enabled=True,
            min_in_flight=24,
            scale_up_step=12,
            scale_down_step=18,
            cooldown_ms=2500,
        ),
        configured_max_in_flight=96,
    )

    snapshot = controller.build_runtime_snapshot(effective_max_in_flight=72)

    assert snapshot.configured_max_in_flight == 96
    assert snapshot.effective_max_in_flight == 72
    assert snapshot.min_in_flight == 24
    assert snapshot.scale_up_step == 12
    assert snapshot.scale_down_step == 18
    assert snapshot.cooldown_ms == 2500
