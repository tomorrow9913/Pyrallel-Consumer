from __future__ import annotations

from typing import Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    start_http_server,
)

from pyrallel_consumer.config import MetricsConfig
from pyrallel_consumer.dto import CompletionStatus, SystemMetrics, TopicPartition


class PrometheusMetricsExporter:
    def __init__(self, config: Optional[MetricsConfig] = None) -> None:
        self._config = config or MetricsConfig()
        self._registry = CollectorRegistry()

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

        if self._config.enabled:
            start_http_server(self._config.port, registry=self._registry)

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
