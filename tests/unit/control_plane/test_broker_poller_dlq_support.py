# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_dlq_support.py
# Role: Verifies BrokerPoller DLQ support composition boundaries.
# Extend here when DLQ cache or publish responsibilities move out of BrokerPoller.

from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller_dlq import BrokerDlqSupport
from pyrallel_consumer.dto import DLQPayloadMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def _make_support(*, config: KafkaConfig, producer=None, max_bytes: int = 64):
    """Create BrokerDlqSupport bound to shared mutable test state."""
    message_cache: OrderedDict[tuple[DtoTopicPartition, int], tuple[object, object]] = (
        OrderedDict()
    )
    size_holder = {"size": 0}
    support = BrokerDlqSupport(
        consume_topic="test-topic",
        get_kafka_config=lambda: config,
        get_producer=lambda: producer,
        get_message_cache=lambda: message_cache,
        get_message_cache_max_bytes=lambda: max_bytes,
        get_message_cache_size_bytes=lambda: size_holder["size"],
        set_message_cache_size_bytes=lambda value: size_holder.update(size=value),
        logger=MagicMock(),
    )
    return support, message_cache, size_holder


def test_broker_dlq_support_cache_policy_reads_live_config():
    # Given: DLQ payload caching starts enabled with FULL payload mode.
    config = KafkaConfig(_env_file=None)
    config.dlq_enabled = True
    config.dlq_payload_mode = DLQPayloadMode.FULL
    support, _, _ = _make_support(config=config, max_bytes=64)

    # When: BrokerDlqSupport evaluates the live cache policy.
    should_cache_full_payload = support.should_cache_message_payloads()

    # Then: FULL payload mode allows raw payload caching.
    assert should_cache_full_payload is True

    # Given: the same live config is switched to metadata-only DLQ payloads.
    config.dlq_payload_mode = DLQPayloadMode.METADATA_ONLY

    # When: BrokerDlqSupport re-evaluates the live cache policy.
    should_cache_metadata_only = support.should_cache_message_payloads()

    # Then: metadata-only mode disables raw payload caching.
    assert should_cache_metadata_only is False


def test_broker_dlq_support_cache_operations_update_shared_size():
    # Given: DLQ payload caching has a byte budget that only fits one payload.
    config = KafkaConfig(_env_file=None)
    config.dlq_enabled = True
    config.dlq_payload_mode = DLQPayloadMode.FULL
    support, message_cache, size_holder = _make_support(config=config, max_bytes=10)
    tp = DtoTopicPartition("test-topic", 0)

    # When: two cache entries are written and the second exceeds the budget.
    support.cache_message_for_dlq(tp, 1, b"k1", b"1234")
    support.cache_message_for_dlq(tp, 2, b"k2", b"5678")

    # Then: the oldest entry is evicted and shared size accounting is updated.
    assert list(message_cache) == [(tp, 2)]
    assert size_holder["size"] == 6

    # When: the remaining cached entry is popped.
    cached = support.pop_cached_message((tp, 2))

    # Then: the payload is returned and shared cache accounting is cleared.
    assert cached == (b"k2", b"5678")
    assert message_cache == OrderedDict()
    assert size_holder["size"] == 0


@pytest.mark.asyncio
async def test_broker_dlq_support_publish_uses_live_producer_and_config():
    # Given: BrokerDlqSupport is bound to a live producer and Kafka config.
    config = KafkaConfig(_env_file=None)
    config.dlq_enabled = True
    producer = MagicMock()
    support, _, _ = _make_support(config=config, producer=producer)
    tp = DtoTopicPartition("test-topic", 0)

    with patch(
        "pyrallel_consumer.control_plane.broker_poller_dlq.publish_to_dlq",
        new=AsyncMock(return_value=True),
    ) as publish:
        # When: a terminal failure is published through the support object.
        result = await support.publish_to_dlq(
            tp=tp,
            offset=9,
            epoch=2,
            key=b"k",
            value=b"v",
            error="boom",
            attempt=3,
        )

    # Then: the lower-level publisher receives the current producer/config pair.
    assert result is True
    publish.assert_awaited_once()
    assert publish.await_args.kwargs["producer"] is producer
    assert publish.await_args.kwargs["kafka_config"] is config
    assert publish.await_args.kwargs["consume_topic"] == "test-topic"


@pytest.mark.asyncio
async def test_broker_dlq_support_publish_requires_producer():
    # Given: BrokerDlqSupport is created before a Kafka producer exists.
    config = KafkaConfig(_env_file=None)
    support, _, _ = _make_support(config=config, producer=None)

    # When/Then: publishing fails with the same compatibility error as BrokerPoller.
    with pytest.raises(RuntimeError, match="Producer must be initialized"):
        await support.publish_to_dlq(
            tp=DtoTopicPartition("test-topic", 0),
            offset=1,
            epoch=0,
            key=b"k",
            value=b"v",
            error="boom",
            attempt=1,
        )
