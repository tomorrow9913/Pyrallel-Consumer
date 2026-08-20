# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_benchmark_runtime_config_metrics.py
# Role: Verifies benchmark config construction, metrics publishing, exporter reuse, and runtime wiring.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._benchmark_runtime_support import (
    Any,
    BaseExecutionEngine,
    BenchmarkResult,
    BenchmarkStats,
    BrokerPoller,
    CompletionStatus,
    ExecutionMode,
    PrometheusMetricsExporter,
    SimpleNamespace,
    TopicPartition,
    _build_args,
    asyncio,
    cast,
    pyrallel_consumer_test,
    pytest,
    run_parallel_benchmark,
    socket,
)


def test_benchmark_metrics_observer_records_success_and_stops_at_target() -> None:
    # Given: Inputs and test doubles are prepared for benchmark metrics observer records success and stops at target.
    completion_event = asyncio.Event()
    stats = BenchmarkStats(
        run_name="demo",
        run_type="async",
        workload="sleep",
        topic="demo-topic",
        ordering="key_hash",
        target_messages=1,
    )
    consumption_stats = pyrallel_consumer_test.ConsumptionStats(target=1)
    completions: list[tuple[TopicPartition, CompletionStatus, float]] = []

    class _FakePrometheusExporter:
        def observe_completion(self, tp, status, duration_seconds: float) -> None:
            completions.append((tp, status, duration_seconds))

    observer = pyrallel_consumer_test.BenchmarkMetricsObserver(
        benchmark_stats=stats,
        cons_stats=consumption_stats,
        completion_event=completion_event,
        prometheus_metrics_exporter=cast(Any, _FakePrometheusExporter()),
    )
    tp = TopicPartition(topic="demo-topic", partition=0)

    # When: The benchmark runtime configuration and metrics path is exercised for benchmark metrics observer records success and stops at target.
    observer.observe_completion(tp, CompletionStatus.SUCCESS, 0.01)

    # Then: The expected benchmark metrics observer records success and stops at target behavior is asserted.
    assert completions == [(tp, CompletionStatus.SUCCESS, 0.01)]
    assert consumption_stats.processed == 1
    assert stats.processed == 1
    assert completion_event.is_set() is True
    assert observer.failure_error is None


def test_benchmark_metrics_observer_reports_completion_failure() -> None:
    # Given: Inputs and test doubles are prepared for benchmark metrics observer reports completion failure.
    completion_event = asyncio.Event()
    observer = pyrallel_consumer_test.BenchmarkMetricsObserver(
        benchmark_stats=None,
        cons_stats=pyrallel_consumer_test.ConsumptionStats(target=1),
        completion_event=completion_event,
    )

    # When: The benchmark runtime configuration and metrics path is exercised for benchmark metrics observer reports completion failure.
    observer.observe_completion(
        TopicPartition(topic="demo-topic", partition=0),
        CompletionStatus.FAILURE,
        0.01,
    )

    # Then: The expected benchmark metrics observer reports completion failure behavior is asserted.
    assert observer.failure_error == (
        "Benchmark worker failure on demo-topic[0]: completion failed"
    )
    assert completion_event.is_set() is True


def test_record_release_gate_metrics_from_snapshot_sums_partition_metrics() -> None:
    # Given: Inputs and test doubles are prepared for record release gate metrics from snapshot sums partition metrics.
    stats = BenchmarkStats(
        run_name="demo",
        run_type="async",
        workload="sleep",
        topic="demo-topic",
        ordering="key_hash",
    )
    metrics = SimpleNamespace(
        partitions=[
            SimpleNamespace(true_lag=3, gap_count=1),
            SimpleNamespace(true_lag=5, gap_count=2),
        ]
    )

    # When: The benchmark runtime configuration and metrics path is exercised for record release gate metrics from snapshot sums partition metrics.
    pyrallel_consumer_test._record_release_gate_metrics_from_snapshot(
        stats,
        cast(Any, metrics),
        elapsed_sec=1.25,
    )

    # Then: The expected record release gate metrics from snapshot sums partition metrics behavior is asserted.
    assert stats._release_gate_observations == [
        {
            "elapsed_sec": 1.25,
            "consumer_parallel_lag": 8,
            "consumer_gap_count": 3,
        }
    ]


