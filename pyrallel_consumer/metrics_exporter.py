from __future__ import annotations

from typing import Optional, Protocol, cast

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

from pyrallel_consumer.config import MetricsConfig
from pyrallel_consumer.dto import (
    AdaptiveBackpressureSnapshot,
    AdaptiveConcurrencyRuntimeSnapshot,
    CompletionStatus,
    PipelineBlockedReason,
    PipelineCount,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSupportState,
    PipelineDispatchCapacityReason,
    PipelineStage,
    ProcessBatchMetrics,
    ResourceSignalSnapshot,
    ResourceSignalStatus,
    SystemMetrics,
    TopicPartition,
    WorkManagerPipelineDiagnostics,
)

_RESOURCE_SIGNAL_STATUSES = tuple(status.value for status in ResourceSignalStatus)
_ADAPTIVE_BACKPRESSURE_DECISIONS = (
    "disabled",
    "hold",
    "scale_up",
    "scale_down",
    "cooldown",
)
COMMIT_FAILURE_REASONS = ("kafka_exception",)
_PIPELINE_SUPPORT_STATES = tuple(
    state.value for state in PipelineDiagnosticsSupportState
)
_PIPELINE_ENGINE_TYPES = ("async", "process")
_PIPELINE_WORKER_CAPACITY_STATES = ("total", "executing", "admitted")


class _Joinable(Protocol):
    """Represent joinable data used by Prometheus metric export."""

    def join(self, timeout: float | None = None) -> None:
        """Handle join within Prometheus metric export."""
        ...


