import socket
from typing import Any, List, Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

from pyrallel_consumer.dto import ExecutionMode


class AsyncConfig(BaseSettings):
    task_timeout_ms: int = 30000


class ProcessConfig(BaseSettings):
    process_count: int = 8
    queue_size: int = 2048
    require_picklable_worker: bool = True
    batch_size: int = 64
    batch_bytes: str = "256KB"
    max_batch_wait_ms: int = 5
    worker_join_timeout_ms: int = 30000
    task_timeout_ms: int = 30000


class MetricsConfig(BaseSettings):
    enabled: bool = True
    port: int = 9091


class ExecutionConfig(BaseSettings):
    model_config = SettingsConfigDict(
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

    @property
    def max_in_flight_messages(self) -> int:
        return self.max_in_flight


class ParallelConsumerConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PARALLEL_CONSUMER_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    poll_batch_size: int = 1000
    worker_pool_size: int = 8
    diag_log_every: int = 1000
    blocking_warn_seconds: float = 5.0
    blocking_cache_ttl: int = 100
    execution: ExecutionConfig = ExecutionConfig()


class KafkaConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    BOOTSTRAP_SERVERS: List[str] = ["localhost:9092"]
    CONSUMER_GROUP: str = "pyrallel-consumer-group"
    DLQ_TOPIC_SUFFIX: str = ".dlq"
    dlq_enabled: bool = True
    DLQ_FLUSH_TIMEOUT_MS: int = 5000
    AUTO_OFFSET_RESET: Literal["earliest", "latest", "none"] = "earliest"
    ENABLE_AUTO_COMMIT: bool = False
    SESSION_TIMEOUT_MS: int = 60000
    metrics: MetricsConfig = MetricsConfig()
    parallel_consumer: ParallelConsumerConfig = ParallelConsumerConfig()

    def get_producer_config(self) -> dict:
        return {
            "bootstrap.servers": ",".join(self.BOOTSTRAP_SERVERS),
            "client.id": socket.gethostname(),
        }

    def get_consumer_config(self) -> dict:
        return {
            "bootstrap.servers": ",".join(self.BOOTSTRAP_SERVERS),
            "group.id": self.CONSUMER_GROUP,
            "auto.offset.reset": self.AUTO_OFFSET_RESET,
            "enable.auto.commit": self.ENABLE_AUTO_COMMIT,
            "session.timeout.ms": self.SESSION_TIMEOUT_MS,
        }

    def dump_to_rdkafka(self) -> dict[str, Any]:
        data = self.model_dump(exclude_none=True)

        conf: dict[str, Any] = {}
        for k, v in data.items():
            if k in [
                "bootstrap_servers",
                "consumer_group",
                "dlq_topic_suffix",
                "dlq_enabled",
                "dlq_flush_timeout_ms",
                "auto_offset_reset",
                "enable_auto_commit",
                "session_timeout_ms",
                "rebalance_protocol",
            ]:
                continue
            conf[k.replace("_", ".")] = v

        if self.model_extra:
            for k, v in self.model_extra.items():
                conf[k.replace("_", ".").lower()] = v
        return conf
