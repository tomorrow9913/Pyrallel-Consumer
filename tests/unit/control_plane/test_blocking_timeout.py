import asyncio

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class _FakeEngine(BaseExecutionEngine):
    async def submit(self, work_item: WorkItem) -> None:
        return None

    async def poll_completed_events(self, batch_limit: int = 1000):
        return []

    def get_in_flight_count(self) -> int:
        return 0

    async def shutdown(self) -> None:
        return None


@pytest.mark.asyncio
async def test_handle_blocking_timeout_forces_failure_and_dlq_path():
    tp = TopicPartition(topic="demo", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
    )

    kafka_config = KafkaConfig()
    kafka_config.parallel_consumer.max_blocking_duration_ms = 1
    kafka_config.dlq_enabled = False

    engine = _FakeEngine()
    work_manager = WorkManager(execution_engine=engine)

    poller = BrokerPoller(
        consume_topic="demo",
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )

    poller._offset_trackers[tp] = tracker  # type: ignore[attr-defined]

    work_item = WorkItem(id="w1", tp=tp, offset=0, epoch=0, key=b"k", payload=b"v")
    work_manager._in_flight_work_items[work_item.id] = work_item  # type: ignore[attr-defined]

    tracker.update_last_fetched_offset(0)
    tracker.get_gaps()
    poller._message_cache[(tp, 0)] = (b"k", b"v")  # type: ignore[attr-defined]

    await asyncio.sleep(0.01)

    timeout_events = await poller._handle_blocking_timeouts()
    assert len(timeout_events) == 1

    await poller._process_completed_events(timeout_events)

    forced_event = timeout_events[0]
    assert forced_event.status == CompletionStatus.FAILURE
    assert forced_event.attempt == kafka_config.parallel_consumer.execution.max_retries
    assert (tp, 0) not in poller._message_cache


@pytest.mark.asyncio
async def test_handle_blocking_timeout_noop_when_threshold_disabled():
    tp = TopicPartition(topic="demo", partition=1)
    tracker = OffsetTracker(
        topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
    )

    kafka_config = KafkaConfig()
    kafka_config.parallel_consumer.max_blocking_duration_ms = 0

    engine = _FakeEngine()
    work_manager = WorkManager(execution_engine=engine)

    poller = BrokerPoller(
        consume_topic="demo",
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )

    poller._offset_trackers[tp] = tracker  # type: ignore[attr-defined]
    timeout_events = await poller._handle_blocking_timeouts()

    assert timeout_events == []
