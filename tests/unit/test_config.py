from pyrallel_consumer.config import ParallelConsumerConfig


def test_parallel_consumer_config_defaults():
    config = ParallelConsumerConfig()

    assert config.blocking_warn_seconds == 5.0
    assert config.max_blocking_duration_ms == 0


def test_parallel_consumer_config_env_override(monkeypatch):
    monkeypatch.setenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", "2500")

    config = ParallelConsumerConfig()

    assert config.max_blocking_duration_ms == 2500

    monkeypatch.delenv("PARALLEL_CONSUMER__MAX_BLOCKING_DURATION_MS", raising=False)
