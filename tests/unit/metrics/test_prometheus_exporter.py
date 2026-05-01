import pytest

pytest.importorskip("prometheus_client")
from prometheus_client import CollectorRegistry  # noqa: E402

from pyrallel_consumer.config import MetricsConfig  # noqa: E402
from pyrallel_consumer.dto import (  # noqa: E402
    AdaptiveBackpressureSnapshot,
    AdaptiveConcurrencyRuntimeSnapshot,
    CompletionStatus,
    PartitionMetrics,
    ProcessBatchMetrics,
    ResourceSignalSnapshot,
    ResourceSignalStatus,
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
        resource_signal=ResourceSignalSnapshot(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.62,
            memory_utilization=0.71,
        ),
        process_batch_metrics=ProcessBatchMetrics(
            size_flush_count=3,
            timer_flush_count=2,
            close_flush_count=1,
            total_flushed_items=12,
            last_flush_size=4,
            last_flush_wait_seconds=0.05,
            buffered_items=1,
            buffered_age_seconds=0.2,
            last_main_to_worker_ipc_seconds=0.003,
            avg_main_to_worker_ipc_seconds=0.002,
            last_worker_exec_seconds=0.015,
            avg_worker_exec_seconds=0.012,
            last_worker_to_main_ipc_seconds=0.004,
            avg_worker_to_main_ipc_seconds=0.003,
            transport_mode="worker_pipes",
            support_state="bounded",
            timer_flush_supported=False,
            demand_flush_supported=False,
            recycle_supported=False,
        ),
        adaptive_backpressure=AdaptiveBackpressureSnapshot(
            configured_max_in_flight=128,
            effective_max_in_flight=96,
            min_in_flight=32,
            scale_up_step=16,
            scale_down_step=16,
            cooldown_ms=1000,
            lag_scale_up_threshold=1000,
            low_latency_threshold_ms=25.5,
            high_latency_threshold_ms=125.0,
            last_decision="scale_down",
            avg_completion_latency_seconds=0.42,
        ),
        adaptive_concurrency=AdaptiveConcurrencyRuntimeSnapshot(
            configured_max_in_flight=100,
            effective_max_in_flight=80,
            min_in_flight=24,
            scale_up_step=8,
            scale_down_step=16,
            cooldown_ms=500,
        ),
    )

    exporter.update_from_system_metrics(metrics)

    assert exporter._in_flight_gauge._value.get() == 5
    assert exporter._backpressure_gauge._value.get() == 1
    assert (
        exporter._resource_signal_status_gauge.labels(status="available")._value.get()
        == 1
    )
    assert (
        exporter._resource_signal_status_gauge.labels(status="unavailable")._value.get()
        == 0
    )
    assert exporter._resource_cpu_utilization_gauge._value.get() == 0.62
    assert exporter._resource_memory_utilization_gauge._value.get() == 0.71

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
    assert (
        exporter._process_batch_last_main_to_worker_ipc_seconds_gauge._value.get()
        == 0.003
    )
    assert (
        exporter._process_batch_avg_main_to_worker_ipc_seconds_gauge._value.get()
        == 0.002
    )
    assert exporter._process_batch_last_worker_exec_seconds_gauge._value.get() == 0.015
    assert exporter._process_batch_avg_worker_exec_seconds_gauge._value.get() == 0.012
    assert (
        exporter._process_batch_last_worker_to_main_ipc_seconds_gauge._value.get()
        == 0.004
    )
    assert (
        exporter._process_batch_avg_worker_to_main_ipc_seconds_gauge._value.get()
        == 0.003
    )
    assert (
        exporter._process_batch_transport_mode_gauge.labels(
            mode="worker_pipes"
        )._value.get()
        == 1
    )
    assert (
        exporter._process_batch_transport_mode_gauge.labels(
            mode="shared_queue"
        )._value.get()
        == 0
    )
    assert (
        exporter._process_batch_support_state_gauge.labels(state="bounded")._value.get()
        == 1
    )
    assert exporter._process_batch_timer_flush_supported_gauge._value.get() == 0
    assert exporter._process_batch_demand_flush_supported_gauge._value.get() == 0
    assert exporter._process_batch_recycle_supported_gauge._value.get() == 0
    assert (
        exporter._adaptive_backpressure_configured_max_in_flight_gauge._value.get()
        == 128
    )
    assert (
        exporter._adaptive_backpressure_effective_max_in_flight_gauge._value.get() == 96
    )
    assert exporter._adaptive_backpressure_min_in_flight_gauge._value.get() == 32
    assert exporter._adaptive_backpressure_scale_up_step_gauge._value.get() == 16
    assert exporter._adaptive_backpressure_scale_down_step_gauge._value.get() == 16
    assert exporter._adaptive_backpressure_cooldown_ms_gauge._value.get() == 1000
    assert (
        exporter._adaptive_backpressure_lag_scale_up_threshold_gauge._value.get()
        == 1000
    )
    assert (
        exporter._adaptive_backpressure_low_latency_threshold_ms_gauge._value.get()
        == 25.5
    )
    assert (
        exporter._adaptive_backpressure_high_latency_threshold_ms_gauge._value.get()
        == 125.0
    )
    assert (
        exporter._adaptive_backpressure_avg_completion_latency_seconds_gauge._value.get()
        == 0.42
    )
    assert (
        exporter._adaptive_backpressure_last_decision_gauge.labels(
            decision="scale_down"
        )._value.get()
        == 1
    )
    assert (
        exporter._adaptive_backpressure_last_decision_gauge.labels(
            decision="scale_up"
        )._value.get()
        == 0
    )
    assert (
        exporter._adaptive_concurrency_configured_max_in_flight_gauge._value.get()
        == 100
    )
    assert (
        exporter._adaptive_concurrency_effective_max_in_flight_gauge._value.get() == 80
    )
    assert exporter._adaptive_concurrency_min_in_flight_gauge._value.get() == 24
    assert exporter._adaptive_concurrency_scale_up_step_gauge._value.get() == 8
    assert exporter._adaptive_concurrency_scale_down_step_gauge._value.get() == 16
    assert exporter._adaptive_concurrency_cooldown_ms_gauge._value.get() == 500

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


