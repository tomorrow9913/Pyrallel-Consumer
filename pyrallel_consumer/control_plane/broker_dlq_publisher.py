"""DLQ publication helper for BrokerPoller."""

import asyncio
import random
from typing import Any, List, Tuple, Union, cast

from confluent_kafka import Producer

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import DLQPayloadMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.utils.validation import validate_topic_name


async def publish_to_dlq(
    *,
    producer: Producer,
    consume_topic: str,
    kafka_config: KafkaConfig,
    tp: DtoTopicPartition,
    offset: int,
    epoch: int,
    key: Any,
    value: Any,
    error: str,
    attempt: int,
    logger: Any,
) -> bool:
    source_topic = validate_topic_name(consume_topic)
    suffix = validate_topic_name(kafka_config.DLQ_TOPIC_SUFFIX)
    dlq_topic = source_topic + suffix
    headers_raw = [
        ("x-error-reason", error.encode("utf-8")),
        ("x-retry-attempt", str(attempt).encode("utf-8")),
        ("source-topic", tp.topic.encode("utf-8")),
        ("partition", str(tp.partition).encode("utf-8")),
        ("offset", str(offset).encode("utf-8")),
        ("epoch", str(epoch).encode("utf-8")),
    ]
    headers: List[Tuple[str, Union[str, bytes, None]]] = cast(
        List[Tuple[str, Union[str, bytes, None]]], headers_raw
    )

    exec_config = kafka_config.parallel_consumer.execution
    max_retries = exec_config.max_retries
    base_backoff_ms = exec_config.retry_backoff_ms
    max_backoff_ms = exec_config.max_retry_backoff_ms
    jitter_ms = exec_config.retry_jitter_ms
    use_exponential = exec_config.exponential_backoff

    payload_mode = getattr(kafka_config, "dlq_payload_mode", DLQPayloadMode.FULL)

    for retry_attempt in range(max_retries):
        try:
            send_key = None
            send_value = None
            if payload_mode == DLQPayloadMode.FULL:
                send_key = key
                send_value = value
            await asyncio.to_thread(
                producer.produce,
                topic=dlq_topic,
                key=send_key,
                value=send_value,
                headers=headers,  # type: ignore[arg-type]
            )
            await asyncio.to_thread(
                producer.flush,
                timeout=kafka_config.DLQ_FLUSH_TIMEOUT_MS / 1000.0,
            )
            logger.debug("Published to DLQ: %s@%d -> %s", tp, offset, dlq_topic)
            return True
        except Exception as exc:
            if retry_attempt < max_retries - 1:
                if use_exponential:
                    backoff = min(base_backoff_ms * (2**retry_attempt), max_backoff_ms)
                else:
                    backoff = base_backoff_ms

                jitter = random.uniform(0, jitter_ms)
                sleep_time_ms = backoff + jitter
                logger.warning(
                    "DLQ publish failed (attempt %d/%d), retrying in %d ms: %s",
                    retry_attempt + 1,
                    max_retries,
                    int(sleep_time_ms),
                    exc,
                )
                await asyncio.sleep(sleep_time_ms / 1000.0)
            else:
                logger.error(
                    "DLQ publish failed after %d attempts for %s@%d: %s",
                    max_retries,
                    tp,
                    offset,
                    exc,
                    exc_info=True,
                )
                return False
    return False
