import socket
from typing import Any, List, Literal  # Import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


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
    REBALANCE_PROTOCOL: str = "consumer"  # "consumer" or "roundrobin"

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
            "go.application.rebalance.enable": True,  # Always enable for graceful shutdown
            "session.timeout.ms": self.SESSION_TIMEOUT_MS,
            "partition.assignment.strategy": self.REBALANCE_PROTOCOL,
        }

    def dump_to_rdkafka(self) -> dict[str, Any]:
        """객체의 필드와 extra 데이터를 librdkafka 스타일 dict로 변환"""
        # 1. 명시적으로 정의된 필드들 가져오기 (None 제외)
        data = self.model_dump(exclude_none=True)

        # 2. 필드명 변환 (언더바 -> 점)
        conf = {}
        for k, v in data.items():
            # BOOTSTRAP_SERVERS, CONSUMER_GROUP 등은 get_producer_config/get_consumer_config에서 처리하므로 제외
            if k in [
                "bootstrap_servers",
                "consumer_group",
                "dlq_topic_suffix",
                "auto_offset_reset",
                "enable_auto_commit",
                "session_timeout_ms",
                "rebalance_protocol",
            ]:
                continue
            conf[k.replace("_", ".")] = v

        # 3. extra 데이터 추가 (이미 dict 형태이므로 변환 후 병합)
        if self.model_extra:
            for k, v in self.model_extra.items():
                conf[k.replace("_", ".").lower()] = v
        return conf
