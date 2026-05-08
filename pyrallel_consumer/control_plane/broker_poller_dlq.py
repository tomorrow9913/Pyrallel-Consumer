# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_poller_dlq.py
# Role: Coordinates BrokerPoller raw-message DLQ cache operations and DLQ publishing.
# Extend here for BrokerPoller DLQ cache/publish behavior; keep completion decisions in broker_completion_support.py.
from __future__ import annotations

from collections import OrderedDict
from typing import Any, Callable, Optional, Tuple

from confluent_kafka import Producer

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import DLQPayloadMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition

from .broker_dlq_publisher import publish_to_dlq
from .broker_support import DlqCacheSupport


class BrokerDlqSupport:
    """Group BrokerPoller DLQ cache and publish operations."""

    def __init__(
        self,
        *,
        consume_topic: str,
        get_kafka_config: Callable[[], KafkaConfig],
        get_producer: Callable[[], Optional[Producer]],
        get_message_cache: Callable[
            [], OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]]
        ],
        get_message_cache_max_bytes: Callable[[], int],
        get_message_cache_size_bytes: Callable[[], int],
        set_message_cache_size_bytes: Callable[[int], None],
        logger: Any,
        cache_support: DlqCacheSupport | None = None,
    ) -> None:
        """Initialize this component."""
        self._consume_topic = consume_topic
        self._get_kafka_config = get_kafka_config
        self._get_producer = get_producer
        self._get_message_cache = get_message_cache
        self._get_message_cache_max_bytes = get_message_cache_max_bytes
        self._get_message_cache_size_bytes = get_message_cache_size_bytes
        self._set_message_cache_size_bytes = set_message_cache_size_bytes
        self._logger = logger
        self._cache_support = cache_support or DlqCacheSupport()

    def should_cache_message_payloads(self) -> bool:
        """Return whether raw payloads should be cached for DLQ publication."""
        kafka_config = self._get_kafka_config()
        dlq_enabled = bool(getattr(kafka_config, "dlq_enabled", False))
        payload_mode = getattr(kafka_config, "dlq_payload_mode", DLQPayloadMode.FULL)
        return bool(
            dlq_enabled
            and payload_mode == DLQPayloadMode.FULL
            and self._get_message_cache_max_bytes() != 0
        )

    @staticmethod
    def estimate_cached_payload_bytes(payload: Any) -> int:
        """Estimate the byte footprint of a cached DLQ key or value."""
        return DlqCacheSupport.estimate_cached_payload_bytes(payload)

    def get_cached_message_size(self, key: Any, value: Any) -> int:
        """Return the byte footprint for a raw DLQ cache entry."""
        return self._cache_support.get_cached_message_size(key, value)

    def pop_cached_message(
        self, cache_key: Tuple[DtoTopicPartition, int]
    ) -> Optional[Tuple[Any, Any]]:
        """Remove and return a cached raw DLQ message."""
        cached_message, size_bytes = self._cache_support.pop_cached_message(
            self._get_message_cache(),
            self._get_message_cache_size_bytes(),
            cache_key,
        )
        self._set_message_cache_size_bytes(size_bytes)
        return cached_message

    def cache_message_for_dlq(
        self, tp: DtoTopicPartition, offset: int, key: Any, value: Any
    ) -> None:
        """Cache a raw message payload for a later terminal DLQ failure."""
        size_bytes = self._cache_support.cache_message_for_dlq(
            message_cache=self._get_message_cache(),
            size_bytes=self._get_message_cache_size_bytes(),
            should_cache=self.should_cache_message_payloads(),
            max_bytes=self._get_message_cache_max_bytes(),
            tp=tp,
            offset=offset,
            key=key,
            value=value,
            logger=self._logger,
        )
        self._set_message_cache_size_bytes(size_bytes)

    def drop_cached_partition_messages(self, tp: DtoTopicPartition) -> None:
        """Drop all raw DLQ cache entries for a revoked partition."""
        size_bytes = self._cache_support.drop_partition_messages(
            message_cache=self._get_message_cache(),
            size_bytes=self._get_message_cache_size_bytes(),
            tp=tp,
        )
        self._set_message_cache_size_bytes(size_bytes)

    async def publish_to_dlq(
        self,
        *,
        tp: DtoTopicPartition,
        offset: int,
        epoch: int,
        key: Any,
        value: Any,
        error: str,
        attempt: int,
    ) -> bool:
        """Publish a terminal failure to the configured DLQ topic."""
        producer = self._get_producer()
        if producer is None:
            raise RuntimeError("Producer must be initialized for DLQ publishing")

        return await publish_to_dlq(
            producer=producer,
            consume_topic=self._consume_topic,
            kafka_config=self._get_kafka_config(),
            tp=tp,
            offset=offset,
            epoch=epoch,
            key=key,
            value=value,
            error=error,
            attempt=attempt,
            logger=self._logger,
        )
