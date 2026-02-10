from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import OffsetRange, SystemMetrics
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    return config


@pytest.fixture
def mock_message_processor():
    return AsyncMock()


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def mock_work_manager():
    wm = MagicMock(spec=WorkManager)
    wm.get_total_in_flight_count.return_value = 0
    wm.get_virtual_queue_sizes.return_value = {}
    return wm


@pytest.fixture
def mock_offset_tracker():
    tracker = MagicMock(spec=OffsetTracker)
    tracker.last_fetched_offset = -1
    tracker.last_committed_offset = -1
    tracker.get_gaps.return_value = []
    tracker.get_blocking_offset_durations.return_value = {}
    tracker.epoch = 0
    return tracker


@pytest.fixture
def broker_poller_with_mocks(
    mock_kafka_config,
    mock_message_processor,
    mock_execution_engine,
    mock_work_manager,
):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        message_processor=mock_message_processor,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )
    # Patch internal _is_paused for testing
    poller._is_paused = False
    return poller


class TestBrokerPollerMetrics:
    @pytest.mark.asyncio
    async def test_get_metrics_initial_state(self, broker_poller_with_mocks):
        metrics = broker_poller_with_mocks.get_metrics()
        assert isinstance(metrics, SystemMetrics)
        assert metrics.total_in_flight == 0
        assert metrics.is_paused is False
        assert len(metrics.partitions) == 0

    @pytest.mark.asyncio
    async def test_get_metrics_with_in_flight_messages(
        self, broker_poller_with_mocks, mock_work_manager, mock_offset_tracker
    ):
        mock_work_manager.get_total_in_flight_count.return_value = 5

        tp1 = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._offset_trackers[tp1] = mock_offset_tracker

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.total_in_flight == 5
        assert len(metrics.partitions) == 1
        assert metrics.partitions[0].tp == tp1
        assert metrics.partitions[0].true_lag == 0
        assert metrics.partitions[0].gap_count == 0
        assert metrics.partitions[0].blocking_offset is None
        assert metrics.partitions[0].blocking_duration_sec is None
        assert metrics.partitions[0].queued_count == 0

    @pytest.mark.asyncio
    async def test_get_metrics_with_lag_and_gaps(
        self, broker_poller_with_mocks, mock_work_manager, mock_offset_tracker
    ):
        mock_work_manager.get_total_in_flight_count.return_value = 10

        tp1 = DtoTopicPartition("test-topic", 0)
        tracker1 = MagicMock(spec=OffsetTracker)
        tracker1.topic_partition = tp1
        tracker1.last_fetched_offset = 100
        tracker1.last_committed_offset = 90
        tracker1.get_gaps.return_value = [OffsetRange(91, 95), OffsetRange(98, 99)]
        tracker1.get_blocking_offset_durations.return_value = {91: 1.5}
        broker_poller_with_mocks._offset_trackers[tp1] = tracker1

        mock_work_manager.get_virtual_queue_sizes.return_value = {
            tp1: {
                "key1": 2,
                "key2": 3,
            }
        }

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.total_in_flight == 10
        assert len(metrics.partitions) == 1
        p_metrics = metrics.partitions[0]
        assert p_metrics.tp == tp1
        assert p_metrics.true_lag == 10
        assert p_metrics.gap_count == 2
        assert p_metrics.blocking_offset == 91
        assert p_metrics.blocking_duration_sec == 1.5
        assert p_metrics.queued_count == 5

    @pytest.mark.asyncio
    async def test_get_metrics_when_paused(self, broker_poller_with_mocks):
        broker_poller_with_mocks._is_paused = True
        metrics = broker_poller_with_mocks.get_metrics()
        assert metrics.is_paused is True

    @pytest.mark.asyncio
    async def test_get_metrics_multiple_partitions(
        self, broker_poller_with_mocks, mock_work_manager
    ):
        mock_work_manager.get_total_in_flight_count.return_value = 15

        tp1 = DtoTopicPartition("test-topic", 0)
        tracker1 = MagicMock(spec=OffsetTracker)
        tracker1.topic_partition = tp1
        tracker1.last_fetched_offset = 100
        tracker1.last_committed_offset = 90
        tracker1.get_gaps.return_value = [OffsetRange(91, 95)]
        tracker1.get_blocking_offset_durations.return_value = {91: 1.0}

        tp2 = DtoTopicPartition("test-topic", 1)
        tracker2 = MagicMock(spec=OffsetTracker)
        tracker2.topic_partition = tp2
        tracker2.last_fetched_offset = 200
        tracker2.last_committed_offset = 198
        tracker2.get_gaps.return_value = []
        tracker2.get_blocking_offset_durations.return_value = {}

        broker_poller_with_mocks._offset_trackers = {tp1: tracker1, tp2: tracker2}

        mock_work_manager.get_virtual_queue_sizes.return_value = {
            tp1: {"keyA": 1, "keyB": 2},
            tp2: {"keyC": 3},
        }

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.total_in_flight == 15
        assert metrics.is_paused is False
        assert len(metrics.partitions) == 2

        # Verify Partition 1 metrics
        p_metrics_tp1 = next(p for p in metrics.partitions if p.tp == tp1)
        assert p_metrics_tp1.true_lag == 10
        assert p_metrics_tp1.gap_count == 1
        assert p_metrics_tp1.blocking_offset == 91
        assert p_metrics_tp1.blocking_duration_sec == 1.0
        assert p_metrics_tp1.queued_count == 3

        # Verify Partition 2 metrics
        p_metrics_tp2 = next(p for p in metrics.partitions if p.tp == tp2)
        assert p_metrics_tp2.true_lag == 2
        assert p_metrics_tp2.gap_count == 0
        assert p_metrics_tp2.blocking_offset is None
        assert p_metrics_tp2.blocking_duration_sec is None
        assert p_metrics_tp2.queued_count == 3
