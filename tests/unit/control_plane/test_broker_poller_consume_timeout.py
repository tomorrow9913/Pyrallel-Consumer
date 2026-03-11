from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import Consumer

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test-group"}
    config.get_producer_config.return_value = {}

    execution = MagicMock()
    execution.max_in_flight = 1000
    execution.max_retries = 3

    parallel_consumer = MagicMock()
    parallel_consumer.poll_batch_size = 50
    parallel_consumer.worker_pool_size = 8
    parallel_consumer.execution = execution
    config.parallel_consumer = parallel_consumer
    config.dlq_enabled = False

    return config


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


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
    poller._work_manager = MagicMock()
    poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    poller._work_manager.schedule = AsyncMock()
    poller._work_manager.get_virtual_queue_sizes.return_value = {}
    return poller


@pytest.mark.asyncio
async def test_get_consume_timeout_returns_zero_with_in_flight_work(broker_poller):
    broker_poller._work_manager.get_total_in_flight_count.return_value = 1

    timeout = await broker_poller._get_consume_timeout_seconds()

    assert timeout == 0.0


@pytest.mark.asyncio
async def test_get_consume_timeout_returns_zero_with_queued_work(broker_poller):
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {
        "tp-0": {"key-a": 2}
    }

    timeout = await broker_poller._get_consume_timeout_seconds()

    assert timeout == 0.0


@pytest.mark.asyncio
async def test_run_consumer_uses_non_blocking_consume_timeout_when_work_remains(
    broker_poller,
):
    broker_poller._running = True
    broker_poller._offset_trackers = {}
    broker_poller._max_blocking_duration_ms = 0

    consume_timeouts: list[float] = []
    work_state = [1, 1]

    def get_total_in_flight_count():
        return work_state.pop(0)

    def fake_consume(num_messages=1, timeout=0.1):
        consume_timeouts.append(timeout)
        broker_poller._running = False
        return []

    broker_poller._work_manager.get_total_in_flight_count.side_effect = (
        get_total_in_flight_count
    )
    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    assert consume_timeouts == [0.0]


@pytest.mark.asyncio
async def test_run_consumer_uses_idle_timeout_when_no_work_remains(broker_poller):
    broker_poller._running = True
    broker_poller._offset_trackers = {}
    broker_poller._max_blocking_duration_ms = 0
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0

    consume_timeouts: list[float] = []

    def fake_consume(num_messages=1, timeout=0.1):
        consume_timeouts.append(timeout)
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    assert consume_timeouts == [0.1]