def test_exporter_treats_missing_resource_signal_as_fail_open_unavailable() -> None:
    registry = CollectorRegistry()
    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=False), registry=registry
    )

    exporter.update_from_system_metrics(
        SystemMetrics(total_in_flight=0, is_paused=False, partitions=[])
    )

    assert (
        exporter._resource_signal_status_gauge.labels(status="unavailable")._value.get()
        == 1
    )
    assert exporter._resource_cpu_utilization_gauge._value.get() == 0
    assert exporter._resource_memory_utilization_gauge._value.get() == 0
    assert (
        exporter._adaptive_backpressure_configured_max_in_flight_gauge._value.get() == 0
    )
    assert (
        exporter._adaptive_backpressure_last_decision_gauge.labels(
            decision="disabled"
        )._value.get()
        == 1
    )
    assert (
        exporter._adaptive_concurrency_effective_max_in_flight_gauge._value.get() == 0
    )
    assert (
        exporter._process_batch_transport_mode_gauge.labels(
            mode="shared_queue"
        )._value.get()
        == 0
    )
    assert (
        exporter._process_batch_support_state_gauge.labels(state="bounded")._value.get()
        == 0
    )
    assert exporter._process_batch_timer_flush_supported_gauge._value.get() == 0
    assert exporter._process_batch_demand_flush_supported_gauge._value.get() == 0
    assert exporter._process_batch_recycle_supported_gauge._value.get() == 0


def test_exporter_registers_and_increments_failure_counters() -> None:
    registry = CollectorRegistry()
    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=False), registry=registry
    )
    tp = TopicPartition(topic="topic-a", partition=0)

    exporter.record_commit_failure(tp, reason="kafka_exception")
    exporter.record_commit_failure(tp, reason="kafka_exception")
    exporter.record_dlq_publish_failure(tp)

    commit_failure = exporter._commit_failures_total.labels(
        topic="topic-a", partition="0", reason="kafka_exception"
    )._value.get()
    dlq_failure = exporter._dlq_publish_failures_total.labels(
        topic="topic-a", partition="0"
    )._value.get()

    assert commit_failure == 2
    assert dlq_failure == 1


def test_exporter_rejects_unknown_commit_failure_reason() -> None:
    registry = CollectorRegistry()
    exporter = PrometheusMetricsExporter(
        MetricsConfig(enabled=False), registry=registry
    )

    with pytest.raises(ValueError, match="Unknown commit failure reason"):
        exporter.record_commit_failure(
            TopicPartition(topic="topic-a", partition=0), reason="exception text"
        )


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
