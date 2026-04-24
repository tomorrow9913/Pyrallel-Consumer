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


def test_process_config_transport_mode_defaults_to_shared_queue() -> None:
    config = ProcessConfig()

    assert config.transport_mode == "shared_queue"


def test_process_config_transport_mode_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PROCESS_TRANSPORT_MODE", "worker_pipes")

    config = ProcessConfig()

    assert config.transport_mode == "worker_pipes"

    monkeypatch.delenv("PROCESS_TRANSPORT_MODE", raising=False)


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


def test_execution_config_rejects_negative_consumer_stop_timeout() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ = ExecutionConfig(consumer_task_stop_timeout_ms=-1)

    assert "consumer_task_stop_timeout_ms" in str(excinfo.value)


def test_execution_config_accepts_zero_consumer_stop_timeout() -> None:
    config = ExecutionConfig(consumer_task_stop_timeout_ms=0)

    assert config.consumer_task_stop_timeout_ms == 0


def test_process_config_rejects_invalid_transport_mode() -> None:
    with pytest.raises(ValidationError) as excinfo:
        _ = ProcessConfig(transport_mode="invalid")

    assert "transport_mode" in str(excinfo.value)


def test_kafka_config_exposes_canonical_snake_case_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    for name in (
        "BOOTSTRAP_SERVERS",
        "CONSUMER_GROUP",
        "DLQ_TOPIC_SUFFIX",
        "DLQ_FLUSH_TIMEOUT_MS",
        "AUTO_OFFSET_RESET",
        "ENABLE_AUTO_COMMIT",
        "SESSION_TIMEOUT_MS",
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_CONSUMER_GROUP",
        "KAFKA_DLQ_TOPIC_SUFFIX",
        "KAFKA_DLQ_FLUSH_TIMEOUT_MS",
        "KAFKA_AUTO_OFFSET_RESET",
        "KAFKA_ENABLE_AUTO_COMMIT",
        "KAFKA_SESSION_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)

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


def test_kafka_config_includes_allowlisted_security_fields_in_client_configs() -> None:
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        consumer_group="secure-group",
        security_protocol="SASL_SSL",
        sasl_mechanisms="SCRAM-SHA-512",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_ca_location="/etc/kafka/ca.pem",
        ssl_certificate_location="/etc/kafka/client.pem",
        ssl_key_location="/etc/kafka/client.key",
        ssl_key_password="key-secret",
    )

    expected_security_config = {
        "bootstrap.servers": "secure-1:9093,secure-2:9093",
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "SCRAM-SHA-512",
        "sasl.username": "pyrallel-user",
        "sasl.password": "super-secret",
        "ssl.ca.location": "/etc/kafka/ca.pem",
        "ssl.certificate.location": "/etc/kafka/client.pem",
        "ssl.key.location": "/etc/kafka/client.key",
        "ssl.key.password": "key-secret",
    }

    producer_config = config.get_producer_config()
    consumer_config = config.get_consumer_config()
    admin_config = config.get_admin_config()

    for key, value in expected_security_config.items():
        assert producer_config[key] == value
        assert consumer_config[key] == value
        assert admin_config[key] == value
    assert consumer_config["group.id"] == "secure-group"


def test_kafka_config_masks_secret_security_fields_in_snapshots() -> None:
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        security_protocol="SASL_SSL",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_key_password="key-secret",
    )

    dumped_json = config.model_dump_json()
    redacted_snapshot = config.dump_to_rdkafka()
    rdkafka_snapshot = repr(redacted_snapshot)

    assert "super-secret" not in dumped_json
    assert "key-secret" not in dumped_json
    assert "super-secret" not in rdkafka_snapshot
    assert "key-secret" not in rdkafka_snapshot
    assert redacted_snapshot["bootstrap.servers"] == "secure-1:9093,secure-2:9093"


def test_kafka_config_get_rdkafka_config_includes_secret_security_fields() -> None:
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        security_protocol="SASL_SSL",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_key_password="key-secret",
    )

    rdkafka_config = config.get_rdkafka_config()

    assert rdkafka_config["bootstrap.servers"] == "secure-1:9093,secure-2:9093"
    assert rdkafka_config["security.protocol"] == "SASL_SSL"
    assert rdkafka_config["sasl.username"] == "pyrallel-user"
    assert rdkafka_config["sasl.password"] == "super-secret"
    assert rdkafka_config["ssl.key.password"] == "key-secret"


def test_kafka_config_omits_blank_security_fields_from_client_configs() -> None:
    config = KafkaConfig(
        _env_file=None,
        security_protocol="",
        sasl_mechanisms="  ",
        sasl_username="",
        sasl_password="",
        ssl_ca_location="",
        ssl_certificate_location="",
        ssl_key_location="",
        ssl_key_password="",
    )

    producer_config = config.get_producer_config()

    assert "security.protocol" not in producer_config
    assert "sasl.mechanisms" not in producer_config
    assert "sasl.username" not in producer_config
    assert "sasl.password" not in producer_config
    assert "ssl.ca.location" not in producer_config
    assert "ssl.certificate.location" not in producer_config
    assert "ssl.key.location" not in producer_config
    assert "ssl.key.password" not in producer_config


def test_kafka_config_preserves_boundary_whitespace_in_secret_fields() -> None:
    config = KafkaConfig(
        _env_file=None,
        sasl_password=" leading-trailing ",
        ssl_key_password="\tkey secret\n",
    )

    producer_config = config.get_producer_config()

    assert producer_config["sasl.password"] == " leading-trailing "
    assert producer_config["ssl.key.password"] == "\tkey secret\n"


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


def test_kafka_config_security_env_vars_populate_allowlisted_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("KAFKA_SASL_MECHANISMS", "SCRAM-SHA-512")
    monkeypatch.setenv("KAFKA_SASL_USERNAME", "env-user")
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", "env-secret")
    monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/env-ca.pem")
    monkeypatch.setenv("KAFKA_SSL_CERTIFICATE_LOCATION", "/env-cert.pem")
    monkeypatch.setenv("KAFKA_SSL_KEY_LOCATION", "/env-key.pem")
    monkeypatch.setenv("KAFKA_SSL_KEY_PASSWORD", "env-key-secret")

    config = KafkaConfig(_env_file=None)

    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanisms == "SCRAM-SHA-512"
    assert config.sasl_username == "env-user"
    assert config.sasl_password is not None
    assert config.sasl_password.get_secret_value() == "env-secret"
    assert config.ssl_ca_location == "/env-ca.pem"
    assert config.ssl_certificate_location == "/env-cert.pem"
    assert config.ssl_key_location == "/env-key.pem"
    assert config.ssl_key_password is not None
    assert config.ssl_key_password.get_secret_value() == "env-key-secret"

    monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)
    monkeypatch.delenv("KAFKA_SASL_MECHANISMS", raising=False)
    monkeypatch.delenv("KAFKA_SASL_USERNAME", raising=False)
    monkeypatch.delenv("KAFKA_SASL_PASSWORD", raising=False)
    monkeypatch.delenv("KAFKA_SSL_CA_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_CERTIFICATE_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_KEY_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_KEY_PASSWORD", raising=False)