def test_build_kafka_config_sets_strict_completion_monitor_flag() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config sets strict completion monitor flag.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config sets strict completion monitor flag.
    config = pyrallel_consumer_test.build_kafka_config(
        strict_completion_monitor_enabled=False
    )

    # Then: The expected build kafka config sets strict completion monitor flag behavior is asserted.
    assert config.parallel_consumer.strict_completion_monitor_enabled is False


def test_build_kafka_config_sets_process_batching_overrides() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config sets process batching overrides.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config sets process batching overrides.
    config = pyrallel_consumer_test.build_kafka_config(
        process_count=2,
        process_batch_size=1,
        process_max_batch_wait_ms=0,
        process_flush_policy="demand_min_residence",
        process_demand_flush_min_residence_ms=2,
    )

    # Then: The expected build kafka config sets process batching overrides behavior is asserted.
    assert config.parallel_consumer.execution.process_config.process_count == 2
    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0
    assert (
        config.parallel_consumer.execution.process_config.flush_policy
        == "demand_min_residence"
    )
    assert (
        config.parallel_consumer.execution.process_config.demand_flush_min_residence_ms
        == 2
    )


def test_build_kafka_config_sets_route_batch_size_without_changing_process_batching() -> (
    None
):
    # Given: Inputs and test doubles are prepared for build kafka config sets route batch size without changing process batching.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config sets route batch size without changing process batching.
    config = pyrallel_consumer_test.build_kafka_config(
        process_batch_size=1,
        route_batch_size=64,
    )

    # Then: The expected build kafka config sets route batch size without changing process batching behavior is asserted.
    assert not hasattr(config.parallel_consumer.execution, "route_batch_size")
    assert config.parallel_consumer.execution.process_config.route_batch_size == 64
    assert config.parallel_consumer.execution.process_config.batch_size == 1


def test_build_kafka_config_defaults_route_batch_size_to_worker_pipes_profile() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config defaults route batch size to worker pipes profile.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config defaults route batch size to worker pipes profile.
    config = pyrallel_consumer_test.build_kafka_config()

    # Then: The expected build kafka config defaults route batch size to worker pipes profile behavior is asserted.
    assert not hasattr(config.parallel_consumer.execution, "route_batch_size")
    assert config.parallel_consumer.execution.process_config.route_batch_size == 64


def test_build_kafka_config_defaults_to_worker_pipes_transport_profile() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config defaults to worker pipes transport profile.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config defaults to worker pipes transport profile.
    config = pyrallel_consumer_test.build_kafka_config()

    # Then: The expected build kafka config defaults to worker pipes transport profile behavior is asserted.
    assert not hasattr(
        config.parallel_consumer.execution.process_config, "transport_mode"
    )
    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0


def test_build_kafka_config_has_single_worker_pipes_topology() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config has single worker pipes topology.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config has single worker pipes topology.
    config = pyrallel_consumer_test.build_kafka_config()

    # Then: The expected build kafka config has single worker pipes topology behavior is asserted.
    assert not hasattr(
        config.parallel_consumer.execution.process_config, "transport_mode"
    )
    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0


def test_build_kafka_config_rejects_non_positive_process_count() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config rejects non positive process count.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config rejects non positive process count.
    # Then: The expected build kafka config rejects non positive process count behavior is asserted.
    with pytest.raises(ValueError, match="process_count must be greater than 0"):
        pyrallel_consumer_test.build_kafka_config(process_count=0)


def test_build_kafka_config_sets_adaptive_concurrency_flag() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config sets adaptive concurrency flag.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config sets adaptive concurrency flag.
    config = pyrallel_consumer_test.build_kafka_config(
        adaptive_concurrency_enabled=True
    )

    # Then: The expected build kafka config sets adaptive concurrency flag behavior is asserted.
    assert config.parallel_consumer.adaptive_concurrency.enabled is True


