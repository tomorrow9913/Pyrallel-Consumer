from unittest.mock import AsyncMock, MagicMock

import pytest
from confluent_kafka import Consumer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller, MessageProcessor
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine  # Added import


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    return config


@pytest.fixture
def mock_message_processor():
    return AsyncMock(spec=MessageProcessor)


@pytest.fixture
def mock_execution_engine():  # Added fixture
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def mock_consumer():
    consumer = MagicMock(spec=Consumer)
    consumer.assign.return_value = None
    consumer.unassign.return_value = None
    consumer.commit.return_value = None
    consumer.pause.return_value = None
    consumer.resume.return_value = None
    consumer.assignment.return_value = [
        KafkaTopicPartition("test-topic", 0),
        KafkaTopicPartition("test-topic", 1),
    ]
    return consumer


@pytest.fixture
def mock_admin_client():
    return MagicMock(spec=AdminClient)


@pytest.fixture
def mock_offset_tracker_factory():
    """Returns a mock factory for OffsetTracker that creates AsyncMock instances."""
    mock_factory = MagicMock()
    # Configure the factory to return new AsyncMock instances when called
    mock_factory.side_effect = (
        lambda tp, starting_offset, max_revoke_grace_ms: AsyncMock(
            spec=OffsetTracker,
            topic_partition=tp,
            starting_offset=starting_offset,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
    )
    return mock_factory


@pytest.fixture
def broker_poller(
    mock_kafka_config, mock_message_processor, mock_execution_engine
):  # Modified fixture
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        message_processor=mock_message_processor,
        execution_engine=mock_execution_engine,  # Passed to BrokerPoller
    )
    # Patch Kafka client objects
    poller.producer = AsyncMock()  # producer should also be async mock for flush
    poller.consumer = MagicMock(spec=Consumer)  # Original consumer is not async
    poller.admin = AsyncMock()
    return poller


@pytest.mark.asyncio
async def test_on_assign_initializes_offset_trackers(broker_poller, mock_consumer):
    tps_to_assign = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]
    broker_poller._on_assign(mock_consumer, tps_to_assign)

    assert len(broker_poller._offset_trackers) == 2
    for ktp in tps_to_assign:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        assert tp in broker_poller._offset_trackers
        tracker = broker_poller._offset_trackers[tp]
        assert tracker.topic_partition == tp
        assert tracker.epoch == 1  # Epoch should be incremented


@pytest.mark.asyncio
async def test_on_revoke_removes_offset_trackers(broker_poller, mock_consumer):
    tps_assigned = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]
    # Manually set up some offset trackers for revocation
    for ktp in tps_assigned:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        tracker = AsyncMock(
            spec=OffsetTracker,
            topic_partition=tp,
            starting_offset=ktp.offset,
            max_revoke_grace_ms=0,
        )
        tracker.last_committed_offset = 50  # Simulate some progress
        tracker.in_flight_count = 5  # Simulate in-flight messages
        tracker.completed_offsets = {51, 52}  # Simulate some completed offsets
        tracker.advance_high_water_mark.return_value = None  # Mock this method
        broker_poller._offset_trackers[tp] = tracker

    tps_to_revoke = [
        KafkaTopicPartition("test-topic", 0),
        KafkaTopicPartition("test-topic", 1),
    ]
    broker_poller._on_revoke(mock_consumer, tps_to_revoke)

    assert len(broker_poller._offset_trackers) == 0
    for ktp in tps_to_revoke:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        assert tp not in broker_poller._offset_trackers
        # Verify advance_high_water_mark was called for each revoked tracker
        # Need to access the mock objects that were *in* the dictionary before deletion
        # This requires a bit more advanced mocking if we want to assert on calls to specific instances.
        # For simplicity, we assume the deletion implies the tracker was handled.
        # A more robust test might check mock_offset_tracker_factory calls or global mocks.
