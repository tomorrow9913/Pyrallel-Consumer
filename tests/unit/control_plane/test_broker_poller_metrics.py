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
    CompletionEvent,
    CompletionStatus,
    DLQPayloadMode,
    EngineRuntimeDiagnostics,
    EngineWorkerDiagnostics,
    OffsetRange,
    OrderingMode,
    PipelineAdmissionDiagnostics,
    PipelineCount,
    PipelineDiagnosticsScope,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSupportState,
    PipelineDispatchCapacityDiagnostics,
    PipelineSettlementBlockerReason,
    PipelineStage,
    PipelineSubqueueDiagnostics,
    PipelineWorkerDiagnostics,
    ProcessBatchMetrics,
    ProcessRuntimeDiagnostics,
    SystemMetrics,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkManagerPipelineDiagnostics
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


def _empty_work_manager_pipeline_diagnostics() -> WorkManagerPipelineDiagnostics:
    return WorkManagerPipelineDiagnostics(
        stage_counts={
            stage: PipelineCount(count=0, oldest_age_ms=None) for stage in PipelineStage
        },
        blocked_counts={},
        dispatch_capacity=PipelineDispatchCapacityDiagnostics(blocked_items=0),
        admission=PipelineAdmissionDiagnostics(blocked_items=0),
        workers=PipelineWorkerDiagnostics(
            total=0,
            executing=0,
            admitted=None,
            top_k_loads=[],
            support_state=PipelineDiagnosticsSupportState.NOT_IMPLEMENTED,
        ),
        subqueues=PipelineSubqueueDiagnostics(
            total=0,
            queued=0,
            queued_items=0,
            eligible_subqueues=0,
            eligible_items=0,
            blocked_subqueues=0,
            blocked_items=0,
            top_k_depths=[],
        ),
        stage_support={
            stage: PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
            for stage in PipelineStage
        },
        section_support={
            section: PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
            for section in PipelineDiagnosticsSection
        },
    )


