import socket
from typing import ClassVar, Literal, cast

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pyrallel_consumer.dto import DLQPayloadMode, ExecutionMode


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

    process_count: int = 8
    queue_size: int = 2048
    require_picklable_worker: bool = True
    batch_size: int = 64
    batch_bytes: str = "256KB"
    max_batch_wait_ms: int = 5
    worker_join_timeout_ms: int = 30000
    task_timeout_ms: int = 30000
    msgpack_max_bytes: int = 1_000_000
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
    port: int = 9091


class ExecutionConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="EXECUTION_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    mode: ExecutionMode = ExecutionMode.ASYNC
    max_in_flight: int = 1000
    max_revoke_grace_ms: int = 500
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
    queue_max_messages: int = 5000
    diag_log_every: int = 1000
    blocking_warn_seconds: float = 5.0
    max_blocking_duration_ms: int = 0
    blocking_cache_ttl: int = 100
    execution: ExecutionConfig = ExecutionConfig()


class KafkaConfig(BaseSettings):
    model_config: ClassVar[SettingsConfigDict] = SettingsConfigDict(
        env_prefix="KAFKA_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    BOOTSTRAP_SERVERS: str | list[str] = ["localhost:9092"]
    CONSUMER_GROUP: str = "pyrallel-consumer-group"
    DLQ_TOPIC_SUFFIX: str = ".dlq"
    dlq_enabled: bool = True
    dlq_payload_mode: DLQPayloadMode = DLQPayloadMode.FULL
    DLQ_FLUSH_TIMEOUT_MS: int = 5000
    AUTO_OFFSET_RESET: Literal["earliest", "latest", "none"] = "earliest"
    ENABLE_AUTO_COMMIT: bool = False
    SESSION_TIMEOUT_MS: int = 60000
    metrics: MetricsConfig = MetricsConfig()
    parallel_consumer: ParallelConsumerConfig = ParallelConsumerConfig()

    def get_producer_config(self) -> dict[str, str]:
        return {
            "bootstrap.servers": ",".join(self.BOOTSTRAP_SERVERS),
            "client.id": socket.gethostname(),
        }

    def get_consumer_config(self) -> dict[str, str | int | bool]:
        return {
            "bootstrap.servers": ",".join(self.BOOTSTRAP_SERVERS),
            "group.id": self.CONSUMER_GROUP,
            "auto.offset.reset": self.AUTO_OFFSET_RESET,
            "enable.auto.commit": self.ENABLE_AUTO_COMMIT,
            "session.timeout.ms": self.SESSION_TIMEOUT_MS,
        }

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

    @field_validator("BOOTSTRAP_SERVERS", mode="before")
    @classmethod
    def _parse_bootstrap_servers(cls, v: object) -> list[str]:
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            for item in cast(list[object], v):
                if not isinstance(item, str):
                    raise TypeError("BOOTSTRAP_SERVERS list entries must be str")
            string_list = cast(list[str], v)
            cleaned: list[str] = [item.strip() for item in string_list if item.strip()]
            return cleaned
        raise TypeError(
            f"BOOTSTRAP_SERVERS must be str or list[str], got {type(v).__name__}"
        )

    def dump_to_rdkafka(self) -> dict[str, object]:
        data: dict[str, object] = self.model_dump(exclude_none=True)

        _exclude = {
            "BOOTSTRAP_SERVERS",
            "CONSUMER_GROUP",
            "DLQ_TOPIC_SUFFIX",
            "dlq_payload_mode",
            "dlq_enabled",
            "DLQ_FLUSH_TIMEOUT_MS",
            "AUTO_OFFSET_RESET",
            "ENABLE_AUTO_COMMIT",
            "SESSION_TIMEOUT_MS",
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
