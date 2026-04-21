import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

from pyrallel_consumer.config import (
    ExecutionConfig,
    KafkaConfig,
    MetricsConfig,
    ParallelConsumerConfig,
    ProcessConfig,
)
from pyrallel_consumer.dto import ExecutionMode, OrderingMode


def test_parallel_consumer_config_defaults():
    config = ParallelConsumerConfig()

    assert config.blocking_warn_seconds == 5.0
    assert config.message_cache_max_bytes == 64 * 1024 * 1024
    assert config.max_blocking_duration_ms == 0
    assert config.ordering_mode == OrderingMode.KEY_HASH
    assert config.strict_completion_monitor_enabled is True
    assert config.adaptive_concurrency.enabled is False
    assert config.adaptive_concurrency.min_in_flight == 0
    assert config.poison_message.enabled is False
    assert config.poison_message.failure_threshold == 3
    assert config.poison_message.cooldown_ms == 30000


def test_parallel_consumer_config_poison_message_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED", "true")
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__COOLDOWN_MS", "7500")

    config = ParallelConsumerConfig()

    assert config.poison_message.enabled is True
    assert config.poison_message.failure_threshold == 2
    assert config.poison_message.cooldown_ms == 7500

    monkeypatch.delenv("PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED", raising=False)
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_POISON_MESSAGE__FAILURE_THRESHOLD",
        raising=False,
    )
    monkeypatch.delenv("PARALLEL_CONSUMER_POISON_MESSAGE__COOLDOWN_MS", raising=False)


def test_parallel_consumer_config_adaptive_concurrency_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED", "true")
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__MIN_IN_FLIGHT",
        "64",
    )
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_UP_STEP",
        "32",
    )

    config = ParallelConsumerConfig()

    assert config.adaptive_concurrency.enabled is True
    assert config.adaptive_concurrency.min_in_flight == 64
    assert config.adaptive_concurrency.scale_up_step == 32

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED",
        raising=False,
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__MIN_IN_FLIGHT",
        raising=False,
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_UP_STEP",
        raising=False,
    )


def test_execution_config_shutdown_policy_defaults_to_graceful() -> None:
    config = ExecutionConfig(mode=ExecutionMode.ASYNC)

    assert config.shutdown_policy == "graceful"
    assert config.consumer_task_stop_timeout_ms == 5000
    assert config.shutdown_drain_timeout_ms == 5000
    assert config.resolve_shutdown_drain_timeout_ms() == 5000


def test_execution_config_shutdown_policy_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("EXECUTION_SHUTDOWN_POLICY", "abort")
    monkeypatch.setenv("EXECUTION_SHUTDOWN_DRAIN_TIMEOUT_MS", "250")

    config = ExecutionConfig()

    assert config.shutdown_policy == "abort"
    assert config.shutdown_drain_timeout_ms == 250
    assert config.resolve_shutdown_drain_timeout_ms() == 0

    monkeypatch.delenv("EXECUTION_SHUTDOWN_POLICY", raising=False)
    monkeypatch.delenv("EXECUTION_SHUTDOWN_DRAIN_TIMEOUT_MS", raising=False)


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


@pytest.mark.parametrize(
    ("config_type", "kwargs", "field_name"),
    [
        (ExecutionConfig, {"max_in_flight": 0}, "max_in_flight"),
        (ExecutionConfig, {"max_in_flight": -1}, "max_in_flight"),
        (ProcessConfig, {"process_count": 0}, "process_count"),
        (ProcessConfig, {"queue_size": 0}, "queue_size"),
        (ProcessConfig, {"batch_size": 0}, "batch_size"),
        (ProcessConfig, {"msgpack_max_bytes": 0}, "msgpack_max_bytes"),
        (MetricsConfig, {"port": 0}, "port"),
        (MetricsConfig, {"port": 65536}, "port"),
    ],
)
def test_resource_config_rejects_unsafe_bounds(
    config_type, kwargs: dict[str, int], field_name: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ = config_type(**kwargs)

    assert field_name in str(excinfo.value)


def test_execution_config_consumer_stop_timeout_default() -> None:
    config = ExecutionConfig(mode=ExecutionMode.ASYNC)

    assert config.consumer_task_stop_timeout_ms == 5000
    assert config.shutdown_drain_timeout_ms == 5000
    assert config.resolve_shutdown_drain_timeout_ms() == 5000


def test_kafka_config_exposes_canonical_snake_case_fields() -> None:
    config = KafkaConfig(_env_file=None)

    assert config.bootstrap_servers == ["localhost:9092"]
    assert config.consumer_group == "pyrallel-consumer-group"
    assert config.dlq_topic_suffix == ".dlq"
    assert config.auto_offset_reset == "earliest"
    assert config.enable_auto_commit is False
    assert config.session_timeout_ms == 60000


def test_kafka_config_accepts_snake_case_constructor_fields() -> None:
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["kafka-1:9092", "kafka-2:9092"],
        consumer_group="demo-group",
        dlq_topic_suffix=".failed",
        dlq_flush_timeout_ms=1234,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        session_timeout_ms=7777,
    )

    assert config.bootstrap_servers == ["kafka-1:9092", "kafka-2:9092"]
    assert config.consumer_group == "demo-group"
    assert config.dlq_topic_suffix == ".failed"
    assert config.dlq_flush_timeout_ms == 1234
    assert config.auto_offset_reset == "latest"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 7777


