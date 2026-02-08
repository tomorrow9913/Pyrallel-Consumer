import socket
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import List, Literal

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
    # Additional consumer/producer specific configs can be added here
    # For consumer
    AUTO_OFFSET_RESET: Literal["earliest", "latest", "none"] = "earliest"
    ENABLE_AUTO_COMMIT: bool = False
    SESSION_TIMEOUT_MS: int = 60000
    REBALANCE_PROTOCOL: str = "consumer" # "consumer" or "roundrobin"

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
            "go.application.rebalance.enable": True, # Always enable for graceful shutdown
            "session.timeout.ms": self.SESSION_TIMEOUT_MS,
            "partition.assignment.strategy": self.REBALANCE_PROTOCOL,
        }
