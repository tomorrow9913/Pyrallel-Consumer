import pytest

pytest.importorskip("prometheus_client")
from prometheus_client import CollectorRegistry  # noqa: E402

from pyrallel_consumer.config import MetricsConfig  # noqa: E402
from pyrallel_consumer.dto import (  # noqa: E402
    CompletionStatus,
    PartitionMetrics,
    ProcessBatchMetrics,
    SystemMetrics,
    TopicPartition,
)
from pyrallel_consumer.metrics_exporter import PrometheusMetricsExporter  # noqa: E402


def _make_partition_metrics(topic: str, partition: int) -> PartitionMetrics:
    return PartitionMetrics(
        tp=TopicPartition(topic=topic, partition=partition),
        true_lag=3,
        gap_count=2,
        blocking_offset=10,
        blocking_duration_sec=1.5,
        queued_count=7,
    )


def test_exporter_uses_provided_registry_and_no_http_when_disabled(monkeypatch):
    registry = CollectorRegistry()
    monkeypatch.setattr(
        "pyrallel_consumer.metrics_exporter.start_http_server",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("should not start")),
    )

    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=False, port=9100), registry=registry
    )

    assert exporter._registry is registry


def test_exporter_updates_metrics_and_observes_completion():
    registry = CollectorRegistry()
    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=False), registry=registry
    )

    metrics = SystemMetrics(
        total_in_flight=5,
        is_paused=True,
        partitions=[
            _make_partition_metrics("topic-a", 0),
            _make_partition_metrics("topic-b", 1),
        ],
        process_batch_metrics=ProcessBatchMetrics(
            size_flush_count=3,
            timer_flush_count=2,
            close_flush_count=1,
            total_flushed_items=12,
            last_flush_size=4,
            last_flush_wait_seconds=0.05,
            buffered_items=1,
            buffered_age_seconds=0.2,
        ),
    )

    exporter.update_from_system_metrics(metrics)

    assert exporter._in_flight_gauge._value.get() == 5
    assert exporter._backpressure_gauge._value.get() == 1

    lag = exporter._lag_gauge.labels("topic-a", "0")._value.get()
    gaps = exporter._gap_gauge.labels("topic-b", "1")._value.get()
    queued = exporter._queued_gauge.labels("topic-a", "0")._value.get()
    blocking = exporter._blocking_duration_gauge.labels("topic-b", "1")._value.get()

    assert lag == 3
    assert gaps == 2
    assert queued == 7
    assert blocking == 1.5
    assert exporter._process_batch_flush_count.labels(reason="size")._value.get() == 3
    assert exporter._process_batch_flush_count.labels(reason="timer")._value.get() == 2
    assert exporter._process_batch_last_size_gauge._value.get() == 4
    assert exporter._process_batch_avg_size_gauge._value.get() == 2
    assert exporter._process_batch_buffered_items_gauge._value.get() == 1
    assert exporter._process_batch_buffered_age_seconds_gauge._value.get() == 0.2

    tp = TopicPartition(topic="topic-a", partition=0)
    exporter.observe_completion(tp, CompletionStatus.SUCCESS, duration_seconds=0.12)
    exporter.update_metadata_size(topic="topic-a", size_bytes=42)

    processed = exporter._processed_total.labels(
        topic="topic-a", partition="0", status="success"
    )._value.get()
    latency_sum = exporter._latency_hist.labels(
        topic="topic-a", partition="0"
    )._sum.get()
    metadata_size = exporter._metadata_size_gauge.labels(topic="topic-a")._value.get()

    assert processed == 1
    assert pytest.approx(latency_sum, rel=1e-6) == 0.12
    assert metadata_size == 42


def test_exporter_closes_http_server_when_enabled(monkeypatch):
    registry = CollectorRegistry()
    closed = {"shutdown": 0, "server_close": 0, "join": 0}

    class _DummyServer:
        def shutdown(self) -> None:
            closed["shutdown"] += 1

        def server_close(self) -> None:
            closed["server_close"] += 1

    class _DummyThread:
        def join(self, timeout=None) -> None:  # noqa: ANN001
            closed["join"] += 1

    monkeypatch.setattr(
        "pyrallel_consumer.metrics_exporter.start_http_server",
        lambda *a, **k: (_DummyServer(), _DummyThread()),
    )

    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=True, port=9100), registry=registry
    )

    exporter.close()
    exporter.close()

    assert closed == {"shutdown": 1, "server_close": 1, "join": 1}