def test_work_manager_only_pipeline_poll_diagnostics_is_not_implemented() -> None:
    # Given: inputs for `work manager only pipeline poll diagnostics i...` are prepared.
    # When: the broker poller metrics code path is exercised.
    diagnostics = _empty_work_manager_pipeline_diagnostics()

    # Then: the expected `work manager only pipeline poll diagnostics i...` behavior is asserted.
    assert (
        diagnostics.poll.support_state
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.POLL]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )


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
    wm.get_pipeline_diagnostics.return_value = (
        _empty_work_manager_pipeline_diagnostics()
    )
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
        # Given: inputs for `get metrics initial state` are prepared.
        metrics = broker_poller_with_mocks.get_metrics()
        # When: the broker poller metrics code path is exercised.
        # Then: the expected `get metrics initial state` behavior is asserted.
        assert isinstance(metrics, SystemMetrics)
        assert metrics.total_in_flight == 0
        assert metrics.is_paused is False
        assert len(metrics.partitions) == 0
        assert metrics.process_batch_metrics is None

    @pytest.mark.asyncio
    async def test_get_metrics_with_in_flight_messages(
        self, broker_poller_with_mocks, mock_work_manager, mock_offset_tracker
    ):
        # Given: inputs for `get metrics with in flight messages` are prepared.
        mock_work_manager.get_total_in_flight_count.return_value = 5

        tp1 = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._offset_trackers[tp1] = mock_offset_tracker

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics with in flight messages` behavior is asserted.
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
        # Given: inputs for `get metrics with lag and gaps` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics with lag and gaps` behavior is asserted.
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
    async def test_get_metrics_includes_completed_offset_skip_count(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `get metrics includes completed offset skip count` are prepared.
        tp = DtoTopicPartition("test-topic", 0)

        broker_poller_with_mocks._record_completed_offset_skip(tp, 4)
        broker_poller_with_mocks._record_completed_offset_skip(tp, 6)

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics includes completed offset skip count` behavior is asserted.
        assert metrics.completed_offset_skips_total == 2

    @pytest.mark.asyncio
    async def test_completed_offset_skip_callback_does_not_call_exporter_directly(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `completed offset skip callback does not call...` are prepared.
        tp = DtoTopicPartition("test-topic", 0)
        exporter = MagicMock()
        broker_poller_with_mocks.set_metrics_exporter(exporter)

        broker_poller_with_mocks._record_completed_offset_skip(tp, 4)

        # When: the broker poller metrics code path is exercised.
        # Then: the expected `completed offset skip callback does not call...` behavior is asserted.
        assert broker_poller_with_mocks.get_metrics().completed_offset_skips_total == 1
        exporter.record_completed_offset_skip.assert_not_called()

    @pytest.mark.asyncio
    async def test_get_pipeline_diagnostics_exposes_completed_offset_skip_count(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `get pipeline diagnostics exposes completed of...` are prepared.
        tp = DtoTopicPartition("test-topic", 0)

        broker_poller_with_mocks._record_completed_offset_skip(tp, 4)
        broker_poller_with_mocks._record_completed_offset_skip(tp, 6)

        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # When: the broker poller metrics code path is exercised.
        # Then: the expected `get pipeline diagnostics exposes completed of...` behavior is asserted.
        assert isinstance(diagnostics, WorkManagerPipelineDiagnostics)
        assert diagnostics.poll.completed_offset_skips_total == 2

    @pytest.mark.asyncio
    async def test_get_pipeline_diagnostics_exposes_poll_record_and_event_counts(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `get pipeline diagnostics exposes poll record...` are prepared.
        broker_poller_with_mocks._record_pipeline_poll_batch([object(), object()])
        broker_poller_with_mocks._record_pipeline_poll_batch([])
        broker_poller_with_mocks._record_pipeline_poll_error()

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics exposes poll record...` behavior is asserted.
        assert diagnostics.poll.records_total == 2
        assert diagnostics.poll.nonempty_polls_total == 1
        assert diagnostics.poll.empty_polls_total == 1
        assert diagnostics.poll.error_polls_total == 1

    @pytest.mark.asyncio
    async def test_get_metrics_when_paused(self, broker_poller_with_mocks):
        # Given: inputs for `get metrics when paused` are prepared.
        broker_poller_with_mocks._is_paused = True
        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()
        # Then: the expected `get metrics when paused` behavior is asserted.
        assert metrics.is_paused is True

    @pytest.mark.asyncio
    async def test_get_metrics_multiple_partitions(
        self, broker_poller_with_mocks, mock_work_manager
    ):
        # Given: inputs for `get metrics multiple partitions` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics multiple partitions` behavior is asserted.
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
        # Given: inputs for `get metrics includes process batch metrics fr...` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics includes process batch metrics fr...` behavior is asserted.
        assert metrics.process_batch_metrics is not None
        assert metrics.process_batch_metrics.size_flush_count == 3
        assert metrics.process_batch_metrics.buffered_items == 1

    @pytest.mark.asyncio
    async def test_get_metrics_projects_process_batch_metrics_from_runtime_envelope(
        self, broker_poller_with_mocks, mock_execution_engine
    ):
        # Given: inputs for `get metrics projects process batch metrics fr...` are prepared.
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
        mock_execution_engine.get_runtime_metrics.return_value = (
            EngineRuntimeDiagnostics(
                engine_type="process",
                process=ProcessRuntimeDiagnostics(batch_metrics=process_metrics),
            )
        )

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics projects process batch metrics fr...` behavior is asserted.
        assert metrics.process_batch_metrics == process_metrics

    @pytest.mark.asyncio
    async def test_get_metrics_includes_adaptive_runtime_snapshots(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `get metrics includes adaptive runtime snapshots` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics includes adaptive runtime snapshots` behavior is asserted.
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
        # Given: inputs for `get metrics clamps adaptive backpressure effe...` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics clamps adaptive backpressure effe...` behavior is asserted.
        assert metrics.adaptive_backpressure is not None
        assert metrics.adaptive_backpressure.effective_max_in_flight == 40
        assert metrics.adaptive_backpressure.configured_max_in_flight == 100
        assert metrics.adaptive_concurrency is not None
        assert metrics.adaptive_concurrency.effective_max_in_flight == 40

    @pytest.mark.asyncio
    async def test_get_metrics_aligns_adaptive_backpressure_effective_max_when_runtime_limit_scales_up(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `get metrics aligns adaptive backpressure effe...` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        metrics = broker_poller_with_mocks.get_metrics()

        # Then: the expected `get metrics aligns adaptive backpressure effe...` behavior is asserted.
        assert metrics.adaptive_backpressure is not None
        assert metrics.adaptive_backpressure.effective_max_in_flight == 60
        assert metrics.adaptive_concurrency is not None
        assert metrics.adaptive_concurrency.effective_max_in_flight == 60

    @pytest.mark.asyncio
    async def test_get_runtime_snapshot_projects_runtime_state(
        self, broker_poller_with_mocks, mock_work_manager, mock_offset_tracker
    ):
        # Given: inputs for `get runtime snapshot projects runtime state` are prepared.
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

        # When: the broker poller metrics code path is exercised.
        snapshot = broker_poller_with_mocks.get_runtime_snapshot()

        # Then: the expected `get runtime snapshot projects runtime state` behavior is asserted.
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

    def test_get_pipeline_diagnostics_adds_empty_settlement_sidecar(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics adds empty settlemen...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics adds empty settlemen...` behavior is asserted.
        assert diagnostics is not pipeline_diagnostics
        assert diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED].count == 0
        assert (
            diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED].oldest_age_ms
            is None
        )
        assert diagnostics.settlement.completed_unsettled == 0
        assert diagnostics.settlement.oldest_age_ms is None
        assert diagnostics.settlement.blocker_reason is None
        assert (
            diagnostics.settlement.support_state
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        assert (
            diagnostics.stage_support[PipelineStage.COMPLETED_UNSETTLED]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        assert (
            diagnostics.section_support[PipelineDiagnosticsSection.SETTLEMENT]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )

    def test_get_pipeline_diagnostics_adds_broker_poll_sidecar(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics adds broker poll sid...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics adds broker poll sid...` behavior is asserted.
        assert diagnostics.poll.records_total == 0
        assert diagnostics.poll.nonempty_polls_total == 0
        assert diagnostics.poll.empty_polls_total == 0
        assert diagnostics.poll.error_polls_total == 0
        assert (
            diagnostics.poll.support_state == PipelineDiagnosticsSupportState.SUPPORTED
        )
        assert (
            diagnostics.section_support[PipelineDiagnosticsSection.POLL]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )

    def test_record_pipeline_poll_batch_updates_broker_owned_poll_sidecar(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `record pipeline poll batch updates broker own...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None

        broker_poller_with_mocks._record_pipeline_poll_batch([object(), object()])
        broker_poller_with_mocks._record_pipeline_poll_batch([])
        broker_poller_with_mocks._record_pipeline_poll_error()

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()
        # Then: the expected `record pipeline poll batch updates broker own...` behavior is asserted.
        assert diagnostics.poll.records_total == 2
        assert diagnostics.poll.nonempty_polls_total == 1
        assert diagnostics.poll.empty_polls_total == 1
        assert diagnostics.poll.error_polls_total == 1

    def test_on_revoke_drops_unsettled_completion_timestamp_ledger(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `on revoke drops unsettled completion timestam...` are prepared.
        tp = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._unsettled_completion_timestamps_by_partition[tp] = {
            10: 100.0
        }
        revoked = [MagicMock(topic="test-topic", partition=0)]
        broker_poller_with_mocks._rebalance_support.handle_revoke = MagicMock()

        # When: the broker poller metrics code path is exercised.
        broker_poller_with_mocks._on_revoke(MagicMock(), revoked)

        # Then: the expected `on revoke drops unsettled completion timestam...` behavior is asserted.
        assert (
            broker_poller_with_mocks._unsettled_completion_timestamps_by_partition == {}
        )

    def test_get_pipeline_diagnostics_reports_completed_unsettled_from_broker_ledger(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics reports completed un...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None
        tp = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._dirty_commit_partitions.add(tp)
        broker_poller_with_mocks._unsettled_completions_by_partition[tp] = 2
        broker_poller_with_mocks._completions_since_last_commit = 2

        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # When: the broker poller metrics code path is exercised.
        completed = diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED]
        # Then: the expected `get pipeline diagnostics reports completed un...` behavior is asserted.
        assert completed.count == 2
        assert completed.oldest_age_ms is None
        assert diagnostics.settlement.completed_unsettled == 2
        assert diagnostics.settlement.oldest_age_ms is None
        assert diagnostics.settlement.blocker_reason == (
            PipelineSettlementBlockerReason.COMMIT_PENDING
        )

    def test_get_pipeline_diagnostics_does_not_count_dirty_partition_as_unsettled_message(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics does not count dirty...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None
        tp = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._dirty_commit_partitions.add(tp)
        broker_poller_with_mocks._completions_since_last_commit = 0

        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # When: the broker poller metrics code path is exercised.
        completed = diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED]
        # Then: the expected `get pipeline diagnostics does not count dirty...` behavior is asserted.
        assert completed.count == 0
        assert diagnostics.settlement.completed_unsettled == 0
        assert diagnostics.settlement.blocker_reason is None

    def test_get_pipeline_diagnostics_reports_unsettled_completion_count_after_counter_reset(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics reports unsettled co...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None
        tp = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._dirty_commit_partitions.add(tp)
        broker_poller_with_mocks._unsettled_completions_by_partition[tp] = 3
        broker_poller_with_mocks._completions_since_last_commit = 0

        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # When: the broker poller metrics code path is exercised.
        completed = diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED]
        # Then: the expected `get pipeline diagnostics reports unsettled co...` behavior is asserted.
        assert completed.count == 3
        assert diagnostics.settlement.completed_unsettled == 3
        assert diagnostics.settlement.blocker_reason == (
            PipelineSettlementBlockerReason.COMMIT_PENDING
        )

    def test_clear_committed_dirty_partitions_preserves_retained_gap_completion(
        self, broker_poller_with_mocks
    ):
        # Given: inputs for `clear committed dirty partitions preserves re...` are prepared.
        tp = DtoTopicPartition("test-topic", 0)
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=10,
            max_revoke_grace_ms=1000,
        )
        tracker.mark_complete(10)
        tracker.mark_complete(12)
        tracker.commit_through(10)
        broker_poller_with_mocks._offset_trackers[tp] = tracker
        broker_poller_with_mocks._dirty_commit_partitions.add(tp)
        broker_poller_with_mocks._unsettled_completions_by_partition[tp] = 2

        # When: the broker poller metrics code path is exercised.
        broker_poller_with_mocks._clear_committed_dirty_partitions([(tp, 10)])

        # Then: the expected `clear committed dirty partitions preserves re...` behavior is asserted.
        assert tp in broker_poller_with_mocks._dirty_commit_partitions
        assert broker_poller_with_mocks._unsettled_completions_by_partition[tp] == 1

    def test_get_pipeline_diagnostics_keeps_pending_dlq_separate_from_completed_unsettled(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics keeps pending dlq se...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None
        tp = DtoTopicPartition("test-topic", 0)
        broker_poller_with_mocks._pending_dlq_events[(tp, 12)] = CompletionEvent(
            id="work-12",
            tp=tp,
            offset=12,
            epoch=0,
            status=CompletionStatus.FAILURE,
            error="boom",
            attempt=3,
        )
        broker_poller_with_mocks._completions_since_last_commit = 1

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics keeps pending dlq se...` behavior is asserted.
        assert diagnostics.stage_counts[PipelineStage.DLQ].count == 1
        assert diagnostics.stage_counts[PipelineStage.COMPLETED_UNSETTLED].count == 0
        assert diagnostics.settlement.completed_unsettled == 0
        assert diagnostics.settlement.blocker_reason == (
            PipelineSettlementBlockerReason.DLQ_PUBLISH_PENDING
        )
        assert diagnostics.settlement.oldest_age_ms is None
        assert (
            diagnostics.stage_support[PipelineStage.DLQ]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )

    def test_get_pipeline_diagnostics_delegates_to_work_manager(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics delegates to work ma...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = None

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics delegates to work ma...` behavior is asserted.
        assert diagnostics is not pipeline_diagnostics
        mock_work_manager.get_pipeline_diagnostics.assert_called_once_with()

    def test_get_pipeline_diagnostics_combines_engine_worker_metrics(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics combines engine work...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = (
            EngineRuntimeDiagnostics(
                engine_type="process",
                workers=EngineWorkerDiagnostics(
                    total=2,
                    executing=1,
                    admitted=1,
                    top_k_loads=[3, 1],
                ),
            )
        )

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics combines engine work...` behavior is asserted.
        assert diagnostics is not pipeline_diagnostics
        assert diagnostics.scope == PipelineDiagnosticsScope.COMBINED
        assert (
            diagnostics.section_support[PipelineDiagnosticsSection.WORKERS]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        assert diagnostics.workers.total == 2
        assert diagnostics.workers.executing == 1
        assert diagnostics.workers.admitted == 1
        assert diagnostics.workers.top_k_loads == [3, 1]
        mock_work_manager.get_pipeline_diagnostics.assert_called_once_with()
        mock_execution_engine.get_runtime_metrics.assert_called_once_with()

    def test_get_pipeline_diagnostics_leaves_engine_sections_unavailable_without_workers(
        self, broker_poller_with_mocks, mock_work_manager, mock_execution_engine
    ):
        # Given: inputs for `get pipeline diagnostics leaves engine sectio...` are prepared.
        pipeline_diagnostics = _empty_work_manager_pipeline_diagnostics()
        mock_work_manager.get_pipeline_diagnostics.return_value = pipeline_diagnostics
        mock_execution_engine.get_runtime_metrics.return_value = (
            EngineRuntimeDiagnostics(
                engine_type="custom",
                workers=None,
            )
        )

        # When: the broker poller metrics code path is exercised.
        diagnostics = broker_poller_with_mocks.get_pipeline_diagnostics()

        # Then: the expected `get pipeline diagnostics leaves engine sectio...` behavior is asserted.
        assert diagnostics.scope == PipelineDiagnosticsScope.COMBINED
        assert (
            diagnostics.section_support[PipelineDiagnosticsSection.WORKERS]
            == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
        )
        assert (
            diagnostics.section_support[PipelineDiagnosticsSection.SETTLEMENT]
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        assert diagnostics.workers.support_state == (
            PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
        )
