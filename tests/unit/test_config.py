import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from pyrallel_consumer.config import ParallelConsumerConfig
from pyrallel_consumer.dto import OrderingMode


def test_parallel_consumer_config_defaults():
    config = ParallelConsumerConfig()

    assert config.blocking_warn_seconds == 5.0
    assert config.max_blocking_duration_ms == 0
    assert config.ordering_mode == OrderingMode.KEY_HASH
    assert config.strict_completion_monitor_enabled is True


def test_parallel_consumer_config_env_override(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", "2500")

    config = ParallelConsumerConfig()

    assert config.max_blocking_duration_ms == 2500

    monkeypatch.delenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", raising=False)


def test_parallel_consumer_config_rebalance_state_strategy_defaults() -> None:
    config = ParallelConsumerConfig()

    assert config.rebalance_state_strategy == "contiguous_only"


def test_parallel_consumer_config_rebalance_state_strategy_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY", "metadata_snapshot"
    )

    config = ParallelConsumerConfig()

    assert config.rebalance_state_strategy == "metadata_snapshot"

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY",
        raising=False,
    )


def test_parallel_consumer_config_ordering_mode_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_ORDERING_MODE", "partition")

    config = ParallelConsumerConfig()

    assert config.ordering_mode == OrderingMode.PARTITION

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ORDERING_MODE",
        raising=False,
    )


def test_parallel_consumer_config_can_disable_strict_completion_monitor(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED", "false")

    config = ParallelConsumerConfig()

    assert config.strict_completion_monitor_enabled is False

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED",
        raising=False,
    )


def test_parallel_consumer_config_rejects_zero_batch_and_worker_pool_size() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(poll_batch_size=0)
    assert "poll_batch_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(worker_pool_size=0)
    assert "worker_pool_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)
