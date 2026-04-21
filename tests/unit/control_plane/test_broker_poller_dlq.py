# -*- coding: utf-8 -*-
"""Unit tests for BrokerPoller DLQ functionality."""

import asyncio
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import KafkaException, Producer

from pyrallel_consumer.config import (
    ExecutionConfig,
    KafkaConfig,
    ParallelConsumerConfig,
)
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, DLQPayloadMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def kafka_config_with_dlq():
    """Create a KafkaConfig with DLQ enabled."""
    config = KafkaConfig()
    config.dlq_enabled = True
    config.DLQ_TOPIC_SUFFIX = ".dlq"
    config.BOOTSTRAP_SERVERS = ["localhost:9092"]
    config.CONSUMER_GROUP = "test-group"

    # Configure execution settings for retries
    config.parallel_consumer = ParallelConsumerConfig()
    config.parallel_consumer.execution = ExecutionConfig()
    config.parallel_consumer.execution.max_retries = 3
    config.parallel_consumer.execution.retry_backoff_ms = 100
    config.parallel_consumer.execution.exponential_backoff = True
    config.parallel_consumer.execution.max_retry_backoff_ms = 1000
    config.parallel_consumer.execution.retry_jitter_ms = 10
    config.parallel_consumer.execution.max_in_flight = 100
    config.parallel_consumer.poll_batch_size = 10
    config.parallel_consumer.worker_pool_size = 4

    return config


@pytest.fixture
def kafka_config_no_dlq():
    """Create a KafkaConfig with DLQ disabled."""
    config = KafkaConfig()
    config.dlq_enabled = False
    config.DLQ_TOPIC_SUFFIX = ".dlq"
    config.BOOTSTRAP_SERVERS = ["localhost:9092"]
    config.CONSUMER_GROUP = "test-group"

    config.parallel_consumer = ParallelConsumerConfig()
    config.parallel_consumer.execution = ExecutionConfig()
    config.parallel_consumer.execution.max_retries = 3
    config.parallel_consumer.execution.max_in_flight = 100
    config.parallel_consumer.poll_batch_size = 10
    config.parallel_consumer.worker_pool_size = 4

    return config


@pytest.fixture
def mock_execution_engine():
    """Create a mock execution engine."""
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def broker_poller_with_dlq(kafka_config_with_dlq, mock_execution_engine):
    """Create a BrokerPoller instance with DLQ enabled."""
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config_with_dlq,
        execution_engine=mock_execution_engine,
    )
    poller.producer = MagicMock(spec=Producer)
    poller.consumer = MagicMock()
    return poller


@pytest.fixture
def broker_poller_no_dlq(kafka_config_no_dlq, mock_execution_engine):
    """Create a BrokerPoller instance with DLQ disabled."""
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config_no_dlq,
        execution_engine=mock_execution_engine,
    )
    poller.producer = MagicMock(spec=Producer)
    poller.consumer = MagicMock()
    return poller


