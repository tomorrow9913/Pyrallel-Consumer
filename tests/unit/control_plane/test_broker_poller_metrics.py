from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.config import (
    AdaptiveBackpressureConfig,
    AdaptiveConcurrencyConfig,
    KafkaConfig,
)
from pyrallel_consumer.control_plane.adaptive_backpressure import (
    AdaptiveBackpressureController,
)
from pyrallel_consumer.control_plane.adaptive_concurrency import (
    AdaptiveConcurrencyController,
)
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    DLQPayloadMode,
    OffsetRange,
    OrderingMode,
    ProcessBatchMetrics,
    SystemMetrics,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}

    parallel_consumer_mock = MagicMock()
    parallel_consumer_mock.poll_batch_size = 1000
    parallel_consumer_mock.worker_pool_size = 8

    execution_mock = MagicMock()
    execution_mock.max_in_flight = 100
    parallel_consumer_mock.execution = execution_mock

    config.parallel_consumer = parallel_consumer_mock
    return config


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
    mock_execution_engine,
    mock_work_manager,
):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
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
        assert metrics.process_batch_metrics is None

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

    @pytest.mark.asyncio
    async def test_get_metrics_includes_process_batch_metrics_from_engine(
        self, broker_poller_with_mocks, mock_execution_engine
    ):
        mock_execution_engine.get_runtime_metrics.return_value = ProcessBatchMetrics(
            size_flush_count=3,
            timer_flush_count=2,
            close_flush_count=1,
            total_flushed_items=12,
            last_flush_size=4,
            last_flush_wait_seconds=0.05,
            buffered_items=1,
            buffered_age_seconds=0.2,
        )

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.process_batch_metrics is not None
        assert metrics.process_batch_metrics.size_flush_count == 3
        assert metrics.process_batch_metrics.buffered_items == 1

    @pytest.mark.asyncio
    async def test_get_metrics_includes_adaptive_runtime_snapshots(
        self, broker_poller_with_mocks
    ):
        broker_poller_with_mocks._adaptive_backpressure_controller = (
            AdaptiveBackpressureController(
                configured_max_in_flight=100,
                config=AdaptiveBackpressureConfig(
                    enabled=True,
                    min_in_flight=8,
                    scale_up_step=16,
                    scale_down_step=16,
                    cooldown_ms=1000,
                    lag_scale_up_threshold=2500,
                    low_latency_threshold_ms=25.0,
                    high_latency_threshold_ms=125.0,
                ),
            )
        )
        broker_poller_with_mocks._adaptive_concurrency_controller = (
            AdaptiveConcurrencyController(
                AdaptiveConcurrencyConfig(
                    enabled=True,
                    min_in_flight=10,
                    scale_up_step=8,
                    scale_down_step=16,
                    cooldown_ms=500,
                ),
                configured_max_in_flight=100,
            )
        )

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.adaptive_backpressure is not None
        assert metrics.adaptive_backpressure.configured_max_in_flight == 100
        assert metrics.adaptive_backpressure.min_in_flight == 8
        assert metrics.adaptive_backpressure.scale_up_step == 16
        assert metrics.adaptive_backpressure.scale_down_step == 16
        assert metrics.adaptive_backpressure.cooldown_ms == 1000
        assert metrics.adaptive_backpressure.lag_scale_up_threshold == 2500
        assert metrics.adaptive_backpressure.last_decision == "hold"

        assert metrics.adaptive_concurrency is not None
        assert metrics.adaptive_concurrency.configured_max_in_flight == 100
        assert metrics.adaptive_concurrency.min_in_flight == 10
        assert metrics.adaptive_concurrency.scale_up_step == 8
        assert metrics.adaptive_concurrency.scale_down_step == 16
        assert metrics.adaptive_concurrency.cooldown_ms == 500

    @pytest.mark.asyncio
    async def test_get_metrics_clamps_adaptive_backpressure_effective_max_to_runtime_limit(
        self, broker_poller_with_mocks
    ):
        broker_poller_with_mocks._adaptive_backpressure_controller = (
            AdaptiveBackpressureController(
                configured_max_in_flight=100,
                config=AdaptiveBackpressureConfig(
                    enabled=True,
                    min_in_flight=8,
                    scale_up_step=16,
                    scale_down_step=16,
                    cooldown_ms=1000,
                    lag_scale_up_threshold=2500,
                    low_latency_threshold_ms=25.0,
                    high_latency_threshold_ms=125.0,
                ),
            )
        )
        broker_poller_with_mocks._adaptive_concurrency_controller = (
            AdaptiveConcurrencyController(
                AdaptiveConcurrencyConfig(
                    enabled=True,
                    min_in_flight=10,
                    scale_up_step=8,
                    scale_down_step=16,
                    cooldown_ms=500,
                ),
                configured_max_in_flight=100,
            )
        )
        # Simulate adaptive concurrency having reduced max_in_flight before this poller
        # emits runtime snapshots.
        broker_poller_with_mocks._set_runtime_max_in_flight(40, log_change=False)

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.adaptive_backpressure is not None
        assert metrics.adaptive_backpressure.effective_max_in_flight == 40
        assert metrics.adaptive_backpressure.configured_max_in_flight == 100
        assert metrics.adaptive_concurrency is not None
        assert metrics.adaptive_concurrency.effective_max_in_flight == 40

    @pytest.mark.asyncio
    async def test_get_metrics_aligns_adaptive_backpressure_effective_max_when_runtime_limit_scales_up(
        self, broker_poller_with_mocks
    ):
        broker_poller_with_mocks._adaptive_backpressure_controller = (
            AdaptiveBackpressureController(
                configured_max_in_flight=100,
                config=AdaptiveBackpressureConfig(
                    enabled=True,
                    min_in_flight=8,
                    scale_up_step=16,
                    scale_down_step=16,
                    cooldown_ms=1000,
                    lag_scale_up_threshold=2500,
                    low_latency_threshold_ms=25.0,
                    high_latency_threshold_ms=125.0,
                ),
            )
        )
        broker_poller_with_mocks._adaptive_concurrency_controller = (
            AdaptiveConcurrencyController(
                AdaptiveConcurrencyConfig(
                    enabled=True,
                    min_in_flight=10,
                    scale_up_step=8,
                    scale_down_step=16,
                    cooldown_ms=500,
                ),
                configured_max_in_flight=100,
            )
        )
        broker_poller_with_mocks._adaptive_backpressure_controller.evaluate(
            total_true_lag=0,
            total_queued=10,
            avg_completion_latency_seconds=0.5,
            is_paused=True,
            now_monotonic=1.0,
        )
        broker_poller_with_mocks._set_runtime_max_in_flight(60, log_change=False)

        metrics = broker_poller_with_mocks.get_metrics()

        assert metrics.adaptive_backpressure is not None
        assert metrics.adaptive_backpressure.effective_max_in_flight == 60
        assert metrics.adaptive_concurrency is not None
        assert metrics.adaptive_concurrency.effective_max_in_flight == 60

    @pytest.mark.asyncio
    async def test_get_runtime_snapshot_projects_runtime_state(
        self, broker_poller_with_mocks, mock_work_manager, mock_offset_tracker
    ):
        tp = DtoTopicPartition("test-topic", 0)
        mock_offset_tracker.last_fetched_offset = 100
        mock_offset_tracker.last_committed_offset = 90
        mock_offset_tracker.get_current_epoch.return_value = 2
        mock_offset_tracker.get_gaps.return_value = [OffsetRange(91, 95)]
        mock_offset_tracker.get_blocking_offset_durations.return_value = {91: 1.25}
        broker_poller_with_mocks._offset_trackers[tp] = mock_offset_tracker

        mock_work_manager.get_total_in_flight_count.return_value = 6
        mock_work_manager.get_total_queued_messages.return_value = 4
        mock_work_manager.get_virtual_queue_sizes.return_value = {tp: {"keyA": 4}}
        mock_work_manager.get_in_flight_counts.return_value = {tp: 2}
        mock_work_manager.is_rebalancing.return_value = False

        process_metrics = ProcessBatchMetrics(
            size_flush_count=3,
            timer_flush_count=2,
            close_flush_count=1,
            total_flushed_items=12,
            last_flush_size=4,
            last_flush_wait_seconds=0.05,
            buffered_items=1,
            buffered_age_seconds=0.2,
        )
        broker_poller_with_mocks._execution_engine.get_runtime_metrics.return_value = (
            process_metrics
        )
        broker_poller_with_mocks._work_manager.get_min_in_flight_offset.return_value = (
            92
        )
        broker_poller_with_mocks._message_cache_size_bytes = 64
        broker_poller_with_mocks._message_cache = {(tp, 91): (b"k", b"v")}
        broker_poller_with_mocks.ORDERING_MODE = OrderingMode.PARTITION
        broker_poller_with_mocks._kafka_config.dlq_enabled = True
        broker_poller_with_mocks._kafka_config.DLQ_TOPIC_SUFFIX = ".dlq"
        broker_poller_with_mocks._kafka_config.dlq_payload_mode = (
            DLQPayloadMode.METADATA_ONLY
        )

        snapshot = broker_poller_with_mocks.get_runtime_snapshot()

        assert snapshot.queue.total_in_flight == 6
        assert snapshot.queue.total_queued == 4
        assert snapshot.queue.max_in_flight == 100
        assert snapshot.queue.configured_max_in_flight == 100
        assert snapshot.queue.ordering_mode == OrderingMode.PARTITION
        assert snapshot.adaptive_concurrency is None
        assert snapshot.retry.max_retries == (
            broker_poller_with_mocks._kafka_config.parallel_consumer.execution.max_retries
        )
        assert snapshot.dlq.enabled is True
        assert snapshot.dlq.topic == "test-topic.dlq"
        assert snapshot.dlq.payload_mode == DLQPayloadMode.METADATA_ONLY
        assert snapshot.dlq.message_cache_size_bytes == 64
        assert snapshot.dlq.message_cache_entry_count == 1
        assert snapshot.process_batch_metrics == process_metrics
        assert len(snapshot.partitions) == 1
        partition = snapshot.partitions[0]
        assert partition.current_epoch == 2
        assert partition.blocking_offset == 91
        assert partition.in_flight_count == 2
        assert partition.min_in_flight_offset == 92
