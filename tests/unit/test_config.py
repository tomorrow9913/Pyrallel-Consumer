import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from pyrallel_consumer.config import ParallelConsumerConfig


def test_parallel_consumer_config_defaults():
    config = ParallelConsumerConfig()

    assert config.blocking_warn_seconds == 5.0
    assert config.max_blocking_duration_ms == 0


def test_parallel_consumer_config_env_override(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", "2500")

    config = ParallelConsumerConfig()

    assert config.max_blocking_duration_ms == 2500

    monkeypatch.delenv("PARALLEL_CONSUMER__MAX_BLOCKING_DURATION_MS", raising=False)


def test_parallel_consumer_config_rejects_zero_batch_and_worker_pool_size() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(poll_batch_size=0)
    assert "poll_batch_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(worker_pool_size=0)
    assert "worker_pool_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)
