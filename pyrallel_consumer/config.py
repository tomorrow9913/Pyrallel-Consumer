import socket
from typing import ClassVar, Literal, cast

from pydantic import AliasChoices, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pyrallel_consumer.dto import DLQPayloadMode, ExecutionMode, OrderingMode


class AsyncConfig(BaseSettings):
    task_timeout_ms: int = 30000
    shutdown_grace_timeout_ms: int = 5000


class ProcessConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PROCESS_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    process_count: int = Field(default=8, gt=0)
    queue_size: int = Field(default=2048, gt=0)
    require_picklable_worker: bool = True
    batch_size: int = Field(default=64, gt=0)
    batch_bytes: str = "256KB"
    max_batch_wait_ms: int = 5
    flush_policy: Literal[
        "size_or_timer", "demand", "demand_min_residence"
    ] = "size_or_timer"
    demand_flush_min_residence_ms: int = 0
    shutdown_drain_timeout_ms: int = 5000
    worker_join_timeout_ms: int = 30000
    task_timeout_ms: int = 30000
    msgpack_max_bytes: int = Field(default=1_000_000, gt=0)
    max_tasks_per_child: int = 0
    recycle_jitter_ms: int = 0


class MetricsConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="METRICS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    port: int = Field(default=9091, ge=1, le=65535)


class AdaptiveConcurrencyConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    min_in_flight: int = Field(default=0, ge=0)
    scale_up_step: int = Field(default=32, gt=0)
    scale_down_step: int = Field(default=64, gt=0)
    cooldown_ms: int = Field(default=1000, ge=0)


class AdaptiveBackpressureConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    min_in_flight: int = Field(default=1, ge=1)
    scale_up_step: int = Field(default=16, gt=0)
    scale_down_step: int = Field(default=16, gt=0)
    cooldown_ms: int = Field(default=1000, ge=0)
    lag_scale_up_threshold: int = Field(default=0, ge=0)
    low_latency_threshold_ms: float = Field(default=25.0, ge=0.0)
    high_latency_threshold_ms: float = Field(default=100.0, ge=0.0)


class PoisonMessageConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    enabled: bool = False
    failure_threshold: int = Field(default=3, gt=0)
    cooldown_ms: int = Field(default=30000, ge=0)


class ExecutionConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="EXECUTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mode: ExecutionMode = ExecutionMode.ASYNC
    max_in_flight: int = Field(default=1000, gt=0)
    max_revoke_grace_ms: int = 500
    shutdown_policy: Literal["graceful", "abort"] = "graceful"
    consumer_task_stop_timeout_ms: int = Field(default=5000, ge=0)
    shutdown_drain_timeout_ms: int = 5000
    max_retries: int = 3
    retry_backoff_ms: int = 1000
    exponential_backoff: bool = True
    max_retry_backoff_ms: int = 30000
    retry_jitter_ms: int = 200
    async_config: AsyncConfig = AsyncConfig()
    process_config: ProcessConfig = ProcessConfig()

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, v: object) -> ExecutionMode:
        if isinstance(v, ExecutionMode):
            return v
        if isinstance(v, str):
            cleaned = v.split("#", 1)[0].strip()
            return ExecutionMode(cleaned)
        raise TypeError(f"mode must be str or ExecutionMode, got {type(v).__name__}")

    @property
    def max_in_flight_messages(self) -> int:
        return self.max_in_flight

    def resolve_shutdown_drain_timeout_ms(self) -> int:
        if self.shutdown_policy == "abort":
            return 0
        if "shutdown_drain_timeout_ms" in self.model_fields_set:
            return max(0, int(self.shutdown_drain_timeout_ms))
        if self.mode == ExecutionMode.ASYNC:
            return max(0, int(self.async_config.shutdown_grace_timeout_ms))
        return max(0, int(self.process_config.shutdown_drain_timeout_ms))


class ParallelConsumerConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="PARALLEL_CONSUMER_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    poll_batch_size: int = Field(default=1000, gt=0)
    worker_pool_size: int = Field(default=8, gt=0)
    ordering_mode: OrderingMode = OrderingMode.KEY_HASH
    queue_max_messages: int = 5000
    message_cache_max_bytes: int = Field(default=64 * 1024 * 1024, ge=0)
    diag_log_every: int = 1000
    blocking_warn_seconds: float = 5.0
    max_blocking_duration_ms: int = 0
    blocking_cache_ttl: int = 100
    strict_completion_monitor_enabled: bool = True
    adaptive_backpressure: AdaptiveBackpressureConfig = AdaptiveBackpressureConfig()
    adaptive_concurrency: AdaptiveConcurrencyConfig = AdaptiveConcurrencyConfig()
    poison_message: PoisonMessageConfig = PoisonMessageConfig()
    rebalance_state_strategy: Literal[
        "contiguous_only", "metadata_snapshot"
    ] = "contiguous_only"
    execution: ExecutionConfig = ExecutionConfig()

    @field_validator("ordering_mode", mode="before")
    @classmethod
    def _normalize_ordering_mode(cls, v: object) -> OrderingMode:
        if isinstance(v, OrderingMode):
            return v
        if isinstance(v, str):
            cleaned = v.split("#", 1)[0].strip()
            return OrderingMode(cleaned)
        raise TypeError(
            f"ordering_mode must be str or OrderingMode, got {type(v).__name__}"
        )


class KafkaConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="KAFKA_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        populate_by_name=True,
        extra="ignore",
    )

    bootstrap_servers: str | list[str] = Field(
        default_factory=lambda: ["localhost:9092"],
        validation_alias=AliasChoices(
            "bootstrap_servers",
            "BOOTSTRAP_SERVERS",
            "KAFKA_BOOTSTRAP_SERVERS",
        ),
    )
    consumer_group: str = Field(
        default="pyrallel-consumer-group",
        validation_alias=AliasChoices(
            "consumer_group",
            "CONSUMER_GROUP",
            "KAFKA_CONSUMER_GROUP",
        ),
    )
    dlq_topic_suffix: str = Field(
        default=".dlq",
        validation_alias=AliasChoices(
            "dlq_topic_suffix",
            "DLQ_TOPIC_SUFFIX",
            "KAFKA_DLQ_TOPIC_SUFFIX",
        ),
    )
    dlq_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "dlq_enabled",
            "DLQ_ENABLED",
            "KAFKA_DLQ_ENABLED",
        ),
    )
    dlq_payload_mode: DLQPayloadMode = Field(
        default=DLQPayloadMode.FULL,
        validation_alias=AliasChoices(
            "dlq_payload_mode",
            "DLQ_PAYLOAD_MODE",
            "KAFKA_DLQ_PAYLOAD_MODE",
        ),
    )
    dlq_flush_timeout_ms: int = Field(
        default=5000,
        validation_alias=AliasChoices(
            "dlq_flush_timeout_ms",
            "DLQ_FLUSH_TIMEOUT_MS",
            "KAFKA_DLQ_FLUSH_TIMEOUT_MS",
        ),
    )
    auto_offset_reset: Literal["earliest", "latest", "none"] = Field(
        default="earliest",
        validation_alias=AliasChoices(
            "auto_offset_reset",
            "AUTO_OFFSET_RESET",
            "KAFKA_AUTO_OFFSET_RESET",
        ),
    )
    enable_auto_commit: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "enable_auto_commit",
            "ENABLE_AUTO_COMMIT",
            "KAFKA_ENABLE_AUTO_COMMIT",
        ),
    )
    session_timeout_ms: int = Field(
        default=60000,
        validation_alias=AliasChoices(
            "session_timeout_ms",
            "SESSION_TIMEOUT_MS",
            "KAFKA_SESSION_TIMEOUT_MS",
        ),
    )
    security_protocol: (
        Literal["PLAINTEXT", "SSL", "SASL_PLAINTEXT", "SASL_SSL"] | None
    ) = Field(
        default=None,
        validation_alias=AliasChoices(
            "security_protocol",
            "SECURITY_PROTOCOL",
            "KAFKA_SECURITY_PROTOCOL",
            "security.protocol",
        ),
    )
    sasl_mechanisms: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "sasl_mechanisms",
            "SASL_MECHANISMS",
            "KAFKA_SASL_MECHANISMS",
            "sasl.mechanisms",
        ),
    )
    sasl_username: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "sasl_username",
            "SASL_USERNAME",
            "KAFKA_SASL_USERNAME",
            "sasl.username",
        ),
    )
    sasl_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "sasl_password",
            "SASL_PASSWORD",
            "KAFKA_SASL_PASSWORD",
            "sasl.password",
        ),
    )
    ssl_ca_location: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ssl_ca_location",
            "SSL_CA_LOCATION",
            "KAFKA_SSL_CA_LOCATION",
            "ssl.ca.location",
        ),
    )
    ssl_certificate_location: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ssl_certificate_location",
            "SSL_CERTIFICATE_LOCATION",
            "KAFKA_SSL_CERTIFICATE_LOCATION",
            "ssl.certificate.location",
        ),
    )
    ssl_key_location: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ssl_key_location",
            "SSL_KEY_LOCATION",
            "KAFKA_SSL_KEY_LOCATION",
            "ssl.key.location",
        ),
    )
    ssl_key_password: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "ssl_key_password",
            "SSL_KEY_PASSWORD",
            "KAFKA_SSL_KEY_PASSWORD",
            "ssl.key.password",
        ),
    )
    metrics: MetricsConfig = MetricsConfig()
    parallel_consumer: ParallelConsumerConfig = ParallelConsumerConfig()

    def _bootstrap_servers_csv(self) -> str:
        return ",".join(self._parse_bootstrap_servers(self.bootstrap_servers))

    def _get_security_config(self) -> dict[str, str]:
        security_config: dict[str, str] = {}
        field_to_config_key = {
            "security_protocol": "security.protocol",
            "sasl_mechanisms": "sasl.mechanisms",
            "sasl_username": "sasl.username",
            "sasl_password": "sasl.password",
            "ssl_ca_location": "ssl.ca.location",
            "ssl_certificate_location": "ssl.certificate.location",
            "ssl_key_location": "ssl.key.location",
            "ssl_key_password": "ssl.key.password",
        }

        for field_name, config_key in field_to_config_key.items():
            value = getattr(self, field_name)
            if value is None:
                continue
            if isinstance(value, SecretStr):
                security_config[config_key] = value.get_secret_value()
            else:
                security_config[config_key] = str(value)
        return security_config

    def get_producer_config(self) -> dict[str, str]:
        config = {
            "bootstrap.servers": self._bootstrap_servers_csv(),
            "client.id": socket.gethostname(),
        }
        config.update(self._get_security_config())
        return config

    def get_consumer_config(self) -> dict[str, str | int | bool]:
        config: dict[str, str | int | bool] = {
            "bootstrap.servers": self._bootstrap_servers_csv(),
            "group.id": self.consumer_group,
            "auto.offset.reset": self.auto_offset_reset,
            "enable.auto.commit": self.enable_auto_commit,
            "session.timeout.ms": self.session_timeout_ms,
        }
        config.update(self._get_security_config())
        return config

    def get_admin_config(self) -> dict[str, str]:
        config = {
            "bootstrap.servers": self._bootstrap_servers_csv(),
        }
        config.update(self._get_security_config())
        return config

    @field_validator(
        "security_protocol",
        "sasl_mechanisms",
        "sasl_username",
        "sasl_password",
        "ssl_ca_location",
        "ssl_certificate_location",
        "ssl_key_location",
        "ssl_key_password",
        mode="before",
    )
    @classmethod
    def _normalize_optional_security_field(cls, v: object) -> object:
        if isinstance(v, str):
            cleaned = v.strip()
            if cleaned == "":
                return None
            return cleaned
        return v

    @field_validator("dlq_payload_mode", mode="before")
    @classmethod
    def _normalize_dlq_mode(cls, v: object) -> DLQPayloadMode:
        if isinstance(v, DLQPayloadMode):
            return v
        if isinstance(v, str):
            cleaned = v.split("#", 1)[0].strip()
            return DLQPayloadMode(cleaned)
        raise TypeError(
            f"dlq_payload_mode must be str or DLQPayloadMode, got {type(v).__name__}"
        )

    @field_validator("bootstrap_servers", mode="before")
    @classmethod
    def _parse_bootstrap_servers(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            for item in cast(list[object], v):
                if not isinstance(item, str):
                    raise TypeError("bootstrap_servers list entries must be str")
            string_list = cast(list[str], v)
            cleaned: list[str] = [item.strip() for item in string_list if item.strip()]
            return cleaned
        raise TypeError(
            "bootstrap_servers must be str or list[str], got %s" % type(v).__name__
        )

    def dump_to_rdkafka(self) -> dict[str, object]:
        data: dict[str, object] = self.model_dump(exclude_none=True)

        _exclude = {
            "bootstrap_servers",
            "consumer_group",
            "dlq_topic_suffix",
            "dlq_payload_mode",
            "dlq_enabled",
            "dlq_flush_timeout_ms",
            "auto_offset_reset",
            "enable_auto_commit",
            "session_timeout_ms",
            "sasl_password",
            "ssl_key_password",
            "rebalance_protocol",
            "metrics",
            "parallel_consumer",
        }

        conf: dict[str, object] = {}
        for k, v in data.items():
            if k in _exclude:
                continue
            conf[k.replace("_", ".")] = v

        if self.model_extra:
            extras_source = cast(dict[str, object], self.model_extra)
            for k, v in extras_source.items():
                conf[k.replace("_", ".").lower()] = v
        return conf

    @property
    def BOOTSTRAP_SERVERS(self) -> list[str]:
        return self._parse_bootstrap_servers(self.bootstrap_servers)

    @BOOTSTRAP_SERVERS.setter
    def BOOTSTRAP_SERVERS(self, value: str | list[str]) -> None:
        self.bootstrap_servers = self._parse_bootstrap_servers(value)

    @property
    def CONSUMER_GROUP(self) -> str:
        return self.consumer_group

    @CONSUMER_GROUP.setter
    def CONSUMER_GROUP(self, value: str) -> None:
        self.consumer_group = value

    @property
    def DLQ_TOPIC_SUFFIX(self) -> str:
        return self.dlq_topic_suffix

    @DLQ_TOPIC_SUFFIX.setter
    def DLQ_TOPIC_SUFFIX(self, value: str) -> None:
        self.dlq_topic_suffix = value

    @property
    def DLQ_FLUSH_TIMEOUT_MS(self) -> int:
        return self.dlq_flush_timeout_ms

    @DLQ_FLUSH_TIMEOUT_MS.setter
    def DLQ_FLUSH_TIMEOUT_MS(self, value: int) -> None:
        self.dlq_flush_timeout_ms = value

    @property
    def AUTO_OFFSET_RESET(self) -> Literal["earliest", "latest", "none"]:
        return self.auto_offset_reset

    @AUTO_OFFSET_RESET.setter
    def AUTO_OFFSET_RESET(self, value: Literal["earliest", "latest", "none"]) -> None:
        self.auto_offset_reset = value

    @property
    def ENABLE_AUTO_COMMIT(self) -> bool:
        return self.enable_auto_commit

    @ENABLE_AUTO_COMMIT.setter
    def ENABLE_AUTO_COMMIT(self, value: bool) -> None:
        self.enable_auto_commit = value

    @property
    def SESSION_TIMEOUT_MS(self) -> int:
        return self.session_timeout_ms

    @SESSION_TIMEOUT_MS.setter
    def SESSION_TIMEOUT_MS(self, value: int) -> None:
        self.session_timeout_ms = value
