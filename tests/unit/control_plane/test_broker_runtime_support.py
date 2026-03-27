from __future__ import annotations

from unittest.mock import MagicMock

from pyrallel_consumer.dto import OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def test_build_system_metrics_projects_lag_gaps_and_queue_sizes() -> None:
    from pyrallel_consumer.control_plane.broker_runtime_support import (
        BrokerRuntimeSupport,
    )

    tp = DtoTopicPartition("test-topic", 0)
    tracker = MagicMock()
    tracker.last_fetched_offset = 100
    tracker.last_committed_offset = 90
    tracker.get_gaps.return_value = [OffsetRange(91, 95), OffsetRange(98, 99)]
    tracker.get_blocking_offset_durations.return_value = {91: 1.5}
    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 10
    work_manager.get_virtual_queue_sizes.return_value = {tp: {"key1": 2, "key2": 3}}

    support = BrokerRuntimeSupport(
        work_manager=work_manager,
        offset_trackers={tp: tracker},
        consumer=None,
        max_in_flight_messages=100,
        min_in_flight_messages_to_resume=70,
        queue_max_messages=0,
        queue_resume_threshold=0,
        is_paused=False,
        blocking_warn_seconds=5.0,
        logger=MagicMock(),
    )

    metrics = support.build_system_metrics()

    assert metrics.total_in_flight == 10
    assert metrics.is_paused is False
    assert len(metrics.partitions) == 1
    partition_metrics = metrics.partitions[0]
    assert partition_metrics.true_lag == 10
    assert partition_metrics.gap_count == 2
    assert partition_metrics.blocking_offset == 91
    assert partition_metrics.blocking_duration_sec == 1.5
    assert partition_metrics.queued_count == 5


def test_check_backpressure_transitions_pause_and_resume_state() -> None:
    from pyrallel_consumer.control_plane.broker_runtime_support import (
        BrokerRuntimeSupport,
    )

    consumer = MagicMock()
    consumer.assignment.return_value = ["p0"]
    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 90
    work_manager.get_total_queued_messages.return_value = 20
    work_manager.get_virtual_queue_sizes.return_value = {}
    support = BrokerRuntimeSupport(
        work_manager=work_manager,
        offset_trackers={},
        consumer=consumer,
        max_in_flight_messages=100,
        min_in_flight_messages_to_resume=50,
        queue_max_messages=0,
        queue_resume_threshold=0,
        is_paused=False,
        blocking_warn_seconds=5.0,
        logger=MagicMock(),
    )

    is_paused = support.check_backpressure(total_queued=20)
    assert is_paused is True
    consumer.pause.assert_called_once_with(["p0"])

    support = BrokerRuntimeSupport(
        work_manager=work_manager,
        offset_trackers={},
        consumer=consumer,
        max_in_flight_messages=100,
        min_in_flight_messages_to_resume=50,
        queue_max_messages=0,
        queue_resume_threshold=0,
        is_paused=True,
        blocking_warn_seconds=5.0,
        logger=MagicMock(),
    )
    work_manager.get_total_in_flight_count.return_value = 40
    consumer.pause.reset_mock()
    consumer.resume.reset_mock()

    is_paused = support.check_backpressure(total_queued=0)
    assert is_paused is False
    consumer.resume.assert_called_once_with(["p0"])