def test_build_kafka_config_enables_metrics_when_port_provided() -> None:
    # Given: Inputs and test doubles are prepared for build kafka config enables metrics when port provided.
    # When: The benchmark runtime configuration and metrics path is exercised for build kafka config enables metrics when port provided.
    config = pyrallel_consumer_test.build_kafka_config(metrics_port=9091)

    # Then: The expected build kafka config enables metrics when port provided behavior is asserted.
    assert config.metrics.enabled is True
    assert config.metrics.port == 9091


def test_normalize_metrics_port_treats_non_positive_values_as_disabled() -> None:
    # Given: Inputs and test doubles are prepared for normalize metrics port treats non positive values as disabled.
    # When: The benchmark runtime configuration and metrics path is exercised for normalize metrics port treats non positive values as disabled.
    # Then: The expected normalize metrics port treats non positive values as disabled behavior is asserted.
    assert run_parallel_benchmark._normalize_metrics_port(None) is None
    assert run_parallel_benchmark._normalize_metrics_port(0) is None
    assert run_parallel_benchmark._normalize_metrics_port(-1) is None
    assert run_parallel_benchmark._normalize_metrics_port(9091) == 9091


def test_ensure_metrics_port_available_reports_listening_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for ensure metrics port available reports listening pid.
    # When: The benchmark runtime configuration and metrics path is exercised for ensure metrics port available reports listening pid.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        monkeypatch.setattr(
            run_parallel_benchmark,
            "_list_listening_pids",
            lambda _port: ("1234",),
        )

        # Then: The expected ensure metrics port available reports listening pid behavior is asserted.
        with pytest.raises(
            RuntimeError,
            match=r"Metrics port \d+ is already in use\(PID 1234\)",
        ):
            run_parallel_benchmark._ensure_metrics_port_available(port)


