from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from confluent_kafka import Consumer

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    config.dlq_enabled = False

    execution = MagicMock()
    execution.max_retries = 3

    parallel_consumer = MagicMock()
    parallel_consumer.poll_batch_size = 1000
    parallel_consumer.worker_pool_size = 8
    parallel_consumer.execution = execution
    config.parallel_consumer = parallel_consumer

    return config


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def topic_partition():
    return DtoTopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def completion_event(topic_partition):
    return CompletionEvent(
        id="work-1",
        tp=topic_partition,
        offset=0,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )


@pytest.fixture
def broker_poller(mock_kafka_config, mock_execution_engine):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
    )
    poller.consumer = MagicMock(spec=Consumer)
    poller.producer = MagicMock()
    poller.admin = MagicMock()
    poller._cleanup = AsyncMock()
    poller._max_blocking_duration_ms = 0
    poller.MAX_IN_FLIGHT_MESSAGES = 1000
    poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
    poller.QUEUE_MAX_MESSAGES = 0
    return poller


def _make_tracker(tp):
    tracker = MagicMock(spec=OffsetTracker)
    tracker.topic_partition = tp
    tracker.last_committed_offset = -1
    tracker.last_fetched_offset = -1
    tracker.completed_offsets = set()
    tracker.get_current_epoch.return_value = 1
    return tracker


def _run_consume_loop_once_then_stop(broker_poller, first_batch):
    call_count = 0

    def fake_consume(num_messages=1, timeout=0.1):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_batch
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)


@pytest.mark.asyncio
async def test_run_consumer_schedules_after_completion_even_without_new_messages(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    _run_consume_loop_once_then_stop(broker_poller, [])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    assert broker_poller._work_manager.schedule.await_count == 1


@pytest.mark.asyncio
async def test_run_consumer_schedules_twice_when_messages_and_completions_share_iteration(
    broker_poller, topic_partition, completion_event
):
    tracker = _make_tracker(topic_partition)
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.submit_message = AsyncMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()

    message = MagicMock()
    message.error.return_value = None
    message.topic.return_value = topic_partition.topic
    message.partition.return_value = topic_partition.partition
    message.offset.return_value = 0
    message.key.return_value = b"key-A"
    message.value.return_value = b"payload-0"
    _run_consume_loop_once_then_stop(broker_poller, [message])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    broker_poller._work_manager.submit_message.assert_awaited_once_with(
        tp=topic_partition,
        offset=0,
        epoch=1,
        key=b"key-A",
        payload=b"payload-0",
    )
    assert broker_poller._work_manager.schedule.await_count == 2
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    assert broker_poller._work_manager.schedule.await_args_list == [call(), call()]