def test_kafka_config_normalizes_string_bootstrap_servers_after_assignment() -> None:
    config = KafkaConfig(_env_file=None)

    config.bootstrap_servers = "kafka-1:9092,kafka-2:9092"

    assert (
        config.get_producer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )
    assert (
        config.get_consumer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )


def test_kafka_config_preserves_list_bootstrap_servers_after_assignment() -> None:
    config = KafkaConfig(_env_file=None)

    config.bootstrap_servers = ["kafka-1:9092", "kafka-2:9092"]

    assert (
        config.get_producer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )
    assert (
        config.get_consumer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )


def test_kafka_config_keeps_legacy_uppercase_aliases() -> None:
    config = KafkaConfig(
        _env_file=None,
        BOOTSTRAP_SERVERS=["alias-broker:9092"],
        CONSUMER_GROUP="alias-group",
        DLQ_TOPIC_SUFFIX=".alias",
        DLQ_FLUSH_TIMEOUT_MS=4321,
        AUTO_OFFSET_RESET="none",
        ENABLE_AUTO_COMMIT=True,
        SESSION_TIMEOUT_MS=8765,
    )

    assert config.bootstrap_servers == ["alias-broker:9092"]
    assert config.consumer_group == "alias-group"
    assert config.dlq_topic_suffix == ".alias"
    assert config.dlq_flush_timeout_ms == 4321
    assert config.auto_offset_reset == "none"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 8765

    config.BOOTSTRAP_SERVERS = ["mutated-broker:9092"]
    config.CONSUMER_GROUP = "mutated-group"
    config.DLQ_TOPIC_SUFFIX = ".mutated"
    config.DLQ_FLUSH_TIMEOUT_MS = 2468
    config.AUTO_OFFSET_RESET = "earliest"
    config.ENABLE_AUTO_COMMIT = False
    config.SESSION_TIMEOUT_MS = 1357

    assert config.bootstrap_servers == ["mutated-broker:9092"]
    assert config.consumer_group == "mutated-group"
    assert config.dlq_topic_suffix == ".mutated"
    assert config.dlq_flush_timeout_ms == 2468
    assert config.auto_offset_reset == "earliest"
    assert config.enable_auto_commit is False
    assert config.session_timeout_ms == 1357


def test_kafka_config_env_vars_populate_canonical_snake_case_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "env-a:9092,env-b:9092")
    monkeypatch.setenv("KAFKA_CONSUMER_GROUP", "env-group")
    monkeypatch.setenv("KAFKA_DLQ_TOPIC_SUFFIX", ".env")
    monkeypatch.setenv("KAFKA_DLQ_FLUSH_TIMEOUT_MS", "999")
    monkeypatch.setenv("KAFKA_AUTO_OFFSET_RESET", "latest")
    monkeypatch.setenv("KAFKA_ENABLE_AUTO_COMMIT", "true")
    monkeypatch.setenv("KAFKA_SESSION_TIMEOUT_MS", "2222")

    config = KafkaConfig(_env_file=None)

    assert config.bootstrap_servers == ["env-a:9092", "env-b:9092"]
    assert config.consumer_group == "env-group"
    assert config.dlq_topic_suffix == ".env"
    assert config.dlq_flush_timeout_ms == 999
    assert config.auto_offset_reset == "latest"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 2222

    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("KAFKA_CONSUMER_GROUP", raising=False)
    monkeypatch.delenv("KAFKA_DLQ_TOPIC_SUFFIX", raising=False)
    monkeypatch.delenv("KAFKA_DLQ_FLUSH_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("KAFKA_AUTO_OFFSET_RESET", raising=False)
    monkeypatch.delenv("KAFKA_ENABLE_AUTO_COMMIT", raising=False)
    monkeypatch.delenv("KAFKA_SESSION_TIMEOUT_MS", raising=False)