def test_run_benchmark_checks_metrics_port_before_running_rounds(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark checks metrics port before running rounds.
    events: list[str] = []

    monkeypatch.setattr(
        run_parallel_benchmark,
        "_check_kafka_connection",
        lambda _bootstrap: events.append("kafka"),
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_ensure_metrics_port_available",
        lambda _port: (_ for _ in ()).throw(RuntimeError("port occupied")),
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "reset_topics_and_groups",
        lambda **_kwargs: events.append("reset"),
    )
    # When: The benchmark runtime configuration and metrics path is exercised for run benchmark checks metrics port before running rounds.
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    # Then: The expected run benchmark checks metrics port before running rounds behavior is asserted.
    with pytest.raises(RuntimeError, match="port occupied"):
        run_parallel_benchmark.run_benchmark(
            _build_args(metrics_port=9091), raw_argv=["--metrics-port", "9091"]
        )

    assert events == ["kafka"]


@pytest.mark.asyncio
async def test_publish_metrics_until_stopped_projects_pipeline_diagnostics() -> None:
    # Given: Inputs and test doubles are prepared for publish metrics until stopped projects pipeline diagnostics.
    stop_event = asyncio.Event()
    metrics_updates: list[Any] = []
    diagnostics_updates: list[Any] = []
    diagnostics = object()

    class _FakePoller:
        def get_metrics(self):
            stop_event.set()
            return SimpleNamespace(total_in_flight=0)

        def get_pipeline_diagnostics(self):
            return diagnostics

    class _FakeExporter:
        def update_from_system_metrics(self, metrics) -> None:
            metrics_updates.append(metrics)

        def update_pipeline_diagnostics(self, update, *, engine_type: str) -> None:
            diagnostics_updates.append((update, engine_type))

    # When: The benchmark runtime configuration and metrics path is exercised for publish metrics until stopped projects pipeline diagnostics.
    await pyrallel_consumer_test._publish_metrics_until_stopped(
        stop_event=stop_event,
        broker_poller=cast(BrokerPoller, _FakePoller()),
        prometheus_exporter=cast(PrometheusMetricsExporter, _FakeExporter()),
        engine_type="process",
        interval_sec=0,
    )

    # Then: The expected publish metrics until stopped projects pipeline diagnostics behavior is asserted.
    assert metrics_updates
    assert diagnostics_updates == [(diagnostics, "process")]


@pytest.mark.asyncio
async def test_finalize_consumer_run_projects_final_pipeline_diagnostics() -> None:
    # Given: Inputs and test doubles are prepared for finalize consumer run projects final pipeline diagnostics.
    metrics_updates: list[Any] = []
    diagnostics_updates: list[Any] = []
    diagnostics = object()

    class _FakePoller:
        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(process_batch_metrics=None, partitions=[])

        def get_pipeline_diagnostics(self):
            return diagnostics

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeExporter:
        def update_from_system_metrics(self, metrics) -> None:
            metrics_updates.append(metrics)

        def update_pipeline_diagnostics(self, update, *, engine_type: str) -> None:
            diagnostics_updates.append((update, engine_type))

    # When: The benchmark runtime configuration and metrics path is exercised for finalize consumer run projects final pipeline diagnostics.
    await pyrallel_consumer_test._finalize_consumer_run(
        broker_poller=cast(BrokerPoller, _FakePoller()),
        engine=cast(BaseExecutionEngine, _FakeEngine()),
        stats=None,
        prometheus_exporter=cast(PrometheusMetricsExporter, _FakeExporter()),
        metrics_start=0.0,
        engine_type="process",
    )

    # Then: The expected finalize consumer run projects final pipeline diagnostics behavior is asserted.
    assert metrics_updates
    assert diagnostics_updates == [(diagnostics, "process")]


def test_get_or_create_prometheus_exporter_reuses_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for get or create prometheus exporter reuses port.
    created: list[int] = []

    class _FakeExporter:
        def __init__(self, config):
            created.append(config.port)

    monkeypatch.setattr(
        pyrallel_consumer_test, "PrometheusMetricsExporter", _FakeExporter
    )
    pyrallel_consumer_test._PROMETHEUS_EXPORTERS.clear()

    first = pyrallel_consumer_test._get_or_create_prometheus_exporter(9091)
    # When: The benchmark runtime configuration and metrics path is exercised for get or create prometheus exporter reuses port.
    second = pyrallel_consumer_test._get_or_create_prometheus_exporter(9091)

    # Then: The expected get or create prometheus exporter reuses port behavior is asserted.
    assert first is second
    assert created == [9091]


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_process_batching_to_build_kafka_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test passes process batching to build kafka config.
    captured: dict[str, Any] = {}

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                partitions=[SimpleNamespace(tp=TopicPartition("demo", 0))]
            )

    class _FakeConsumer:
        async def shutdown(self) -> None:
            return None

    def _fake_build_kafka_config(**kwargs):
        captured.update(kwargs)
        return pyrallel_consumer_test.KafkaConfig()

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "build_kafka_config", _fake_build_kafka_config
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "ProcessExecutionEngine",
        lambda **_kwargs: _FakeConsumer(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "WorkManager",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "BrokerPoller",
        lambda **_kwargs: _FakePoller(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    # When: The benchmark runtime configuration and metrics path is exercised for run pyrallel consumer test passes process batching to build kafka config.
    (
        _timed_out,
        _stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
        process_count=2,
        process_batch_size=1,
        process_max_batch_wait_ms=0,
        process_flush_policy="demand",
        process_demand_flush_min_residence_ms=2,
    )

    # Then: The expected run pyrallel consumer test passes process batching to build kafka config behavior is asserted.
    assert captured["process_count"] == 2
    assert captured["process_batch_size"] == 1
    assert captured["process_max_batch_wait_ms"] == 0
    assert captured["process_flush_policy"] == "demand"
    assert captured["process_demand_flush_min_residence_ms"] == 2


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_does_not_pass_process_transport_mode_to_build_kafka_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test does not pass process transport mode to build kafka config.
    captured: dict[str, Any] = {}

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                partitions=[SimpleNamespace(tp=TopicPartition("demo", 0))]
            )

    class _FakeConsumer:
        async def shutdown(self) -> None:
            return None

    def _fake_build_kafka_config(**kwargs):
        captured.update(kwargs)
        return pyrallel_consumer_test.KafkaConfig()

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "build_kafka_config", _fake_build_kafka_config
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "ProcessExecutionEngine",
        lambda **_kwargs: _FakeConsumer(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "WorkManager",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "BrokerPoller",
        lambda **_kwargs: _FakePoller(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    # When: The benchmark runtime configuration and metrics path is exercised for run pyrallel consumer test does not pass process transport mode to build kafka config.
    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
    )

    # Then: The expected run pyrallel consumer test does not pass process transport mode to build kafka config behavior is asserted.
    assert "process_transport_mode" not in captured


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_route_batch_size_to_config_and_work_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test passes route batch size to config and work manager.
    captured_config: dict[str, Any] = {}
    captured_work_manager: dict[str, Any] = {}

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                partitions=[SimpleNamespace(tp=TopicPartition("demo", 0))]
            )

    class _FakeConsumer:
        async def shutdown(self) -> None:
            return None

    def _fake_build_kafka_config(**kwargs):
        captured_config.update(kwargs)
        config = pyrallel_consumer_test.KafkaConfig()
        config.parallel_consumer.execution.process_config.route_batch_size = kwargs[
            "route_batch_size"
        ]
        return config

    def _capture_work_manager(**kwargs):
        captured_work_manager.update(kwargs)
        return object()

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "build_kafka_config", _fake_build_kafka_config
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "ProcessExecutionEngine",
        lambda **_kwargs: _FakeConsumer(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "WorkManager",
        _capture_work_manager,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "BrokerPoller",
        lambda **_kwargs: _FakePoller(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    # When: The benchmark runtime configuration and metrics path is exercised for run pyrallel consumer test passes route batch size to config and work manager.
    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
        process_batch_size=1,
        route_batch_size=64,
    )

    # Then: The expected run pyrallel consumer test passes route batch size to config and work manager behavior is asserted.
    assert captured_config["route_batch_size"] == 64
    assert captured_config["process_batch_size"] == 1
    assert captured_work_manager["route_batch_size"] == 64


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_adaptive_concurrency_to_build_kafka_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test passes adaptive concurrency to build kafka config.
    captured: dict[str, Any] = {}

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                partitions=[SimpleNamespace(tp=TopicPartition("demo", 0))]
            )

    class _FakeConsumer:
        async def shutdown(self) -> None:
            return None

    def _fake_build_kafka_config(**kwargs):
        captured.update(kwargs)
        return pyrallel_consumer_test.KafkaConfig()

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "build_kafka_config", _fake_build_kafka_config
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "AsyncExecutionEngine",
        lambda **_kwargs: _FakeConsumer(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "WorkManager",
        lambda **_kwargs: object(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "BrokerPoller",
        lambda **_kwargs: _FakePoller(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    # When: The benchmark runtime configuration and metrics path is exercised for run pyrallel consumer test passes adaptive concurrency to build kafka config.
    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="async",
        adaptive_concurrency_enabled=True,
    )

    # Then: The expected run pyrallel consumer test passes adaptive concurrency to build kafka config behavior is asserted.
    assert captured["adaptive_concurrency_enabled"] is True


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_wires_prometheus_exporter_when_metrics_port_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test wires prometheus exporter when metrics port provided.
    captured_metrics_exporter: Any = None
    metrics_updates: list[Any] = []
    pipeline_updates: list[tuple[Any, str]] = []
    poller_exporters: list[Any] = []
    pipeline_diagnostics = object()

    class _FakePrometheusExporter:
        def observe_completion(self, tp, status, duration_seconds: float) -> None:
            del tp, status, duration_seconds

        def update_from_system_metrics(self, metrics) -> None:
            metrics_updates.append(metrics)

        def update_pipeline_diagnostics(self, diagnostics, *, engine_type: str) -> None:
            pipeline_updates.append((diagnostics, engine_type))

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def set_metrics_exporter(self, metrics_exporter) -> None:
            poller_exporters.append(metrics_exporter)

        def get_metrics(self):
            return SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[],
                process_batch_metrics=None,
            )

        def get_pipeline_diagnostics(self):
            return pipeline_diagnostics

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_get_or_create_prometheus_exporter",
        lambda port: _FakePrometheusExporter(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "AsyncExecutionEngine",
        lambda **_kwargs: _FakeEngine(),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "BrokerPoller",
        lambda **_kwargs: _FakePoller(),
    )

    def _capture_work_manager(**kwargs):
        nonlocal captured_metrics_exporter
        captured_metrics_exporter = kwargs["metrics_exporter"]
        return object()

    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        lambda *_args, **_kwargs: asyncio.sleep(0),
    )

    # When: The benchmark runtime configuration and metrics path is exercised for run pyrallel consumer test wires prometheus exporter when metrics port provided.
    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="async",
        metrics_port=9091,
    )

    # Then: The expected run pyrallel consumer test wires prometheus exporter when metrics port provided behavior is asserted.
    assert captured_metrics_exporter is not None
    assert metrics_updates
    assert pipeline_updates == [
        (pipeline_diagnostics, "async"),
        (pipeline_diagnostics, "async"),
    ]
    assert poller_exporters == [captured_metrics_exporter._prometheus_metrics_exporter]