class PrometheusMetricsExporter:
    """Project runtime metrics into Prometheus collectors."""

    def __init__(
        self,
        config: Optional[MetricsConfig] = None,
        registry: Optional[CollectorRegistry] = None,
    ) -> None:
        self._config = config or MetricsConfig()
        self._registry = registry or CollectorRegistry()
        self._http_server = None
        self._http_thread: Optional[_Joinable] = None

        self._processed_total = Counter(
            "consumer_processed_total",
            "Number of completion events processed",
            labelnames=("topic", "partition", "status"),
            registry=self._registry,
        )
        self._commit_failures_total = Counter(
            "consumer_commit_failures_total",
            "Number of final offset commit failures",
            labelnames=("topic", "partition", "reason"),
            registry=self._registry,
        )
        self._dlq_publish_failures_total = Counter(
            "consumer_dlq_publish_failures_total",
            "Number of terminal DLQ publish failures",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._latency_hist = Histogram(
            "consumer_processing_latency_seconds",
            "End-to-end processing latency measured at completion",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._in_flight_gauge = Gauge(
            "consumer_in_flight_count",
            "Total in-flight messages",
            registry=self._registry,
        )
        self._lag_gauge = Gauge(
            "consumer_parallel_lag",
            "True lag per topic partition",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._gap_gauge = Gauge(
            "consumer_gap_count",
            "Number of outstanding gaps per partition",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._queued_gauge = Gauge(
            "consumer_internal_queue_depth",
            "Queued messages per partition",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._blocking_duration_gauge = Gauge(
            "consumer_oldest_task_duration_seconds",
            "Duration of oldest blocking offset",
            labelnames=("topic", "partition"),
            registry=self._registry,
        )
        self._backpressure_gauge = Gauge(
            "consumer_backpressure_active",
            "Backpressure status (1=paused,0=running)",
            registry=self._registry,
        )
        self._metadata_size_gauge = Gauge(
            "consumer_metadata_size_bytes",
            "Offset commit metadata payload size",
            labelnames=("topic",),
            registry=self._registry,
        )
        self._resource_signal_status_gauge = Gauge(
            "consumer_resource_signal_status",
            "Resource signal status as a one-hot fixed-cardinality gauge",
            labelnames=("status",),
            registry=self._registry,
        )
        self._resource_cpu_utilization_gauge = Gauge(
            "consumer_resource_cpu_utilization_ratio",
            "Latest host CPU utilization ratio from resource signals",
            registry=self._registry,
        )
        self._resource_memory_utilization_gauge = Gauge(
            "consumer_resource_memory_utilization_ratio",
            "Latest host memory utilization ratio from resource signals",
            registry=self._registry,
        )
        self._adaptive_backpressure_configured_max_in_flight_gauge = Gauge(
            "consumer_adaptive_backpressure_configured_max_in_flight",
            "Configured adaptive backpressure max_in_flight ceiling",
            registry=self._registry,
        )
        self._adaptive_backpressure_effective_max_in_flight_gauge = Gauge(
            "consumer_adaptive_backpressure_effective_max_in_flight",
            "Live adaptive backpressure max_in_flight limit",
            registry=self._registry,
        )
        self._adaptive_backpressure_min_in_flight_gauge = Gauge(
            "consumer_adaptive_backpressure_min_in_flight",
            "Minimum adaptive backpressure max_in_flight floor",
            registry=self._registry,
        )
        self._adaptive_backpressure_scale_up_step_gauge = Gauge(
            "consumer_adaptive_backpressure_scale_up_step",
            "Adaptive backpressure scale-up step",
            registry=self._registry,
        )
        self._adaptive_backpressure_scale_down_step_gauge = Gauge(
            "consumer_adaptive_backpressure_scale_down_step",
            "Adaptive backpressure scale-down step",
            registry=self._registry,
        )
        self._adaptive_backpressure_cooldown_ms_gauge = Gauge(
            "consumer_adaptive_backpressure_cooldown_ms",
            "Adaptive backpressure cooldown in milliseconds",
            registry=self._registry,
        )
        self._adaptive_backpressure_lag_scale_up_threshold_gauge = Gauge(
            "consumer_adaptive_backpressure_lag_scale_up_threshold",
            "Adaptive backpressure lag threshold that triggers scale-up",
            registry=self._registry,
        )
        self._adaptive_backpressure_low_latency_threshold_ms_gauge = Gauge(
            "consumer_adaptive_backpressure_low_latency_threshold_ms",
            "Adaptive backpressure low-latency threshold in milliseconds",
            registry=self._registry,
        )
        self._adaptive_backpressure_high_latency_threshold_ms_gauge = Gauge(
            "consumer_adaptive_backpressure_high_latency_threshold_ms",
            "Adaptive backpressure high-latency threshold in milliseconds",
            registry=self._registry,
        )
        self._adaptive_backpressure_avg_completion_latency_seconds_gauge = Gauge(
            "consumer_adaptive_backpressure_avg_completion_latency_seconds",
            "Current adaptive backpressure decision input: completion latency seconds",
            registry=self._registry,
        )
        self._adaptive_backpressure_last_decision_gauge = Gauge(
            "consumer_adaptive_backpressure_last_decision",
            "One-hot gauge of the latest adaptive backpressure decision",
            labelnames=("decision",),
            registry=self._registry,
        )
        self._adaptive_concurrency_configured_max_in_flight_gauge = Gauge(
            "consumer_adaptive_concurrency_configured_max_in_flight",
            "Configured adaptive concurrency max_in_flight ceiling",
            registry=self._registry,
        )
        self._adaptive_concurrency_effective_max_in_flight_gauge = Gauge(
            "consumer_adaptive_concurrency_effective_max_in_flight",
            "Live adaptive concurrency max_in_flight limit",
            registry=self._registry,
        )
        self._adaptive_concurrency_min_in_flight_gauge = Gauge(
            "consumer_adaptive_concurrency_min_in_flight",
            "Minimum adaptive concurrency max_in_flight floor",
            registry=self._registry,
        )
        self._adaptive_concurrency_scale_up_step_gauge = Gauge(
            "consumer_adaptive_concurrency_scale_up_step",
            "Adaptive concurrency scale-up step",
            registry=self._registry,
        )
        self._adaptive_concurrency_scale_down_step_gauge = Gauge(
            "consumer_adaptive_concurrency_scale_down_step",
            "Adaptive concurrency scale-down step",
            registry=self._registry,
        )
        self._adaptive_concurrency_cooldown_ms_gauge = Gauge(
            "consumer_adaptive_concurrency_cooldown_ms",
            "Adaptive concurrency cooldown in milliseconds",
            registry=self._registry,
        )
        self._process_batch_flush_count = Gauge(
            "consumer_process_batch_flush_count",
            "Cumulative process batch flush count by reason",
            labelnames=("reason",),
            registry=self._registry,
        )
        self._process_batch_avg_size_gauge = Gauge(
            "consumer_process_batch_avg_size",
            "Average process batch size across all flushes",
            registry=self._registry,
        )
        self._process_batch_last_size_gauge = Gauge(
            "consumer_process_batch_last_size",
            "Size of the most recent process batch flush",
            registry=self._registry,
        )
        self._process_batch_last_wait_seconds_gauge = Gauge(
            "consumer_process_batch_last_wait_seconds",
            "Wait time for the most recent process batch flush",
            registry=self._registry,
        )
        self._process_batch_buffered_items_gauge = Gauge(
            "consumer_process_batch_buffered_items",
            "Number of currently buffered process batch items",
            registry=self._registry,
        )
        self._process_batch_buffered_age_seconds_gauge = Gauge(
            "consumer_process_batch_buffered_age_seconds",
            "Age of the current process batch buffer",
            registry=self._registry,
        )
        self._process_batch_last_main_to_worker_ipc_seconds_gauge = Gauge(
            "consumer_process_batch_last_main_to_worker_ipc_seconds",
            "Last observed main-to-worker IPC time for process batches",
            registry=self._registry,
        )
        self._process_batch_avg_main_to_worker_ipc_seconds_gauge = Gauge(
            "consumer_process_batch_avg_main_to_worker_ipc_seconds",
            "Average observed main-to-worker IPC time for process batches",
            registry=self._registry,
        )
        self._process_batch_last_worker_exec_seconds_gauge = Gauge(
            "consumer_process_batch_last_worker_exec_seconds",
            "Last observed worker execution time for process batches",
            registry=self._registry,
        )
        self._process_batch_avg_worker_exec_seconds_gauge = Gauge(
            "consumer_process_batch_avg_worker_exec_seconds",
            "Average observed worker execution time for process batches",
            registry=self._registry,
        )
        self._process_batch_last_worker_to_main_ipc_seconds_gauge = Gauge(
            "consumer_process_batch_last_worker_to_main_ipc_seconds",
            "Last observed worker-to-main IPC time for process completions",
            registry=self._registry,
        )
        self._process_batch_avg_worker_to_main_ipc_seconds_gauge = Gauge(
            "consumer_process_batch_avg_worker_to_main_ipc_seconds",
            "Average observed worker-to-main IPC time for process completions",
            registry=self._registry,
        )
        self._process_batch_transport_mode_gauge = Gauge(
            "consumer_process_batch_transport_mode",
            "Active process transport mode for batch metrics",
            labelnames=("mode",),
            registry=self._registry,
        )
        self._process_batch_support_state_gauge = Gauge(
            "consumer_process_batch_support_state",
            "Support boundary state for the active process transport",
            labelnames=("state",),
            registry=self._registry,
        )
        self._process_batch_timer_flush_supported_gauge = Gauge(
            "consumer_process_batch_timer_flush_supported",
            "Whether timer-based process batch flushing is supported for the active transport",
            registry=self._registry,
        )
        self._process_batch_demand_flush_supported_gauge = Gauge(
            "consumer_process_batch_demand_flush_supported",
            "Whether demand-based process batch flushing is supported for the active transport",
            registry=self._registry,
        )
        self._process_batch_recycle_supported_gauge = Gauge(
            "consumer_process_batch_recycle_supported",
            "Whether recycle settings are supported for the active process transport",
            registry=self._registry,
        )
        self._pipeline_stage_messages_gauge = Gauge(
            "pyrallel_pipeline_stage_messages",
            "Pipeline diagnostic message counts by supported bounded stage",
            labelnames=("stage", "engine_type"),
            registry=self._registry,
        )
        self._pipeline_blocked_messages_gauge = Gauge(
            "pyrallel_pipeline_blocked_messages",
            "Pipeline diagnostic blocked message counts by supported bounded reason",
            labelnames=("reason", "engine_type"),
            registry=self._registry,
        )
        self._pipeline_dispatch_capacity_blocked_messages_gauge = Gauge(
            "pyrallel_pipeline_dispatch_capacity_blocked_messages",
            "Pipeline diagnostic dispatch-capacity blocked count by bounded reason",
            labelnames=("reason", "engine_type"),
            registry=self._registry,
        )
        self._pipeline_section_support_state_gauge = Gauge(
            "pyrallel_pipeline_section_support_state",
            "Pipeline diagnostics section support state as a bounded one-hot gauge",
            labelnames=("section", "state", "engine_type"),
            registry=self._registry,
        )
        self._pipeline_worker_capacity_units_gauge = Gauge(
            "pyrallel_pipeline_worker_capacity_units",
            "Pipeline diagnostic aggregate worker capacity units by bounded state",
            labelnames=("state", "engine_type"),
            registry=self._registry,
        )

        if self._config.enabled:
            server = start_http_server(self._config.port, registry=self._registry)
            if isinstance(server, tuple):
                self._http_server = server[0]
                thread = server[1]
                if hasattr(thread, "join"):
                    self._http_thread = cast(_Joinable, thread)
            elif server is not None:
                self._http_server = server

    def update_from_system_metrics(self, metrics: SystemMetrics) -> None:
        """Build update from system metrics."""
        self._in_flight_gauge.set(metrics.total_in_flight)
        self._backpressure_gauge.set(1 if metrics.is_paused else 0)
        for partition in metrics.partitions:
            labels = (partition.tp.topic, str(partition.tp.partition))
            self._lag_gauge.labels(*labels).set(partition.true_lag)
            self._gap_gauge.labels(*labels).set(partition.gap_count)
            self._queued_gauge.labels(*labels).set(partition.queued_count)
            duration = partition.blocking_duration_sec or 0.0
            self._blocking_duration_gauge.labels(*labels).set(duration)
        self._update_resource_signal(metrics.resource_signal)
        self._update_adaptive_snapshot_metrics(
            metrics.adaptive_backpressure,
            metrics.adaptive_concurrency,
        )
        self._update_process_batch_metrics(metrics.process_batch_metrics)

    def observe_completion(
        self, tp: TopicPartition, status: CompletionStatus, duration_seconds: float
    ) -> None:
        """Observe completion for Prometheus metric export."""
        self._processed_total.labels(
            topic=tp.topic, partition=str(tp.partition), status=status.value
        ).inc()
        self._latency_hist.labels(topic=tp.topic, partition=str(tp.partition)).observe(
            duration_seconds
        )

    def update_metadata_size(self, topic: str, size_bytes: int) -> None:
        """Update metadata size for Prometheus metric export."""
        self._metadata_size_gauge.labels(topic=topic).set(size_bytes)

    def update_pipeline_diagnostics(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        """Project supported pipeline diagnostics sidecar values into Prometheus."""
        if engine_type not in _PIPELINE_ENGINE_TYPES:
            allowed_engine_types = ", ".join(_PIPELINE_ENGINE_TYPES)
            raise ValueError(
                "Unknown pipeline engine_type: "
                f"{engine_type!r}; expected one of: {allowed_engine_types}"
            )
        self._update_pipeline_section_support(diagnostics, engine_type=engine_type)
        self._update_pipeline_stage_counts(diagnostics, engine_type=engine_type)
        self._update_pipeline_blocked_counts(diagnostics, engine_type=engine_type)
        self._update_pipeline_dispatch_capacity(diagnostics, engine_type=engine_type)
        self._update_pipeline_worker_capacity(diagnostics, engine_type=engine_type)

    def record_commit_failure(
        self, tp: TopicPartition, reason: str = "kafka_exception"
    ) -> None:
        """Record commit failure for Prometheus metric export."""
        if reason not in COMMIT_FAILURE_REASONS:
            allowed_reasons = ", ".join(COMMIT_FAILURE_REASONS)
            raise ValueError(
                "Unknown commit failure reason: "
                f"{reason!r}; expected one of: {allowed_reasons}"
            )
        self._commit_failures_total.labels(
            topic=tp.topic,
            partition=str(tp.partition),
            reason=reason,
        ).inc()

    def record_dlq_publish_failure(self, tp: TopicPartition) -> None:
        """Record dlq publish failure for Prometheus metric export."""
        self._dlq_publish_failures_total.labels(
            topic=tp.topic,
            partition=str(tp.partition),
        ).inc()

    def close(self) -> None:
        """Release resources held by this component."""
        if self._http_server is None:
            return

        shutdown = getattr(self._http_server, "shutdown", None)
        if callable(shutdown):
            shutdown()

        server_close = getattr(self._http_server, "server_close", None)
        if callable(server_close):
            server_close()

        if self._http_thread is not None:
            self._http_thread.join(timeout=1.0)

        self._http_server = None
        self._http_thread = None

    def _update_resource_signal(self, signal: Optional[ResourceSignalSnapshot]) -> None:
        """Update resource signal for Prometheus metric export."""
        signal_status = (
            signal.status.value
            if signal is not None
            else ResourceSignalStatus.UNAVAILABLE.value
        )
        for status in _RESOURCE_SIGNAL_STATUSES:
            self._resource_signal_status_gauge.labels(status=status).set(
                1 if status == signal_status else 0
            )
        self._resource_cpu_utilization_gauge.set(
            signal.cpu_utilization
            if signal is not None and signal.cpu_utilization is not None
            else 0
        )
        self._resource_memory_utilization_gauge.set(
            signal.memory_utilization
            if signal is not None and signal.memory_utilization is not None
            else 0
        )

    def _update_adaptive_snapshot_metrics(
        self,
        adaptive_backpressure: Optional[AdaptiveBackpressureSnapshot],
        adaptive_concurrency: Optional[AdaptiveConcurrencyRuntimeSnapshot],
    ) -> None:
        """Update adaptive snapshot metrics for Prometheus metric export."""
        if adaptive_backpressure is None:
            self._adaptive_backpressure_configured_max_in_flight_gauge.set(0)
            self._adaptive_backpressure_effective_max_in_flight_gauge.set(0)
            self._adaptive_backpressure_min_in_flight_gauge.set(0)
            self._adaptive_backpressure_scale_up_step_gauge.set(0)
            self._adaptive_backpressure_scale_down_step_gauge.set(0)
            self._adaptive_backpressure_cooldown_ms_gauge.set(0)
            self._adaptive_backpressure_lag_scale_up_threshold_gauge.set(0)
            self._adaptive_backpressure_low_latency_threshold_ms_gauge.set(0)
            self._adaptive_backpressure_high_latency_threshold_ms_gauge.set(0)
            self._adaptive_backpressure_avg_completion_latency_seconds_gauge.set(0)
            last_decision = "disabled"
        else:
            self._adaptive_backpressure_configured_max_in_flight_gauge.set(
                adaptive_backpressure.configured_max_in_flight
            )
            self._adaptive_backpressure_effective_max_in_flight_gauge.set(
                adaptive_backpressure.effective_max_in_flight
            )
            self._adaptive_backpressure_min_in_flight_gauge.set(
                adaptive_backpressure.min_in_flight
            )
            self._adaptive_backpressure_scale_up_step_gauge.set(
                adaptive_backpressure.scale_up_step
            )
            self._adaptive_backpressure_scale_down_step_gauge.set(
                adaptive_backpressure.scale_down_step
            )
            self._adaptive_backpressure_cooldown_ms_gauge.set(
                adaptive_backpressure.cooldown_ms
            )
            self._adaptive_backpressure_lag_scale_up_threshold_gauge.set(
                adaptive_backpressure.lag_scale_up_threshold
            )
            self._adaptive_backpressure_low_latency_threshold_ms_gauge.set(
                adaptive_backpressure.low_latency_threshold_ms
            )
            self._adaptive_backpressure_high_latency_threshold_ms_gauge.set(
                adaptive_backpressure.high_latency_threshold_ms
            )
            self._adaptive_backpressure_avg_completion_latency_seconds_gauge.set(
                adaptive_backpressure.avg_completion_latency_seconds or 0
            )
            last_decision = adaptive_backpressure.last_decision

        for decision in _ADAPTIVE_BACKPRESSURE_DECISIONS:
            self._adaptive_backpressure_last_decision_gauge.labels(
                decision=decision
            ).set(1 if decision == last_decision else 0)

        if adaptive_concurrency is None:
            self._adaptive_concurrency_configured_max_in_flight_gauge.set(0)
            self._adaptive_concurrency_effective_max_in_flight_gauge.set(0)
            self._adaptive_concurrency_min_in_flight_gauge.set(0)
            self._adaptive_concurrency_scale_up_step_gauge.set(0)
            self._adaptive_concurrency_scale_down_step_gauge.set(0)
            self._adaptive_concurrency_cooldown_ms_gauge.set(0)
        else:
            self._adaptive_concurrency_configured_max_in_flight_gauge.set(
                adaptive_concurrency.configured_max_in_flight
            )
            self._adaptive_concurrency_effective_max_in_flight_gauge.set(
                adaptive_concurrency.effective_max_in_flight
            )
            self._adaptive_concurrency_min_in_flight_gauge.set(
                adaptive_concurrency.min_in_flight
            )
            self._adaptive_concurrency_scale_up_step_gauge.set(
                adaptive_concurrency.scale_up_step
            )
            self._adaptive_concurrency_scale_down_step_gauge.set(
                adaptive_concurrency.scale_down_step
            )
            self._adaptive_concurrency_cooldown_ms_gauge.set(
                adaptive_concurrency.cooldown_ms
            )

    def _update_process_batch_metrics(
        self, metrics: Optional[ProcessBatchMetrics]
    ) -> None:
        """Update process batch metrics for Prometheus metric export."""
        if metrics is None:
            for reason in ("size", "timer", "close", "demand"):
                self._process_batch_flush_count.labels(reason=reason).set(0)
            for mode in ("worker_pipes",):
                self._process_batch_transport_mode_gauge.labels(mode=mode).set(0)
            for state in ("full", "bounded"):
                self._process_batch_support_state_gauge.labels(state=state).set(0)
            self._process_batch_avg_size_gauge.set(0)
            self._process_batch_last_size_gauge.set(0)
            self._process_batch_last_wait_seconds_gauge.set(0)
            self._process_batch_buffered_items_gauge.set(0)
            self._process_batch_buffered_age_seconds_gauge.set(0)
            self._process_batch_last_main_to_worker_ipc_seconds_gauge.set(0)
            self._process_batch_avg_main_to_worker_ipc_seconds_gauge.set(0)
            self._process_batch_last_worker_exec_seconds_gauge.set(0)
            self._process_batch_avg_worker_exec_seconds_gauge.set(0)
            self._process_batch_last_worker_to_main_ipc_seconds_gauge.set(0)
            self._process_batch_avg_worker_to_main_ipc_seconds_gauge.set(0)
            self._process_batch_timer_flush_supported_gauge.set(0)
            self._process_batch_demand_flush_supported_gauge.set(0)
            self._process_batch_recycle_supported_gauge.set(0)
            return

        self._process_batch_flush_count.labels(reason="size").set(
            metrics.size_flush_count
        )
        self._process_batch_flush_count.labels(reason="timer").set(
            metrics.timer_flush_count
        )
        self._process_batch_flush_count.labels(reason="close").set(
            metrics.close_flush_count
        )
        self._process_batch_flush_count.labels(reason="demand").set(
            metrics.demand_flush_count
        )
        flush_total = (
            metrics.size_flush_count
            + metrics.timer_flush_count
            + metrics.close_flush_count
            + metrics.demand_flush_count
        )
        average_batch_size = (
            metrics.total_flushed_items / flush_total if flush_total > 0 else 0.0
        )
        self._process_batch_avg_size_gauge.set(average_batch_size)
        self._process_batch_last_size_gauge.set(metrics.last_flush_size)
        self._process_batch_last_wait_seconds_gauge.set(metrics.last_flush_wait_seconds)
        self._process_batch_buffered_items_gauge.set(metrics.buffered_items)
        self._process_batch_buffered_age_seconds_gauge.set(metrics.buffered_age_seconds)
        self._process_batch_last_main_to_worker_ipc_seconds_gauge.set(
            metrics.last_main_to_worker_ipc_seconds
        )
        self._process_batch_avg_main_to_worker_ipc_seconds_gauge.set(
            metrics.avg_main_to_worker_ipc_seconds
        )
        self._process_batch_last_worker_exec_seconds_gauge.set(
            metrics.last_worker_exec_seconds
        )
        self._process_batch_avg_worker_exec_seconds_gauge.set(
            metrics.avg_worker_exec_seconds
        )
        self._process_batch_last_worker_to_main_ipc_seconds_gauge.set(
            metrics.last_worker_to_main_ipc_seconds
        )
        self._process_batch_avg_worker_to_main_ipc_seconds_gauge.set(
            metrics.avg_worker_to_main_ipc_seconds
        )
        for mode in ("worker_pipes",):
            self._process_batch_transport_mode_gauge.labels(mode=mode).set(
                1 if metrics.transport_mode == mode else 0
            )
        for state in ("full", "bounded"):
            self._process_batch_support_state_gauge.labels(state=state).set(
                1 if metrics.support_state == state else 0
            )
        self._process_batch_timer_flush_supported_gauge.set(
            1 if metrics.timer_flush_supported else 0
        )
        self._process_batch_demand_flush_supported_gauge.set(
            1 if metrics.demand_flush_supported else 0
        )
        self._process_batch_recycle_supported_gauge.set(
            1 if metrics.recycle_supported else 0
        )

    def _update_pipeline_section_support(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        for section in PipelineDiagnosticsSection:
            support_state = diagnostics.section_support.get(
                section, PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
            )
            for state in _PIPELINE_SUPPORT_STATES:
                self._pipeline_section_support_state_gauge.labels(
                    section=section.value,
                    state=state,
                    engine_type=engine_type,
                ).set(1 if support_state.value == state else 0)

    def _update_pipeline_stage_counts(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        section_supported = (
            diagnostics.section_support.get(PipelineDiagnosticsSection.STAGES)
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        for stage in PipelineStage:
            labels = {"stage": stage.value, "engine_type": engine_type}
            stage_supported = (
                diagnostics.stage_support.get(stage)
                == PipelineDiagnosticsSupportState.SUPPORTED
            )
            if section_supported and stage_supported:
                self._pipeline_stage_messages_gauge.labels(**labels).set(
                    diagnostics.stage_counts.get(stage, PipelineCount(count=0)).count
                )
            else:
                self._remove_labeled_metric(self._pipeline_stage_messages_gauge, labels)

    def _update_pipeline_blocked_counts(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        section_supported = (
            diagnostics.section_support.get(PipelineDiagnosticsSection.BLOCKED)
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        for reason in PipelineBlockedReason:
            labels = {"reason": reason.value, "engine_type": engine_type}
            if section_supported:
                self._pipeline_blocked_messages_gauge.labels(**labels).set(
                    diagnostics.blocked_counts.get(reason, PipelineCount(count=0)).count
                )
            else:
                self._remove_labeled_metric(
                    self._pipeline_blocked_messages_gauge, labels
                )

    def _update_pipeline_dispatch_capacity(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        section_supported = (
            diagnostics.section_support.get(
                PipelineDiagnosticsSection.DISPATCH_CAPACITY
            )
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        active_reason = diagnostics.dispatch_capacity.reason
        for reason in PipelineDispatchCapacityReason:
            labels = {"reason": reason.value, "engine_type": engine_type}
            if section_supported and active_reason == reason:
                self._pipeline_dispatch_capacity_blocked_messages_gauge.labels(
                    **labels
                ).set(diagnostics.dispatch_capacity.blocked_items)
            else:
                self._remove_labeled_metric(
                    self._pipeline_dispatch_capacity_blocked_messages_gauge, labels
                )

    def _update_pipeline_worker_capacity(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        *,
        engine_type: str,
    ) -> None:
        section_supported = (
            diagnostics.section_support.get(PipelineDiagnosticsSection.WORKERS)
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        worker_supported = (
            diagnostics.workers.support_state
            == PipelineDiagnosticsSupportState.SUPPORTED
        )
        values = {
            "total": diagnostics.workers.total,
            "executing": diagnostics.workers.executing,
            "admitted": diagnostics.workers.admitted,
        }
        for state in _PIPELINE_WORKER_CAPACITY_STATES:
            labels = {"state": state, "engine_type": engine_type}
            value = values[state]
            if section_supported and worker_supported and value is not None:
                self._pipeline_worker_capacity_units_gauge.labels(**labels).set(value)
            else:
                self._remove_labeled_metric(
                    self._pipeline_worker_capacity_units_gauge, labels
                )

    @staticmethod
    def _remove_labeled_metric(metric: Gauge, labels: dict[str, str]) -> None:
        try:
            metric.remove_by_labels(labels)
        except KeyError:
            return