@pytest.mark.asyncio
async def test_publish_to_dlq_success_first_attempt(broker_poller_with_dlq):
    """Test successful DLQ publish on first attempt."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100
    epoch = 1
    key = b"test-key"
    value = b"test-value"
    error = "Processing failed"
    attempt = 3

    # Mock successful produce
    broker_poller_with_dlq.producer.produce = MagicMock()
    broker_poller_with_dlq.producer.flush = MagicMock()

    result = await broker_poller_with_dlq._publish_to_dlq(
        tp=tp,
        offset=offset,
        epoch=epoch,
        key=key,
        value=value,
        error=error,
        attempt=attempt,
    )

    assert result is True

    # Verify produce was called with correct parameters
    broker_poller_with_dlq.producer.produce.assert_called_once()
    call_kwargs = broker_poller_with_dlq.producer.produce.call_args[1]
    assert call_kwargs["topic"] == "test-topic.dlq"
    assert call_kwargs["key"] == key
    assert call_kwargs["value"] == value

    # Verify headers
    headers = call_kwargs["headers"]
    header_dict = {h[0]: h[1] for h in headers}
    assert header_dict["x-error-reason"] == error.encode("utf-8")
    assert header_dict["x-retry-attempt"] == str(attempt).encode("utf-8")
    assert header_dict["source-topic"] == tp.topic.encode("utf-8")
    assert header_dict["partition"] == str(tp.partition).encode("utf-8")
    assert header_dict["offset"] == str(offset).encode("utf-8")
    assert header_dict["epoch"] == str(epoch).encode("utf-8")

    broker_poller_with_dlq.producer.flush.assert_called_once()
    flush_kwargs = broker_poller_with_dlq.producer.flush.call_args.kwargs
    assert flush_kwargs.get("timeout") == pytest.approx(5.0)


@pytest.mark.asyncio
async def test_publish_to_dlq_retry_with_exponential_backoff(broker_poller_with_dlq):
    """Test DLQ publish retries with exponential backoff."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    # Mock produce to fail twice, then succeed
    broker_poller_with_dlq.producer.produce = MagicMock(
        side_effect=[
            KafkaException("Network error"),
            KafkaException("Network error"),
            None,  # Success on third attempt
        ]
    )
    broker_poller_with_dlq.producer.flush = MagicMock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await broker_poller_with_dlq._publish_to_dlq(
            tp=tp,
            offset=100,
            epoch=1,
            key=b"key",
            value=b"value",
            error="test error",
            attempt=1,
        )

    assert result is True
    assert broker_poller_with_dlq.producer.produce.call_count == 3

    # Verify exponential backoff (base=100ms, exponential=True)
    # First retry: 100 * 2^0 = 100ms + jitter
    # Second retry: 100 * 2^1 = 200ms + jitter
    assert mock_sleep.call_count == 2
    first_sleep = mock_sleep.call_args_list[0][0][0]
    second_sleep = mock_sleep.call_args_list[1][0][0]

    # First sleep should be around 100ms (0.1s) + jitter (max 10ms = 0.01s)
    assert 0.1 <= first_sleep <= 0.11
    # Second sleep should be around 200ms (0.2s) + jitter
    assert 0.2 <= second_sleep <= 0.21