@pytest.mark.asyncio
async def test_publish_metrics_loop_publishes_pipeline_diagnostics() -> None:
    # Given: Inputs and test doubles are prepared for publish metrics loop publishes pipeline diagnostics.
    stop_event = asyncio.Event()
    system_metrics = object()
    pipeline_diagnostics = object()
    system_updates: list[Any] = []
    pipeline_updates: list[tuple[Any, str]] = []

    class _FakePoller:
        def get_metrics(self):
            return system_metrics

        def get_pipeline_diagnostics(self):
            return pipeline_diagnostics

    class _FakeExporter:
        def update_from_system_metrics(self, metrics) -> None:
            system_updates.append(metrics)

        def update_pipeline_diagnostics(self, diagnostics, *, engine_type: str) -> None:
            pipeline_updates.append((diagnostics, engine_type))
            stop_event.set()

    # When: The benchmark runtime configuration and metrics path is exercised for publish metrics loop publishes pipeline diagnostics.
    await pyrallel_consumer_test._publish_metrics_until_stopped(
        stop_event=stop_event,
        broker_poller=cast(Any, _FakePoller()),
        prometheus_exporter=cast(Any, _FakeExporter()),
        engine_type=ExecutionMode.PROCESS.value,
        interval_sec=0,
    )

    # Then: The expected publish metrics loop publishes pipeline diagnostics behavior is asserted.
    assert system_updates == [system_metrics]
    assert pipeline_updates == [(pipeline_diagnostics, "process")]


