from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast

import pytest

from benchmarks import (
    baseline_consumer,
    producer,
    pyrallel_consumer_test,
    run_parallel_benchmark,
)
from benchmarks.stats import BenchmarkResult
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem


@pytest.fixture
def benchmark_result() -> BenchmarkResult:
    return BenchmarkResult(
        run_name="demo",
        run_type="baseline",
        workload="sleep",
        topic="demo-topic",
        ordering="key_hash",
        messages_processed=10,
        total_time_sec=1.0,
        throughput_tps=10.0,
        avg_processing_ms=1.0,
        p99_processing_ms=1.0,
    )


def _build_args(**overrides: Any) -> argparse.Namespace:
    parser = run_parallel_benchmark.build_parser()
    args = parser.parse_args([])
    defaults = {
        "skip_baseline": False,
        "skip_async": False,
        "skip_process": True,
        "skip_reset": False,
        "topic_prefix": "demo-topic",
        "baseline_group": "baseline-group",
        "async_group": "async-group",
        "process_group": "process-group",
        "num_partitions": 3,
        "num_messages": 10,
        "num_keys": 2,
        "timeout_sec": 5,
        "bootstrap_servers": "localhost:9092",
        "workloads": ["sleep"],
        "order": ["key_hash"],
        "strict_completion_monitor": ["on"],
        "profile": False,
        "json_output": "benchmarks/results/test-runtime.json",
        "log_level": "WARNING",
    }
    for key, value in {**defaults, **overrides}.items():
        setattr(args, key, value)
    return args


def test_run_benchmark_resets_each_mode_immediately_before_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None: None,
    )

    def _record_reset(*, topics, consumer_groups, **_kwargs) -> None:
        topic_name = next(iter(topics.keys()))
        events.append(("reset", topic_name))
        events.append(("groups", consumer_groups[0]))

    def _baseline_round(**kwargs) -> BenchmarkResult:
        events.append(("run", kwargs["topic_name"]))
        return benchmark_result

    async def _async_round(**kwargs) -> BenchmarkResult:
        events.append(("run", kwargs["topic_name"]))
        return benchmark_result

    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", _record_reset
    )
    monkeypatch.setattr(run_parallel_benchmark, "_run_baseline_round", _baseline_round)
    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(), raw_argv=["--num-messages", "10"]
    )

    assert events == [
        ("reset", "demo-topic-sleep-key_hash-baseline"),
        ("groups", "baseline-group-sleep-key_hash"),
        ("run", "demo-topic-sleep-key_hash-baseline"),
        ("reset", "demo-topic-sleep-key_hash-async"),
        ("groups", "async-group-sleep-key_hash"),
        ("run", "demo-topic-sleep-key_hash-async"),
    ]


def test_run_benchmark_expands_selected_workloads_and_orderings(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    baseline_calls: list[tuple[str, str, str]] = []
    async_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )

    def _baseline_round(**kwargs) -> BenchmarkResult:
        baseline_calls.append(
            (kwargs["run_name"], kwargs["workload"], kwargs["ordering"])
        )
        return benchmark_result

    async def _async_round(**kwargs) -> BenchmarkResult:
        async_calls.append((kwargs["run_name"], kwargs["workload"], kwargs["ordering"]))
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_baseline_round", _baseline_round)
    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
        ),
        raw_argv=[
            "--workloads",
            "sleep,cpu",
            "--order",
            "key_hash,partition",
        ],
    )

    assert baseline_calls == [
        ("sleep-key_hash-baseline", "sleep", "key_hash"),
        ("sleep-partition-baseline", "sleep", "partition"),
        ("cpu-key_hash-baseline", "cpu", "key_hash"),
        ("cpu-partition-baseline", "cpu", "partition"),
    ]
    assert async_calls == [
        ("sleep-key_hash-pyrallel-async", "sleep", "key_hash"),
        ("sleep-partition-pyrallel-async", "sleep", "partition"),
        ("cpu-key_hash-pyrallel-async", "cpu", "key_hash"),
        ("cpu-partition-pyrallel-async", "cpu", "partition"),
    ]