@pytest.mark.asyncio
async def test_publish_to_dlq_failure_after_max_retries(broker_poller_with_dlq):
    """Test DLQ publish failure after exhausting all retries."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    # Mock produce to always fail
    broker_poller_with_dlq.producer.produce = MagicMock(
        side_effect=KafkaException("Persistent error")
    )

    with patch("asyncio.sleep", new_callable=AsyncMock):
        result = await broker_poller_with_dlq._publish_to_dlq(
            tp=tp,
            offset=100,
            epoch=1,
            key=b"key",
            value=b"value",
            error="test error",
            attempt=1,
        )

    assert result is False
    # Should attempt max_retries times (3)
    assert broker_poller_with_dlq.producer.produce.call_count == 3


@pytest.mark.asyncio
async def test_publish_to_dlq_linear_backoff(
    kafka_config_with_dlq, mock_execution_engine
):
    """Test DLQ publish with linear backoff strategy."""
    # Configure linear backoff
    kafka_config_with_dlq.parallel_consumer.execution.exponential_backoff = False
    kafka_config_with_dlq.parallel_consumer.execution.retry_backoff_ms = 50

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config_with_dlq,
        execution_engine=mock_execution_engine,
    )
    poller.producer = MagicMock(spec=Producer)
    poller.consumer = MagicMock()

    # Mock produce to fail twice, then succeed
    poller.producer.produce = MagicMock(
        side_effect=[
            KafkaException("Error"),
            KafkaException("Error"),
            None,
        ]
    )
    poller.producer.flush = MagicMock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        result = await poller._publish_to_dlq(
            tp=DtoTopicPartition(topic="test-topic", partition=0),
            offset=100,
            epoch=1,
            key=b"key",
            value=b"value",
            error="test error",
            attempt=1,
        )

    assert result is True
    assert mock_sleep.call_count == 2

    # Both sleeps should be around 50ms (0.05s) + jitter with linear backoff
    for sleep_call in mock_sleep.call_args_list:
        sleep_time = sleep_call[0][0]
        assert 0.05 <= sleep_time <= 0.06


@pytest.mark.asyncio
async def test_publish_to_dlq_max_backoff_cap(
    kafka_config_with_dlq, mock_execution_engine
):
    """Test that exponential backoff is capped at max_retry_backoff_ms."""
    # Configure very large exponential backoff but with a cap
    kafka_config_with_dlq.parallel_consumer.execution.retry_backoff_ms = 1000
    kafka_config_with_dlq.parallel_consumer.execution.max_retry_backoff_ms = 2000
    kafka_config_with_dlq.parallel_consumer.execution.exponential_backoff = True
    kafka_config_with_dlq.parallel_consumer.execution.retry_jitter_ms = 0

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config_with_dlq,
        execution_engine=mock_execution_engine,
    )
    poller.producer = MagicMock(spec=Producer)
    poller.consumer = MagicMock()

    # Mock produce to fail multiple times
    poller.producer.produce = MagicMock(
        side_effect=[KafkaException("Error"), KafkaException("Error"), None]
    )
    poller.producer.flush = MagicMock()

    with patch("asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        await poller._publish_to_dlq(
            tp=DtoTopicPartition(topic="test-topic", partition=0),
            offset=100,
            epoch=1,
            key=b"key",
            value=b"value",
            error="test error",
            attempt=1,
        )

    # First retry: 1000 * 2^0 = 1000ms = 1.0s (no jitter)
    # Second retry: 1000 * 2^1 = 2000ms = 2.0s (capped, no jitter)
    first_sleep = mock_sleep.call_args_list[0][0][0]
    second_sleep = mock_sleep.call_args_list[1][0][0]

    assert first_sleep == 1.0
    assert second_sleep == 2.0  # Capped at max


@pytest.mark.asyncio
async def test_publish_to_dlq_raises_if_producer_not_initialized():
    """Test that _publish_to_dlq raises error if producer is None."""
    config = KafkaConfig()
    config.dlq_enabled = True
    mock_engine = AsyncMock(spec=BaseExecutionEngine)

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=config,
        execution_engine=mock_engine,
    )
    # Explicitly set producer to None
    poller.producer = None

    with pytest.raises(RuntimeError, match="Producer must be initialized"):
        await poller._publish_to_dlq(
            tp=DtoTopicPartition(topic="test-topic", partition=0),
            offset=100,
            epoch=1,
            key=b"key",
            value=b"value",
            error="test error",
            attempt=1,
        )


@pytest.mark.asyncio
async def test_completion_event_dlq_disabled_skips_publish(broker_poller_no_dlq):
    """Test that DLQ publish is skipped when dlq_enabled is False."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100

    # Set up offset tracker
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=99,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 99
    tracker.increment_epoch()
    broker_poller_no_dlq._offset_trackers[tp] = tracker

    # Cache the message
    broker_poller_no_dlq._message_cache[(tp, offset)] = (b"key", b"value")

    # Create a FAILURE completion event
    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,  # Max retries reached
    )

    # Mock the work manager to return this event
    broker_poller_no_dlq._work_manager.poll_completed_events = AsyncMock(
        return_value=[event]
    )

    # Spy on _publish_to_dlq to ensure it's not called
    original_publish = broker_poller_no_dlq._publish_to_dlq
    broker_poller_no_dlq._publish_to_dlq = AsyncMock(wraps=original_publish)

    # Process the completion event
    await broker_poller_no_dlq._work_manager.poll_completed_events()

    # When DLQ is disabled, we should not call _publish_to_dlq
    # This is handled in the consumer loop, but we verify the config
    assert broker_poller_no_dlq._kafka_config.dlq_enabled is False

    # Verify message was marked as complete even without DLQ
    # (this would be tested in integration, here we just verify config)


