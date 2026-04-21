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
    CompletionStatus,
    ProcessBatchMetrics,
    ResourceSignalSnapshot,
    ResourceSignalStatus,
    SystemMetrics,
    TopicPartition,
)

_RESOURCE_SIGNAL_STATUSES = tuple(status.value for status in ResourceSignalStatus)


class _Joinable(Protocol):
    def join(self, timeout: float | None = None) -> None:
        ...


class PrometheusMetricsExporter:
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
        self._update_process_batch_metrics(metrics.process_batch_metrics)

    def observe_completion(
        self, tp: TopicPartition, status: CompletionStatus, duration_seconds: float
    ) -> None:
        self._processed_total.labels(
            topic=tp.topic, partition=str(tp.partition), status=status.value
        ).inc()
        self._latency_hist.labels(topic=tp.topic, partition=str(tp.partition)).observe(
            duration_seconds
        )

    def update_metadata_size(self, topic: str, size_bytes: int) -> None:
        self._metadata_size_gauge.labels(topic=topic).set(size_bytes)

    def close(self) -> None:
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

    def _update_process_batch_metrics(
        self, metrics: Optional[ProcessBatchMetrics]
    ) -> None:
        if metrics is None:
            for reason in ("size", "timer", "close", "demand"):
                self._process_batch_flush_count.labels(reason=reason).set(0)
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
