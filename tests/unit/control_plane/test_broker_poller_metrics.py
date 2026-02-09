from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    return config


@pytest.fixture
def mock_work_manager():
    wm = MagicMock(spec=WorkManager)
    wm.get_total_in_flight_count.return_value = 10
    wm.get_virtual_queue_sizes.return_value = {
        DtoTopicPartition("topic", 0): {"key1": 5}
    }
    return wm


@pytest.fixture
def broker_poller(mock_kafka_config, mock_work_manager):
    poller = BrokerPoller(
        consume_topic="topic",
        kafka_config=mock_kafka_config,
        message_processor=AsyncMock(),
        execution_engine=AsyncMock(),
        work_manager=mock_work_manager,
    )
    return poller


def test_get_metrics(broker_poller, mock_work_manager):
    tp = DtoTopicPartition("topic", 0)
    tracker = MagicMock(spec=OffsetTracker)
    tracker.last_fetched_offset = 100
    tracker.last_committed_offset = 90
    tracker.get_gaps.return_value = [OffsetRange(95, 95)]
    tracker.get_blocking_offset_durations.return_value = {95: 2.5}

    broker_poller._offset_trackers[tp] = tracker
    broker_poller._is_paused = True

    metrics = broker_poller.get_metrics()

    assert metrics.total_in_flight == 10
    assert metrics.is_paused is True
    assert len(metrics.partitions) == 1

    pm = metrics.partitions[0]
    assert pm.tp == tp
    assert pm.true_lag == 10
    assert pm.gap_count == 1
    assert pm.blocking_offset == 95
    assert pm.blocking_duration_sec == 2.5
    assert pm.queued_count == 5
