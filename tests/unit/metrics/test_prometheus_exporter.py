import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from pyrallel_consumer.config import MetricsConfig  # noqa: E402
from pyrallel_consumer.dto import (  # noqa: E402
    CompletionStatus,
    PartitionMetrics,
    SystemMetrics,
    TopicPartition,
)
from pyrallel_consumer.metrics_exporter import PrometheusMetricsExporter  # noqa: E402


def _make_metrics(paused: bool = True) -> SystemMetrics:
    partition = PartitionMetrics(
        tp=TopicPartition(topic="bench", partition=1),
        true_lag=5,
        gap_count=2,
        blocking_offset=10,
        blocking_duration_sec=1.5,
        queued_count=7,
    )
    return SystemMetrics(total_in_flight=3, is_paused=paused, partitions=[partition])


def test_update_from_system_metrics_sets_gauges():
    exporter = PrometheusMetricsExporter(MetricsConfig(enabled=False, port=0))
    metrics = _make_metrics()
    exporter.update_from_system_metrics(metrics)

    labels = ("bench", "1")
    assert exporter._lag_gauge.labels(*labels)._value.get() == 5
    assert exporter._gap_gauge.labels(*labels)._value.get() == 2
    assert exporter._queued_gauge.labels(*labels)._value.get() == 7
    assert exporter._blocking_duration_gauge.labels(*labels)._value.get() == 1.5
    assert exporter._backpressure_gauge._value.get() == 1
    assert exporter._in_flight_gauge._value.get() == 3


def test_observe_completion_updates_counters_and_histogram():
    exporter = PrometheusMetricsExporter(MetricsConfig(enabled=False, port=0))
    tp = TopicPartition(topic="bench", partition=2)
    exporter.observe_completion(tp, CompletionStatus.SUCCESS, 0.25)

    counter_sample = exporter._processed_total.labels(
        topic="bench", partition="2", status=CompletionStatus.SUCCESS.value
    )._value.get()
    assert counter_sample == 1

    histogram_sum = exporter._latency_hist.labels(
        topic="bench", partition="2"
    )._sum.get()
    assert histogram_sum == 0.25


def test_update_metadata_size_sets_gauge():
    exporter = PrometheusMetricsExporter(MetricsConfig(enabled=False, port=0))
    exporter.update_metadata_size("bench", 1234)
    assert exporter._metadata_size_gauge.labels(topic="bench")._value.get() == 1234