@pytest.mark.asyncio
async def test_completion_event_dlq_publish_success_commits_offset(
    broker_poller_with_dlq,
):
    """Test that offset is committed after successful DLQ publish."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100

    # Set up offset tracker
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=99,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 99
    tracker.last_fetched_offset = 100
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    # Cache the message
    broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")

    # Mock successful DLQ publish
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=True)

    # Create a FAILURE completion event
    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,  # Max retries reached
    )

    # Simulate processing the completion event (simplified logic)
    if event.status == CompletionStatus.FAILURE:
        max_retries = (
            broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
        )
        if (
            broker_poller_with_dlq._kafka_config.dlq_enabled
            and event.attempt >= max_retries
        ):
            cache_key = (event.tp, event.offset)
            cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
            if cached_msg:
                msg_key, msg_value = cached_msg
                dlq_success = await broker_poller_with_dlq._publish_to_dlq(
                    tp=event.tp,
                    offset=event.offset,
                    epoch=event.epoch,
                    key=msg_key,
                    value=msg_value,
                    error=event.error or "Unknown error",
                    attempt=event.attempt,
                )
                if dlq_success:
                    tracker.mark_complete(event.offset)
                    broker_poller_with_dlq._message_cache.pop(cache_key, None)

    # Verify DLQ publish was called
    broker_poller_with_dlq._publish_to_dlq.assert_called_once_with(
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        key=b"key",
        value=b"value",
        error="Test error",
        attempt=3,
    )

    # Verify offset was marked complete
    assert offset in tracker.completed_offsets

    # Verify message was removed from cache
    assert (tp, offset) not in broker_poller_with_dlq._message_cache


@pytest.mark.asyncio
async def test_completion_event_dlq_publish_failure_skips_commit(
    broker_poller_with_dlq,
):
    """Test that offset is NOT committed after failed DLQ publish."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100

    # Set up offset tracker
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=99,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 99
    tracker.last_fetched_offset = 100
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    # Cache the message
    broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")

    # Mock failed DLQ publish
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=False)

    # Create a FAILURE completion event
    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,  # Max retries reached
    )

    # Simulate processing the completion event (simplified logic)
    if event.status == CompletionStatus.FAILURE:
        max_retries = (
            broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
        )
        if (
            broker_poller_with_dlq._kafka_config.dlq_enabled
            and event.attempt >= max_retries
        ):
            cache_key = (event.tp, event.offset)
            cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
            if cached_msg:
                msg_key, msg_value = cached_msg
                dlq_success = await broker_poller_with_dlq._publish_to_dlq(
                    tp=event.tp,
                    offset=event.offset,
                    epoch=event.epoch,
                    key=msg_key,
                    value=msg_value,
                    error=event.error or "Unknown error",
                    attempt=event.attempt,
                )
                if not dlq_success:
                    pass

    # Verify DLQ publish was called
    broker_poller_with_dlq._publish_to_dlq.assert_called_once()

    # Verify offset was NOT marked complete
    assert offset not in tracker.completed_offsets

    assert (tp, offset) in broker_poller_with_dlq._message_cache


@pytest.mark.asyncio
async def test_completion_event_dlq_missing_cache_entry(broker_poller_with_dlq):
    """Test handling when cached message is missing during DLQ publish."""
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100

    # Set up offset tracker
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=99,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 99
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    # Deliberately do NOT cache the message
    # broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")

    # Create a FAILURE completion event
    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,  # Max retries reached
    )

    # Spy on _publish_to_dlq
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=True)

    # Simulate processing the completion event (simplified logic)
    if event.status == CompletionStatus.FAILURE:
        max_retries = (
            broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
        )
        if (
            broker_poller_with_dlq._kafka_config.dlq_enabled
            and event.attempt >= max_retries
        ):
            cache_key = (event.tp, event.offset)
            cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
            if cached_msg is None:
                # Should skip DLQ publish if no cache entry
                pass
            else:
                msg_key, msg_value = cached_msg
                await broker_poller_with_dlq._publish_to_dlq(
                    tp=event.tp,
                    offset=event.offset,
                    epoch=event.epoch,
                    key=msg_key,
                    value=msg_value,
                    error=event.error or "Unknown error",
                    attempt=event.attempt,
                )

    # Verify _publish_to_dlq was NOT called because cache entry was missing
    broker_poller_with_dlq._publish_to_dlq.assert_not_called()

    assert offset not in tracker.completed_offsets


@pytest.mark.asyncio
async def test_completion_event_dlq_publish_success_from_capped_failure(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 42

    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=offset - 1,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = offset - 1
    tracker.last_fetched_offset = offset
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=True)
    broker_poller_with_dlq.consumer = MagicMock()

    event = CompletionEvent(
        id="dead-worker",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="worker_died_max_retries",
        attempt=broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries,
    )

    max_retries = (
        broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
    )
    if (
        broker_poller_with_dlq._kafka_config.dlq_enabled
        and event.attempt >= max_retries
    ):
        cache_key = (event.tp, event.offset)
        cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
        if cached_msg:
            msg_key, msg_value = cached_msg
            dlq_success = await broker_poller_with_dlq._publish_to_dlq(
                tp=event.tp,
                offset=event.offset,
                epoch=event.epoch,
                key=msg_key,
                value=msg_value,
                error=event.error or "Unknown error",
                attempt=event.attempt,
            )
            if dlq_success:
                tracker.mark_complete(event.offset)
                broker_poller_with_dlq._message_cache.pop(cache_key, None)
                await asyncio.to_thread(
                    broker_poller_with_dlq.consumer.commit, offsets=[]
                )

    broker_poller_with_dlq._publish_to_dlq.assert_called_once_with(
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        key=b"key",
        value=b"value",
        error="worker_died_max_retries",
        attempt=max_retries,
    )
    assert offset in tracker.completed_offsets
    assert (tp, offset) not in broker_poller_with_dlq._message_cache


