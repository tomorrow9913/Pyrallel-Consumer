from __future__ import annotations

from unittest.mock import MagicMock

from pyrallel_consumer.config import ExecutionConfig, PoisonMessageConfig
from pyrallel_consumer.dto import (
    AdaptiveConcurrencyRuntimeSnapshot,
    DLQPayloadMode,
    OffsetRange,
    OrderingMode,
)
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


def test_build_system_metrics_preserves_zero_blocking_offset_duration() -> None:
    from pyrallel_consumer.control_plane.broker_runtime_support import (
        BrokerRuntimeSupport,
    )

    tp = DtoTopicPartition("test-topic", 0)
    tracker = MagicMock()
    tracker.last_fetched_offset = 10
    tracker.last_committed_offset = -1
    tracker.get_gaps.return_value = [OffsetRange(0, 0)]
    tracker.get_blocking_offset_durations.return_value = {0: 2.5}
    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 1
    work_manager.get_virtual_queue_sizes.return_value = {tp: {"key": 1}}

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

    assert metrics.partitions[0].blocking_offset == 0
    assert metrics.partitions[0].blocking_duration_sec == 2.5


def test_build_runtime_snapshot_projects_assignments_and_runtime_state() -> None:
    from pyrallel_consumer.control_plane.broker_runtime_support import (
        BrokerRuntimeSupport,
    )

    tp = DtoTopicPartition("test-topic", 0)
    tracker = MagicMock()
    tracker.last_fetched_offset = 100
    tracker.last_committed_offset = 90
    tracker.get_current_epoch.return_value = 3
    tracker.get_gaps.return_value = [OffsetRange(91, 95), OffsetRange(98, 99)]
    tracker.get_blocking_offset_durations.return_value = {91: 1.5}

    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 10
    work_manager.get_total_queued_messages.return_value = 7
    work_manager.get_virtual_queue_sizes.return_value = {tp: {"key1": 2, "key2": 3}}
    work_manager.get_in_flight_counts.return_value = {tp: 4}
    work_manager.get_min_in_flight_offset.return_value = 92
    work_manager.is_rebalancing.return_value = True

    execution_engine = MagicMock()
    runtime_metrics = MagicMock()
    execution_engine.get_runtime_metrics.return_value = runtime_metrics

    execution_config = ExecutionConfig(
        max_retries=5,
        retry_backoff_ms=200,
        exponential_backoff=False,
        max_retry_backoff_ms=500,
        retry_jitter_ms=30,
    )

    support = BrokerRuntimeSupport(
        work_manager=work_manager,
        offset_trackers={tp: tracker},
        consumer=None,
        execution_engine=execution_engine,
        execution_config=execution_config,
        consume_topic="source-topic",
        ordering_mode=OrderingMode.KEY_HASH,
        dlq_enabled=True,
        dlq_topic_suffix=".dlq",
        dlq_payload_mode=DLQPayloadMode.METADATA_ONLY,
        message_cache_size_bytes=128,
        message_cache_entry_count=2,
        max_in_flight_messages=100,
        min_in_flight_messages_to_resume=70,
        queue_max_messages=0,
        queue_resume_threshold=0,
        is_paused=False,
        blocking_warn_seconds=5.0,
        logger=MagicMock(),
        adaptive_concurrency=AdaptiveConcurrencyRuntimeSnapshot(
            configured_max_in_flight=100,
            effective_max_in_flight=64,
            min_in_flight=25,
            scale_up_step=10,
            scale_down_step=20,
            cooldown_ms=1500,
        ),
        poison_message_config=PoisonMessageConfig(
            enabled=True,
            failure_threshold=2,
            cooldown_ms=7500,
        ),
        poison_message_open_circuit_count=3,
    )

    snapshot = support.build_runtime_snapshot()

    assert snapshot.queue.total_in_flight == 10
    assert snapshot.queue.total_queued == 7
    assert snapshot.queue.max_in_flight == 100
    assert snapshot.queue.is_paused is False
    assert snapshot.queue.is_rebalancing is True
    assert snapshot.queue.ordering_mode == OrderingMode.KEY_HASH
    assert snapshot.retry.max_retries == 5
    assert snapshot.retry.retry_backoff_ms == 200
    assert snapshot.retry.exponential_backoff is False
    assert snapshot.retry.max_retry_backoff_ms == 500
    assert snapshot.retry.retry_jitter_ms == 30
    assert snapshot.dlq.enabled is True
    assert snapshot.dlq.topic == "source-topic.dlq"
    assert snapshot.dlq.payload_mode == DLQPayloadMode.METADATA_ONLY
    assert snapshot.dlq.message_cache_size_bytes == 128
    assert snapshot.dlq.message_cache_entry_count == 2
    assert snapshot.process_batch_metrics is runtime_metrics
    assert snapshot.adaptive_concurrency is not None
    assert snapshot.adaptive_concurrency.configured_max_in_flight == 100
    assert snapshot.adaptive_concurrency.effective_max_in_flight == 64
    assert snapshot.adaptive_concurrency.min_in_flight == 25
    assert snapshot.adaptive_concurrency.scale_up_step == 10
    assert snapshot.adaptive_concurrency.scale_down_step == 20
    assert snapshot.adaptive_concurrency.cooldown_ms == 1500
    assert snapshot.poison_message is not None
    assert snapshot.poison_message.enabled is True
    assert snapshot.poison_message.failure_threshold == 2
    assert snapshot.poison_message.cooldown_ms == 7500
    assert snapshot.poison_message.open_circuit_count == 3
    assert len(snapshot.partitions) == 1
    partition_snapshot = snapshot.partitions[0]
    assert partition_snapshot.tp == tp
    assert partition_snapshot.current_epoch == 3
    assert partition_snapshot.last_committed_offset == 90
    assert partition_snapshot.last_fetched_offset == 100
    assert partition_snapshot.true_lag == 10
    assert partition_snapshot.gaps == [OffsetRange(91, 95), OffsetRange(98, 99)]
    assert partition_snapshot.blocking_offset == 91
    assert partition_snapshot.blocking_duration_sec == 1.5
    assert partition_snapshot.queued_count == 5
    assert partition_snapshot.in_flight_count == 4
    assert partition_snapshot.min_in_flight_offset == 92


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