def test_run_benchmark_expands_strict_completion_monitor_modes(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    async_calls: list[tuple[str, bool, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        async_calls.append(
            (
                kwargs["run_name"],
                kwargs["strict_completion_monitor_enabled"],
                kwargs["topic_name"],
            )
        )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(strict_completion_monitor=["on", "off"]),
        raw_argv=["--strict-completion-monitor", "on,off"],
    )

    assert async_calls == [
        (
            "sleep-key_hash-pyrallel-async-strict-on",
            True,
            "demo-topic-sleep-key_hash-async-strict-on",
        ),
        (
            "sleep-key_hash-pyrallel-async-strict-off",
            False,
            "demo-topic-sleep-key_hash-async-strict-off",
        ),
    ]


def test_produce_messages_skips_topic_creation_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    producer_instance = SimpleNamespace(
        produce=lambda *args, **kwargs: None,
        poll=lambda _timeout: None,
        flush=lambda timeout=60: None,
    )
    create_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(producer, "Producer", lambda _conf: producer_instance)
    monkeypatch.setattr(
        producer,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions: create_calls.append(
            (topic_name, num_partitions)
        ),
    )

    producer.produce_messages(
        num_messages=1,
        num_keys=1,
        num_partitions=2,
        topic_name="demo-topic",
        ensure_topic_exists=False,
    )

    assert create_calls == []


def test_run_baseline_round_preserves_workload_specific_run_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        run_parallel_benchmark, "produce_messages", lambda **kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "consume_messages",
        lambda **kwargs: BenchmarkResult(
            run_name="sleep-baseline",
            run_type="baseline",
            workload="sleep",
            topic="demo-topic",
            ordering="key_hash",
            messages_processed=10,
            total_time_sec=1.0,
            throughput_tps=10.0,
            avg_processing_ms=1.0,
            p99_processing_ms=1.0,
        ),
    )

    result = run_parallel_benchmark._run_baseline_round(
        run_name="sleep-baseline",
        topic_name="demo-topic",
        num_messages=10,
        bootstrap_servers="localhost:9092",
        num_partitions=1,
        num_keys=1,
        group_id="demo-group",
        worker_fn=lambda _payload: None,
        workload="sleep",
        ordering="key_hash",
    )

    assert result.run_name == "sleep-baseline"


def test_baseline_consumer_logs_effective_topic_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class _FakeConsumer:
        def subscribe(self, topics) -> None:
            self.topics = topics

        def poll(self, timeout: float = 1.0):
            del timeout
            return None

        def commit(self, asynchronous: bool = False) -> None:
            del asynchronous

        def close(self) -> None:
            return None

    monkeypatch.setattr(baseline_consumer, "Consumer", lambda _conf: _FakeConsumer())

    baseline_consumer.consume_messages(
        num_messages_to_process=0,
        topic_name="demo-baseline-topic",
    )

    output = capsys.readouterr().out
    assert "Starting baseline consumer for topic 'demo-baseline-topic'." in output


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_skips_topic_creation_after_reset(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    create_calls: list[str] = []
    reset_calls: list[tuple[dict[str, Any], list[str]]] = []

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            return None

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "reset_topics_and_groups",
        lambda **kwargs: reset_calls.append(
            (kwargs["topics"], list(kwargs["consumer_groups"]))
        ),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: create_calls.append(topic_name),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    (
        timed_out,
        _stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=1,
        topic_name="demo-topic",
        consumer_group="demo-group",
        execution_mode="async",
        reset_topic=True,
        stats_tracker=None,
    )

    assert timed_out is True
    assert reset_calls == [
        (
            {"demo-topic": pyrallel_consumer_test.TopicConfig(num_partitions=1)},
            ["demo-group"],
        )
    ]
    assert create_calls == []


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_stops_poller_and_engine_when_assignment_wait_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class _FakeEngine:
        async def shutdown(self) -> None:
            events.append("engine.shutdown")

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def start(self) -> None:
            events.append("poller.start")

        def get_metrics(self):
            return SimpleNamespace(partitions=[])

        async def stop(self) -> None:
            events.append("poller.stop")

    async def _fail_wait(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("assignment failed")

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _fail_wait,
    )

    with pytest.raises(RuntimeError, match="assignment failed"):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=1,
            timeout_sec=1,
            topic_name="demo-topic",
            execution_mode="async",
            stats_tracker=None,
        )

    assert events == ["poller.start", "poller.stop", "engine.shutdown"]


@pytest.mark.asyncio
async def test_wait_for_partition_assignment_raises_clear_error_for_topic() -> None:
    class _NoAssignmentPoller:
        def get_metrics(self):
            return SimpleNamespace(partitions=[])

    with pytest.raises(RuntimeError, match="demo-topic"):
        await pyrallel_consumer_test._wait_for_partition_assignment(
            cast(Any, _NoAssignmentPoller()),
            topic_name="demo-topic",
            timeout_sec=0.0,
        )


def test_build_kafka_config_sets_strict_completion_monitor_flag() -> None:
    config = pyrallel_consumer_test.build_kafka_config(
        strict_completion_monitor_enabled=False
    )

    assert config.parallel_consumer.strict_completion_monitor_enabled is False


def test_build_kafka_config_sets_process_batching_overrides() -> None:
    config = pyrallel_consumer_test.build_kafka_config(
        process_batch_size=1,
        process_max_batch_wait_ms=0,
    )

    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_process_batching_to_build_kafka_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    (
        _timed_out,
        _stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
        process_batch_size=1,
        process_max_batch_wait_ms=0,
    )

    assert captured["process_batch_size"] == 1
    assert captured["process_max_batch_wait_ms"] == 0


def test_run_benchmark_passes_process_batching_overrides_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    process_calls: list[tuple[int, int]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(
                (
                    kwargs["process_batch_size"],
                    kwargs["process_max_batch_wait_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
            process_batch_size=1,
            process_max_batch_wait_ms=0,
        ),
        raw_argv=[
            "--skip-async",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
        ],
    )

    assert process_calls == [(1, 0)]


def test_ordering_validator_reports_key_hash_pass_summary() -> None:
    validator = pyrallel_consumer_test.OrderingValidator(
        ordering_mode="key_hash", topic_name="demo-topic"
    )

    validator.observe(
        WorkItem(
            id="item-1",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=0,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":0}',
        )
    )
    validator.observe(
        WorkItem(
            id="item-2",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=1,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":1}',
        )
    )

    assert validator.summary() == "Ordering validation PASS: key_hash keys=1 checks=2"


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_validates_key_hash_ordering_in_process_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    captured_exporter: Any = None
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            assert captured_process_worker is not None
            process_worker = cast(Callable[[WorkItem], None], captured_process_worker)
            for offset, sequence in ((0, 0), (1, 1)):
                item = WorkItem(
                    id=f"item-{offset}",
                    tp=TopicPartition(topic="demo-topic", partition=0),
                    offset=offset,
                    epoch=0,
                    key="key-0",
                    payload=f'{{"key":"key-0","sequence":{sequence}}}'.encode(),
                )
                process_worker(item)  # pylint: disable=not-callable
                captured_exporter.observe_work_completion(
                    SimpleNamespace(status=CompletionStatus.SUCCESS),
                    item,
                    0.01,
                )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    (
        timed_out,
        consumption_stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=2,
        timeout_sec=5,
        topic_name="demo-topic",
        execution_mode="process",
        ordering_mode="key_hash",
        stats_tracker=None,
    )

    assert timed_out is False
    assert consumption_stats.processed == 2
    assert (
        "Ordering validation PASS: key_hash keys=1 checks=2" in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_uses_picklable_process_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            return None

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=1,
        timeout_sec=1,
        topic_name="demo-topic",
        execution_mode="process",
        ordering_mode="unordered",
        stats_tracker=None,
        process_worker_fn=pyrallel_consumer_test._process_mode_worker,
    )

    assert captured_process_worker is pyrallel_consumer_test._process_mode_worker


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_raises_on_process_ordering_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_exporter: Any = None
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            assert captured_process_worker is not None
            process_worker = cast(Callable[[WorkItem], None], captured_process_worker)
            item_0 = WorkItem(
                id="item-0",
                tp=TopicPartition(topic="demo-topic", partition=0),
                offset=0,
                epoch=0,
                key="key-0",
                payload=b'{"key":"key-0","sequence":0}',
            )
            process_worker(item_0)  # pylint: disable=not-callable
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.SUCCESS,
                0.01,
            )
            captured_exporter.observe_work_completion(
                SimpleNamespace(status=CompletionStatus.SUCCESS),
                item_0,
                0.01,
            )
            item_1 = WorkItem(
                id="item-1",
                tp=TopicPartition(topic="demo-topic", partition=0),
                offset=1,
                epoch=0,
                key="key-0",
                payload=b'{"key":"key-0","sequence":3}',
            )
            process_worker(item_1)  # pylint: disable=not-callable
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.SUCCESS,
                0.01,
            )
            captured_exporter.observe_work_completion(
                SimpleNamespace(status=CompletionStatus.SUCCESS),
                item_1,
                0.01,
            )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    with pytest.raises(
        RuntimeError, match="Ordering validation failed for key key-0 on demo-topic"
    ):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=2,
            timeout_sec=5,
            topic_name="demo-topic",
            execution_mode="process",
            ordering_mode="key_hash",
            stats_tracker=None,
        )


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_raises_clear_error_on_completion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    captured_exporter: Any = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            events.append("engine.shutdown")

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            events.append("poller.start")
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.FAILURE,
                0.01,
            )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            events.append("poller.stop")

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    with pytest.raises(
        RuntimeError,
        match="Benchmark worker failure on demo-topic\\[0\\]: completion failed",
    ):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=1,
            timeout_sec=5,
            topic_name="demo-topic",
            execution_mode="async",
            stats_tracker=None,
        )

    assert events == ["poller.start", "poller.stop", "engine.shutdown"]
