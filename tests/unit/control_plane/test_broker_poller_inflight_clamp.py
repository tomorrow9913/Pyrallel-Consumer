from unittest.mock import MagicMock

import pytest
from confluent_kafka import Consumer, Message

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


class DummyProcessEngine(ProcessExecutionEngine):
    def __init__(self):
        self._in_flight_registry = {
            ("test-topic", 0, 0): {"offset": 5, "topic": "test-topic", "partition": 0}
        }

    def get_min_inflight_offset(self, tp: DtoTopicPartition):
        return 5 if tp.topic == "test-topic" and tp.partition == 0 else None

    async def poll_completed_events(self, batch_limit: int = 1000):
        return []

    async def shutdown(self):
        return None


@pytest.mark.asyncio
async def test_commit_clamped_by_inflight_registry(monkeypatch):
    kafka_config = KafkaConfig()
    kafka_config.parallel_consumer.poll_batch_size = 1
    kafka_config.parallel_consumer.worker_pool_size = 1
    kafka_config.parallel_consumer.queue_max_messages = 0
    kafka_config.parallel_consumer.diag_log_every = 1000
    kafka_config.parallel_consumer.blocking_warn_seconds = 5.0
    kafka_config.parallel_consumer.blocking_cache_ttl = 100
    kafka_config.parallel_consumer.execution.max_in_flight = 10

    consumer = MagicMock(spec=Consumer)
    consumer.assignment.return_value = [MagicMock(spec=Message)]

    engine = DummyProcessEngine()
    offset_tracker = MagicMock(spec=OffsetTracker)
    offset_tracker.last_committed_offset = 0
    offset_tracker.completed_offsets = [1, 2, 3, 4, 5, 6]

    work_manager = MagicMock(spec=WorkManager)
    work_manager.get_virtual_queue_sizes.return_value = {}
    work_manager.get_gaps.return_value = {}
    work_manager.get_blocking_offsets.return_value = {}
    work_manager.get_total_in_flight_count.return_value = 0

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=engine,  # type: ignore[arg-type]
        work_manager=work_manager,
    )
    poller.consumer = consumer
    poller._offset_trackers = {DtoTopicPartition("test-topic", 0): offset_tracker}

    poller._running = True

    async def run_once():
        await poller._check_backpressure()
        commits_to_make = []
        for tp, tracker in poller._offset_trackers.items():
            potential_hwm = tracker.last_committed_offset
            for offset in tracker.completed_offsets:
                if offset == potential_hwm + 1:
                    potential_hwm = offset
                else:
                    break
            min_inflight = poller._get_min_inflight_offset(tp)
            if min_inflight is not None and min_inflight <= potential_hwm:
                potential_hwm = min_inflight - 1
            if potential_hwm > tracker.last_committed_offset:
                commits_to_make.append((tp, potential_hwm))

        for tp, safe_offset in commits_to_make:
            kafka_tp = MagicMock()
            kafka_tp.topic = tp.topic
            kafka_tp.partition = tp.partition
            kafka_tp.offset = safe_offset + 1
        return commits_to_make

    commits = await run_once()
    assert commits == [(DtoTopicPartition("test-topic", 0), 4)]
