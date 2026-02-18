"""Unit tests for configuration models."""

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig


class TestExecutionConfigRetryFields:
    """Test ExecutionConfig retry/backoff configuration fields."""

    def test_execution_config_has_retry_defaults(self) -> None:
        """Verify ExecutionConfig includes retry fields with correct defaults."""
        config = ExecutionConfig()

        assert config.max_retries == 3
        assert config.retry_backoff_ms == 1000
        assert config.exponential_backoff is True
        assert config.max_retry_backoff_ms == 30000
        assert config.retry_jitter_ms == 200

    def test_execution_config_retry_fields_can_be_overridden(self) -> None:
        """Verify retry fields can be set via constructor."""
        config = ExecutionConfig(
            max_retries=5,
            retry_backoff_ms=2000,
            exponential_backoff=False,
            max_retry_backoff_ms=60000,
            retry_jitter_ms=500,
        )

        assert config.max_retries == 5
        assert config.retry_backoff_ms == 2000
        assert config.exponential_backoff is False
        assert config.max_retry_backoff_ms == 60000
        assert config.retry_jitter_ms == 500


class TestKafkaConfigDLQFields:
    """Test KafkaConfig DLQ-related configuration fields."""

    def test_kafka_config_has_dlq_enabled_default(self) -> None:
        """Verify KafkaConfig includes dlq_enabled field with default True."""
        config = KafkaConfig()

        assert config.dlq_enabled is True
        assert config.DLQ_TOPIC_SUFFIX == ".dlq"
        assert config.DLQ_FLUSH_TIMEOUT_MS == 5000

    def test_parallel_consumer_diag_defaults(self) -> None:
        config = KafkaConfig()

        assert config.parallel_consumer.diag_log_every == 1000
        assert config.parallel_consumer.blocking_warn_seconds == 5.0
        assert config.parallel_consumer.blocking_cache_ttl == 100

    def test_kafka_config_dlq_enabled_can_be_disabled(self) -> None:
        """Verify dlq_enabled can be set to False."""
        config = KafkaConfig(dlq_enabled=False)

        assert config.dlq_enabled is False

    def test_kafka_config_dlq_topic_suffix_can_be_overridden(self) -> None:
        """Verify DLQ_TOPIC_SUFFIX can be customized."""
        config = KafkaConfig(DLQ_TOPIC_SUFFIX="-failed")

        assert config.DLQ_TOPIC_SUFFIX == "-failed"

    def test_kafka_config_dlq_flush_timeout_can_be_overridden(self) -> None:
        config = KafkaConfig(DLQ_FLUSH_TIMEOUT_MS=2000)

        assert config.DLQ_FLUSH_TIMEOUT_MS == 2000


class TestKafkaConfigDumpToRdkafka:
    """Test dump_to_rdkafka excludes app-level fields."""

    def test_dump_to_rdkafka_excludes_dlq_enabled(self) -> None:
        """Verify dump_to_rdkafka does not include dlq_enabled field."""
        config = KafkaConfig(dlq_enabled=True)
        rdkafka_conf = config.dump_to_rdkafka()

        assert "dlq_enabled" not in rdkafka_conf
        assert "dlq.enabled" not in rdkafka_conf

    def test_dump_to_rdkafka_excludes_existing_app_fields(self) -> None:
        """Verify dump_to_rdkafka continues to exclude known app-level fields."""
        config = KafkaConfig()
        rdkafka_conf = config.dump_to_rdkafka()

        # Existing exclusions
        excluded_fields = [
            "bootstrap_servers",
            "consumer_group",
            "dlq_topic_suffix",
            "dlq_flush_timeout_ms",
            "auto_offset_reset",
            "enable_auto_commit",
            "session_timeout_ms",
            "rebalance_protocol",
        ]

        for field in excluded_fields:
            assert field not in rdkafka_conf
            assert field.replace("_", ".") not in rdkafka_conf
