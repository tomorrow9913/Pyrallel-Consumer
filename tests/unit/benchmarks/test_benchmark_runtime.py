from __future__ import annotations

import argparse
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