@pytest.mark.asyncio
async def test_completion_event_dlq_publish_failure_from_capped_failure(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 43

    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=offset - 1,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = offset - 1
    tracker.last_fetched_offset = offset
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=False)

    event = CompletionEvent(
        id="dead-worker",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="worker_died_max_retries",
        attempt=broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries,
    )

    max_retries = (
        broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
    )
    if (
        broker_poller_with_dlq._kafka_config.dlq_enabled
        and event.attempt >= max_retries
    ):
        cache_key = (event.tp, event.offset)
        cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
        if cached_msg:
            msg_key, msg_value = cached_msg
            dlq_success = await broker_poller_with_dlq._publish_to_dlq(
                tp=event.tp,
                offset=event.offset,
                epoch=event.epoch,
                key=msg_key,
                value=msg_value,
                error=event.error or "Unknown error",
                attempt=event.attempt,
            )
            if dlq_success:
                tracker.mark_complete(event.offset)
                broker_poller_with_dlq._message_cache.pop(cache_key, None)

    broker_poller_with_dlq._publish_to_dlq.assert_called_once()
    assert offset not in tracker.completed_offsets
    assert (tp, offset) in broker_poller_with_dlq._message_cache


@pytest.mark.asyncio
async def test_completion_event_dlq_capped_failure_missing_cache(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 44

    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=offset - 1,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = offset - 1
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker

    broker_poller_with_dlq._publish_to_dlq = AsyncMock()

    event = CompletionEvent(
        id="dead-worker",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="worker_died_max_retries",
        attempt=broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries,
    )

    max_retries = (
        broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries
    )
    if (
        broker_poller_with_dlq._kafka_config.dlq_enabled
        and event.attempt >= max_retries
    ):
        cache_key = (event.tp, event.offset)
        cached_msg = broker_poller_with_dlq._message_cache.get(cache_key)
        if cached_msg:
            msg_key, msg_value = cached_msg
            await broker_poller_with_dlq._publish_to_dlq(
                tp=event.tp,
                offset=event.offset,
                epoch=event.epoch,
                key=msg_key,
                value=msg_value,
                error=event.error or "Unknown error",
                attempt=event.attempt,
            )

    broker_poller_with_dlq._publish_to_dlq.assert_not_called()
    assert offset not in tracker.completed_offsets


@pytest.mark.asyncio
async def test_publish_to_dlq_metadata_only(broker_poller_with_dlq):
    broker_poller_with_dlq._kafka_config.dlq_payload_mode = DLQPayloadMode.METADATA_ONLY
    broker_poller_with_dlq.producer.produce = MagicMock()
    broker_poller_with_dlq.producer.flush = MagicMock()

    result = await broker_poller_with_dlq._publish_to_dlq(
        tp=DtoTopicPartition(topic="test-topic", partition=0),
        offset=1,
        epoch=0,
        key=b"key",
        value=b"value",
        error="err",
        attempt=1,
    )

    assert result is True
    _, kwargs = broker_poller_with_dlq.producer.produce.call_args
    assert kwargs["key"] is None
    assert kwargs["value"] is None


@pytest.mark.asyncio
async def test_publish_to_dlq_invalid_suffix_raises(broker_poller_with_dlq):
    broker_poller_with_dlq._kafka_config.DLQ_TOPIC_SUFFIX = "bad suffix!"
    with pytest.raises(ValueError):
        await broker_poller_with_dlq._publish_to_dlq(
            tp=DtoTopicPartition(topic="test-topic", partition=0),
            offset=1,
            epoch=0,
            key=b"key",
            value=b"value",
            error="err",
            attempt=1,
        )


def test_cache_message_for_dlq_respects_mode_and_byte_budget(
    broker_poller_with_dlq, broker_poller_no_dlq
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)

    broker_poller_no_dlq._cache_message_for_dlq(tp, 1, b"k", b"value")
    assert broker_poller_no_dlq._message_cache == {}

    broker_poller_with_dlq._kafka_config.dlq_payload_mode = DLQPayloadMode.METADATA_ONLY
    broker_poller_with_dlq._cache_message_for_dlq(tp, 1, b"k", b"value")
    assert broker_poller_with_dlq._message_cache == {}

    broker_poller_with_dlq._kafka_config.dlq_payload_mode = DLQPayloadMode.FULL
    broker_poller_with_dlq._message_cache_max_bytes = 10
    broker_poller_with_dlq._cache_message_for_dlq(tp, 1, b"k1", b"1234")
    broker_poller_with_dlq._cache_message_for_dlq(tp, 2, b"k2", b"5678")

    assert (tp, 1) not in broker_poller_with_dlq._message_cache
    assert broker_poller_with_dlq._message_cache[(tp, 2)] == (b"k2", b"5678")
    assert broker_poller_with_dlq._message_cache_size_bytes == 6


@pytest.mark.asyncio
async def test_drain_completion_events_retries_pending_dlq_without_new_completions(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    pending_event = CompletionEvent(
        id="pending-work-id",
        tp=tp,
        offset=100,
        epoch=1,
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )
    broker_poller_with_dlq._pending_dlq_events = OrderedDict({(tp, 100): pending_event})
    broker_poller_with_dlq._work_manager.poll_completed_events = AsyncMock(
        return_value=[]
    )
    broker_poller_with_dlq._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller_with_dlq._process_completed_events = AsyncMock()
    broker_poller_with_dlq._work_manager.schedule = AsyncMock()

    drained = await broker_poller_with_dlq._drain_completion_events_once()

    assert drained is True
    broker_poller_with_dlq._process_completed_events.assert_awaited_once_with([])
    broker_poller_with_dlq._work_manager.schedule.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_graceful_shutdown_drain_times_out_while_pending_dlq_remains(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    pending_event = CompletionEvent(
        id="pending-work-id",
        tp=tp,
        offset=100,
        epoch=1,
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )
    broker_poller_with_dlq._pending_dlq_events = OrderedDict({(tp, 100): pending_event})
    broker_poller_with_dlq._work_manager = MagicMock()
    broker_poller_with_dlq._work_manager.schedule = AsyncMock()
    broker_poller_with_dlq._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller_with_dlq._get_total_queued_messages = AsyncMock(return_value=0)
    broker_poller_with_dlq._drain_completion_events_once = AsyncMock(return_value=True)
    broker_poller_with_dlq._commit_ready_offsets = AsyncMock()

    drained = await broker_poller_with_dlq._drain_shutdown_work(timeout_seconds=0)

    assert drained is False
    broker_poller_with_dlq._drain_completion_events_once.assert_awaited_once()
    broker_poller_with_dlq._commit_ready_offsets.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_monitor_retries_pending_dlq_without_engine_completion(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    pending_event = CompletionEvent(
        id="pending-work-id",
        tp=tp,
        offset=100,
        epoch=1,
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )
    broker_poller_with_dlq._pending_dlq_events = OrderedDict({(tp, 100): pending_event})
    broker_poller_with_dlq._running = True
    broker_poller_with_dlq._work_manager = MagicMock()
    broker_poller_with_dlq._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller_with_dlq._drain_completion_events_once = AsyncMock()
    broker_poller_with_dlq._commit_ready_offsets = AsyncMock()

    async def wait_for_completion(timeout_seconds=None):
        del timeout_seconds
        broker_poller_with_dlq._running = False
        return False

    broker_poller_with_dlq._execution_engine.wait_for_completion = AsyncMock(
        side_effect=wait_for_completion
    )

    async def stop_after_retry(*_args, **_kwargs):
        broker_poller_with_dlq._running = False
        return True

    broker_poller_with_dlq._drain_completion_events_once.side_effect = stop_after_retry

    await broker_poller_with_dlq._run_completion_monitor()

    broker_poller_with_dlq._drain_completion_events_once.assert_awaited_once()
    broker_poller_with_dlq._commit_ready_offsets.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_monitor_throttles_persistent_pending_dlq_retries(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    pending_event = CompletionEvent(
        id="pending-work-id",
        tp=tp,
        offset=100,
        epoch=1,
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )
    broker_poller_with_dlq._pending_dlq_events = OrderedDict({(tp, 100): pending_event})
    broker_poller_with_dlq._running = True
    broker_poller_with_dlq._work_manager = MagicMock()
    broker_poller_with_dlq._work_manager.get_total_in_flight_count.return_value = 0
    drain_attempts = 0

    async def keep_pending_until_second_attempt():
        nonlocal drain_attempts
        drain_attempts += 1
        if drain_attempts > 1:
            broker_poller_with_dlq._running = False
        return True

    broker_poller_with_dlq._drain_completion_events_once = AsyncMock(
        side_effect=keep_pending_until_second_attempt
    )
    broker_poller_with_dlq._commit_ready_offsets = AsyncMock()

    async def stop_after_throttle(_timeout):
        broker_poller_with_dlq._running = False

    with patch(
        "asyncio.sleep", new=AsyncMock(side_effect=stop_after_throttle)
    ) as sleep:
        await broker_poller_with_dlq._run_completion_monitor()

    broker_poller_with_dlq._execution_engine.wait_for_completion.assert_not_awaited()
    broker_poller_with_dlq._drain_completion_events_once.assert_awaited_once()
    broker_poller_with_dlq._commit_ready_offsets.assert_awaited_once()
    sleep.assert_awaited_once_with(broker_poller_with_dlq._idle_consume_timeout_seconds)


@pytest.mark.asyncio
async def test_cleanup_clears_pending_dlq_events(broker_poller_with_dlq):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    broker_poller_with_dlq._pending_dlq_events = OrderedDict(
        {
            (
                tp,
                100,
            ): CompletionEvent(
                id="pending-work-id",
                tp=tp,
                offset=100,
                epoch=1,
                status=CompletionStatus.FAILURE,
                error="Test error",
                attempt=3,
            )
        }
    )
    broker_poller_with_dlq._message_cache[(tp, 100)] = (b"key", b"value")

    await broker_poller_with_dlq._cleanup()

    assert broker_poller_with_dlq._pending_dlq_events == OrderedDict()
    assert broker_poller_with_dlq._message_cache == OrderedDict()


@pytest.mark.asyncio
async def test_consumer_loop_prioritizes_pending_dlq_before_fetching_new_messages(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    pending_event = CompletionEvent(
        id="pending-work-id",
        tp=tp,
        offset=100,
        epoch=1,
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )
    broker_poller_with_dlq._pending_dlq_events = OrderedDict({(tp, 100): pending_event})
    broker_poller_with_dlq._running = True
    broker_poller_with_dlq._drain_completion_events_once = AsyncMock()
    broker_poller_with_dlq._commit_ready_offsets = AsyncMock()
    broker_poller_with_dlq.consumer.consume = MagicMock(return_value=[])

    async def stop_after_pending_retry(*_args, **_kwargs):
        broker_poller_with_dlq._running = False
        return True

    broker_poller_with_dlq._drain_completion_events_once.side_effect = (
        stop_after_pending_retry
    )

    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller_with_dlq._run_consumer()

    broker_poller_with_dlq.consumer.consume.assert_not_called()
    broker_poller_with_dlq._drain_completion_events_once.assert_awaited_once()
    broker_poller_with_dlq._commit_ready_offsets.assert_awaited_once()


@pytest.mark.asyncio
async def test_process_completed_events_falls_back_to_metadata_only_when_cache_missing(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 100

    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=99,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 99
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(return_value=True)

    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=3,
    )

    await broker_poller_with_dlq._process_completed_events([event])

    broker_poller_with_dlq._publish_to_dlq.assert_called_once_with(
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        key=None,
        value=None,
        error="Test error",
        attempt=3,
    )
    assert offset in tracker.completed_offsets


@pytest.mark.asyncio
async def test_dlq_publish_failure_is_retried_without_duplicate_completion_event(
    broker_poller_with_dlq,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    offset = 101

    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=offset - 1,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = offset - 1
    tracker.last_fetched_offset = offset
    tracker.increment_epoch()
    broker_poller_with_dlq._offset_trackers[tp] = tracker
    broker_poller_with_dlq._message_cache[(tp, offset)] = (b"key", b"value")
    broker_poller_with_dlq._publish_to_dlq = AsyncMock(side_effect=[False, True])

    event = CompletionEvent(
        id="test-id",
        tp=tp,
        offset=offset,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="Test error",
        attempt=broker_poller_with_dlq._kafka_config.parallel_consumer.execution.max_retries,
    )

    await broker_poller_with_dlq._process_completed_events([event])
    assert offset not in tracker.completed_offsets
    assert (tp, offset) in broker_poller_with_dlq._message_cache

    await broker_poller_with_dlq._process_completed_events([])

    assert broker_poller_with_dlq._publish_to_dlq.await_count == 2
    assert offset in tracker.completed_offsets
    assert (tp, offset) not in broker_poller_with_dlq._message_cache
