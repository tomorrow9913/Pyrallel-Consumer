from pyrallel_consumer.config import AdaptiveConcurrencyConfig
from pyrallel_consumer.control_plane.adaptive_concurrency import (
    AdaptiveConcurrencyController,
    AdaptiveConcurrencySample,
)


def test_adaptive_concurrency_scales_up_when_lag_saturates_current_limit() -> None:
    # Given: inputs for `adaptive concurrency scales up when lag satur...` are prepared.
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

    # When: the adaptive concurrency controller code path is exercised.
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

    # Then: the expected `adaptive concurrency scales up when lag satur...` behavior is asserted.
    assert new_limit == 80


def test_adaptive_concurrency_scales_down_when_paused_under_pressure() -> None:
    # Given: inputs for `adaptive concurrency scales down when paused...` are prepared.
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

    # When: the adaptive concurrency controller code path is exercised.
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

    # Then: the expected `adaptive concurrency scales down when paused...` behavior is asserted.
    assert new_limit == 56


def test_adaptive_concurrency_respects_cooldown_between_adjustments() -> None:
    # Given: inputs for `adaptive concurrency respects cooldown betwee...` are prepared.
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

    # When: the adaptive concurrency controller code path is exercised.
    # Then: the expected `adaptive concurrency respects cooldown betwee...` behavior is asserted.
    assert controller.evaluate(sample, now_seconds=10.0) == 80
    assert controller.evaluate(sample, now_seconds=12.0) is None


def test_adaptive_concurrency_auto_min_resolves_to_quarter_ceiling() -> None:
    # Given: inputs for `adaptive concurrency auto min resolves to qua...` are prepared.
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

    # When: the adaptive concurrency controller code path is exercised.
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

    # Then: the expected `adaptive concurrency auto min resolves to qua...` behavior is asserted.
    assert new_limit == 32


def test_adaptive_concurrency_builds_runtime_snapshot() -> None:
    # Given: inputs for `adaptive concurrency builds runtime snapshot` are prepared.
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

    # When: the adaptive concurrency controller code path is exercised.
    snapshot = controller.build_runtime_snapshot(effective_max_in_flight=72)

    # Then: the expected `adaptive concurrency builds runtime snapshot` behavior is asserted.
    assert snapshot.configured_max_in_flight == 96
    assert snapshot.effective_max_in_flight == 72
    assert snapshot.min_in_flight == 24
    assert snapshot.scale_up_step == 12
    assert snapshot.scale_down_step == 18
    assert snapshot.cooldown_ms == 2500