@pytest.mark.asyncio
async def test_finalize_consumer_run_publishes_final_pipeline_diagnostics() -> None:
    # Given: Inputs and test doubles are prepared for finalize consumer run publishes final pipeline diagnostics.
    system_metrics = SimpleNamespace(partitions=[], process_batch_metrics=None)
    pipeline_diagnostics = object()
    system_updates: list[Any] = []
    pipeline_updates: list[tuple[Any, str]] = []

    class _FakePoller:
        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return system_metrics

        def get_pipeline_diagnostics(self):
            return pipeline_diagnostics

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeExporter:
        def update_from_system_metrics(self, metrics) -> None:
            system_updates.append(metrics)

        def update_pipeline_diagnostics(self, diagnostics, *, engine_type: str) -> None:
            pipeline_updates.append((diagnostics, engine_type))

    # When: The benchmark runtime configuration and metrics path is exercised for finalize consumer run publishes final pipeline diagnostics.
    await pyrallel_consumer_test._finalize_consumer_run(
        broker_poller=cast(Any, _FakePoller()),
        engine=cast(Any, _FakeEngine()),
        stats=None,
        prometheus_exporter=cast(Any, _FakeExporter()),
        metrics_start=0.0,
        engine_type=ExecutionMode.ASYNC.value,
    )

    # Then: The expected finalize consumer run publishes final pipeline diagnostics behavior is asserted.
    assert system_updates == [system_metrics]
    assert pipeline_updates == [(pipeline_diagnostics, "async")]
