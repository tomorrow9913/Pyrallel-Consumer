from __future__ import annotations

import argparse
import asyncio
import socket
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
from benchmarks.stats import BenchmarkResult, BenchmarkStats
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)

E2E_WORKFLOW = (
    run_parallel_benchmark.Path(__file__).resolve().parents[3]
    / ".github"
    / "workflows"
    / "e2e.yml"
)


@pytest.fixture
def benchmark_result() -> BenchmarkResult:
    return BenchmarkResult(
        run_name="demo",
        run_type="baseline",
        workload="sleep",
        topic="demo-topic",
        ordering="key_hash",
        process_transport_mode=None,
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
        "adaptive_concurrency": ["off"],
        "process_transport": "worker_pipes",
        "route_batch_size": 64,
        "metrics_port": 0,
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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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


def test_build_parser_accepts_adaptive_concurrency_matrix() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(["--adaptive-concurrency", "off,on"])

    assert args.adaptive_concurrency == ["off", "on"]


def test_run_benchmark_expands_adaptive_concurrency_modes(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    async_calls: list[tuple[str, bool, str, str]] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
                kwargs["adaptive_concurrency_enabled"],
                kwargs["topic_name"],
                kwargs["group_id"],
            )
        )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(adaptive_concurrency=["off", "on"]),
        raw_argv=["--adaptive-concurrency", "off,on"],
    )

    assert async_calls == [
        (
            "sleep-key_hash-pyrallel-async-adaptive-off",
            False,
            "demo-topic-sleep-key_hash-async-adaptive-off",
            "async-group-sleep-key_hash-adaptive-off",
        ),
        (
            "sleep-key_hash-pyrallel-async-adaptive-on",
            True,
            "demo-topic-sleep-key_hash-async-adaptive-on",
            "async-group-sleep-key_hash-adaptive-on",
        ),
    ]


def test_build_benchmark_run_plans_expands_selected_workloads_and_orderings() -> None:
    plans = run_parallel_benchmark._build_benchmark_run_plans(
        _build_args(
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
            skip_process=False,
        )
    )

    assert [
        (plan.kind, plan.run_name, plan.workload, plan.ordering) for plan in plans
    ] == [
        ("baseline", "sleep-key_hash-baseline", "sleep", "key_hash"),
        ("async", "sleep-key_hash-pyrallel-async", "sleep", "key_hash"),
        ("process", "sleep-key_hash-pyrallel-process", "sleep", "key_hash"),
        ("baseline", "sleep-partition-baseline", "sleep", "partition"),
        ("async", "sleep-partition-pyrallel-async", "sleep", "partition"),
        ("process", "sleep-partition-pyrallel-process", "sleep", "partition"),
        ("baseline", "cpu-key_hash-baseline", "cpu", "key_hash"),
        ("async", "cpu-key_hash-pyrallel-async", "cpu", "key_hash"),
        ("process", "cpu-key_hash-pyrallel-process", "cpu", "key_hash"),
        ("baseline", "cpu-partition-baseline", "cpu", "partition"),
        ("async", "cpu-partition-pyrallel-async", "cpu", "partition"),
        ("process", "cpu-partition-pyrallel-process", "cpu", "partition"),
    ]


def test_build_benchmark_run_plans_preserves_mode_suffixes_and_options() -> None:
    plans = run_parallel_benchmark._build_benchmark_run_plans(
        _build_args(
            skip_baseline=True,
            skip_process=False,
            strict_completion_monitor=["on", "off"],
            adaptive_concurrency=["off", "on"],
            workload_options={"sleep": {"sleep_ms": 1.25}},
        )
    )

    assert [
        (
            plan.kind,
            plan.run_name,
            plan.topic_name,
            plan.group_id,
            plan.strict_completion_monitor_enabled,
            plan.adaptive_concurrency_enabled,
            plan.workload_options,
        )
        for plan in plans
    ] == [
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-on-adaptive-off",
            "demo-topic-sleep-key_hash-async-strict-on-adaptive-off",
            "async-group-sleep-key_hash-strict-on-adaptive-off",
            True,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-on-adaptive-off",
            "demo-topic-sleep-key_hash-process-strict-on-adaptive-off",
            "process-group-sleep-key_hash-strict-on-adaptive-off",
            True,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-on-adaptive-on",
            "demo-topic-sleep-key_hash-async-strict-on-adaptive-on",
            "async-group-sleep-key_hash-strict-on-adaptive-on",
            True,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-on-adaptive-on",
            "demo-topic-sleep-key_hash-process-strict-on-adaptive-on",
            "process-group-sleep-key_hash-strict-on-adaptive-on",
            True,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-off-adaptive-off",
            "demo-topic-sleep-key_hash-async-strict-off-adaptive-off",
            "async-group-sleep-key_hash-strict-off-adaptive-off",
            False,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-off-adaptive-off",
            "demo-topic-sleep-key_hash-process-strict-off-adaptive-off",
            "process-group-sleep-key_hash-strict-off-adaptive-off",
            False,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-off-adaptive-on",
            "demo-topic-sleep-key_hash-async-strict-off-adaptive-on",
            "async-group-sleep-key_hash-strict-off-adaptive-on",
            False,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-off-adaptive-on",
            "demo-topic-sleep-key_hash-process-strict-off-adaptive-on",
            "process-group-sleep-key_hash-strict-off-adaptive-on",
            False,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
    ]


def test_run_benchmark_preserves_event_loop_per_workload_ordering(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    loop_runs: list[int] = []
    real_asyncio_run = asyncio.run

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )

    async def _async_round(**_kwargs) -> BenchmarkResult:
        return benchmark_result

    def _record_asyncio_run(coro):
        loop_runs.append(1)
        return real_asyncio_run(coro)

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)
    monkeypatch.setattr(run_parallel_benchmark.asyncio, "run", _record_asyncio_run)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_process=False,
            skip_reset=True,
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-reset",
            "--workloads",
            "sleep,cpu",
            "--order",
            "key_hash,partition",
        ],
    )

    assert loop_runs == [1, 1, 1, 1]


def test_build_artifact_metadata_prefers_github_environment() -> None:
    metadata = run_parallel_benchmark._build_artifact_metadata(
        output_path="benchmarks/results/release-gate.json",
        environ={
            "GITHUB_ACTIONS": "true",
            "GITHUB_SHA": "deadbeef",
            "GITHUB_REF": "refs/heads/develop",
            "GITHUB_REF_NAME": "develop",
            "GITHUB_REF_TYPE": "branch",
            "GITHUB_REPOSITORY": "tomorrow9913/Pyrallel-Consumer",
            "GITHUB_WORKFLOW": "benchmarks",
            "GITHUB_WORKFLOW_REF": (
                "tomorrow9913/Pyrallel-Consumer/.github/workflows/benchmarks.yml"
                "@refs/heads/develop"
            ),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "2",
            "GITHUB_JOB": "release-candidate-gate",
            "GITHUB_EVENT_NAME": "workflow_dispatch",
            "PYRALLEL_BENCHMARK_ARTIFACT_NAME": "release-gate-develop-123",
        },
    )

    assert metadata == {
        "artifact_name": "release-gate-develop-123",
        "artifact_path": "benchmarks/results/release-gate.json",
        "execution_context": "github_actions",
        "generated_at_utc": metadata["generated_at_utc"],
        "git_commit_sha": "deadbeef",
        "git_ref": "refs/heads/develop",
        "git_ref_name": "develop",
        "git_ref_type": "branch",
        "github_event_name": "workflow_dispatch",
        "github_job": "release-candidate-gate",
        "github_repository": "tomorrow9913/Pyrallel-Consumer",
        "github_run_attempt": "2",
        "github_run_id": "123",
        "github_workflow": "benchmarks",
        "github_workflow_ref": (
            "tomorrow9913/Pyrallel-Consumer/.github/workflows/benchmarks.yml"
            "@refs/heads/develop"
        ),
    }


def test_run_benchmark_writes_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    captured: dict[str, Any] = {}

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
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_build_artifact_metadata",
        lambda *, output_path, environ=None: {
            "artifact_path": output_path,
            "git_commit_sha": "deadbeef",
        },
    )

    def _capture_results_json(_results, _path, options=None, artifact_metadata=None):
        captured["options"] = options
        captured["artifact_metadata"] = artifact_metadata

    monkeypatch.setattr(
        run_parallel_benchmark, "write_results_json", _capture_results_json
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(skip_async=True, skip_process=True),
        raw_argv=["--skip-async", "--skip-process"],
    )

    assert captured["artifact_metadata"] == {
        "artifact_path": "benchmarks/results/test-runtime.json",
        "git_commit_sha": "deadbeef",
    }


def test_e2e_workflow_runs_monitoring_smoke_as_test_code() -> None:
    text = E2E_WORKFLOW.read_text(encoding="utf-8")

    assert "kafka-1 kafka-exporter prometheus grafana" in text
    assert "uv run pytest tests/e2e -q" in text
    assert "actions/upload-artifact@v7" in text
    assert "path: .artifacts/e2e-junit*.xml" in text


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
            process_transport_mode=None,
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


@pytest.mark.asyncio
async def test_run_pyrparallel_round_omits_process_transport_helper_argument(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        run_parallel_benchmark, "produce_messages", lambda **kwargs: None
    )

    async def _fake_run_pyrallel_consumer_test(**kwargs):
        captured.update(kwargs)
        return (False, None, benchmark_result)

    monkeypatch.setattr(
        run_parallel_benchmark,
        "run_pyrallel_consumer_test",
        _fake_run_pyrallel_consumer_test,
    )

    result = await run_parallel_benchmark._run_pyrparallel_round(
        topic_name="demo-topic",
        run_name="async-round",
        mode=ExecutionMode.ASYNC,
        num_messages=10,
        bootstrap_servers="localhost:9092",
        num_partitions=1,
        num_keys=1,
        group_id="demo-group",
        timeout_sec=10,
        async_worker_fn=lambda _item: asyncio.sleep(0),
        process_worker_fn=lambda _item: None,
        workload="sleep",
        ordering="key_hash",
    )

    assert result is benchmark_result
    assert "process_transport_mode" not in captured


def test_benchmark_stats_summary_carries_process_transport_mode() -> None:
    stats = run_parallel_benchmark.BenchmarkStats(
        run_name="process-run",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        process_transport_mode="worker_pipes",
        target_messages=1,
    )

    stats.start()
    assert stats._start_time is not None
    stats.record(0.001, completed_at=stats._start_time + 0.001)
    stats.stop()

    summary = stats.summary()

    assert summary.process_transport_mode == "worker_pipes"


def test_benchmark_stats_summary_carries_route_batch_size() -> None:
    stats = run_parallel_benchmark.BenchmarkStats(
        run_name="process-run",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        route_batch_size=64,
        target_messages=1,
    )

    stats.start()
    assert stats._start_time is not None
    stats.record(0.001, completed_at=stats._start_time + 0.001)
    stats.stop()

    summary = stats.summary()

    assert summary.route_batch_size == 64


def test_benchmark_stats_summary_carries_runtime_ipc_metrics() -> None:
    stats = run_parallel_benchmark.BenchmarkStats(
        run_name="process-run",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        process_transport_mode="worker_pipes",
        route_batch_size=64,
        process_batch_size=1,
        target_messages=1,
    )
    process_metrics = SimpleNamespace(
        items_per_input_ipc=2.0,
        items_per_completion_ipc=2.0,
        route_batch_count=1,
        route_batch_item_count=2,
        route_batch_size_avg=2.0,
        route_batch_size_max=2,
        completion_item_payload_count=0,
        completion_batch_payload_count=1,
    )

    stats.start()
    assert stats._start_time is not None
    stats.record(0.001, completed_at=stats._start_time + 0.001)
    stats.record_process_batch_metrics(process_metrics)
    stats.stop()

    summary = stats.summary()

    assert summary.process_batch_size == 1
    assert summary.route_batch_size == 64
    assert summary.items_per_input_ipc == 2.0
    assert summary.items_per_completion_ipc == 2.0
    assert summary.route_batch_count == 1
    assert summary.route_batch_item_count == 2
    assert summary.route_batch_size_avg == 2.0
    assert summary.route_batch_size_max == 2
    assert summary.completion_item_payload_count == 0
    assert summary.completion_batch_payload_count == 1


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
async def test_run_pyrallel_consumer_test_records_metrics_after_poller_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            self._lag = 99

        async def start(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=self._lag,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def stop(self) -> None:
            self._lag = 0

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
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *args, **kwargs: None,
    )
    stats = BenchmarkStats(
        run_name="demo",
        run_type="async",
        workload="io",
        topic="demo-topic",
        ordering="key_hash",
        target_messages=1,
    )

    (
        _timed_out,
        _stats,
        summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=1,
        timeout_sec=0,
        topic_name="demo-topic",
        consumer_group="demo-group",
        execution_mode="async",
        stats_tracker=stats,
    )

    assert summary is not None
    assert summary.final_lag == 0


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


def test_benchmark_metrics_observer_records_success_and_stops_at_target() -> None:
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

    observer.observe_completion(tp, CompletionStatus.SUCCESS, 0.01)

    assert completions == [(tp, CompletionStatus.SUCCESS, 0.01)]
    assert consumption_stats.processed == 1
    assert stats.processed == 1
    assert completion_event.is_set() is True
    assert observer.failure_error is None


def test_benchmark_metrics_observer_reports_completion_failure() -> None:
    completion_event = asyncio.Event()
    observer = pyrallel_consumer_test.BenchmarkMetricsObserver(
        benchmark_stats=None,
        cons_stats=pyrallel_consumer_test.ConsumptionStats(target=1),
        completion_event=completion_event,
    )

    observer.observe_completion(
        TopicPartition(topic="demo-topic", partition=0),
        CompletionStatus.FAILURE,
        0.01,
    )

    assert observer.failure_error == (
        "Benchmark worker failure on demo-topic[0]: completion failed"
    )
    assert completion_event.is_set() is True


def test_record_release_gate_metrics_from_snapshot_sums_partition_metrics() -> None:
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

    pyrallel_consumer_test._record_release_gate_metrics_from_snapshot(
        stats,
        cast(Any, metrics),
        elapsed_sec=1.25,
    )

    assert stats._release_gate_observations == [
        {
            "elapsed_sec": 1.25,
            "consumer_parallel_lag": 8,
            "consumer_gap_count": 3,
        }
    ]


def test_build_kafka_config_sets_strict_completion_monitor_flag() -> None:
    config = pyrallel_consumer_test.build_kafka_config(
        strict_completion_monitor_enabled=False
    )

    assert config.parallel_consumer.strict_completion_monitor_enabled is False


def test_build_kafka_config_sets_process_batching_overrides() -> None:
    config = pyrallel_consumer_test.build_kafka_config(
        process_count=2,
        process_batch_size=1,
        process_max_batch_wait_ms=0,
        process_flush_policy="demand_min_residence",
        process_demand_flush_min_residence_ms=2,
    )

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
    config = pyrallel_consumer_test.build_kafka_config(
        process_batch_size=1,
        route_batch_size=64,
    )

    assert not hasattr(config.parallel_consumer.execution, "route_batch_size")
    assert config.parallel_consumer.execution.process_config.route_batch_size == 64
    assert config.parallel_consumer.execution.process_config.batch_size == 1


def test_build_kafka_config_defaults_route_batch_size_to_worker_pipes_profile() -> None:
    config = pyrallel_consumer_test.build_kafka_config()

    assert not hasattr(config.parallel_consumer.execution, "route_batch_size")
    assert config.parallel_consumer.execution.process_config.route_batch_size == 64


def test_build_kafka_config_defaults_to_worker_pipes_transport_profile() -> None:
    config = pyrallel_consumer_test.build_kafka_config()

    assert not hasattr(
        config.parallel_consumer.execution.process_config, "transport_mode"
    )
    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0


def test_build_kafka_config_has_single_worker_pipes_topology() -> None:
    config = pyrallel_consumer_test.build_kafka_config()

    assert not hasattr(
        config.parallel_consumer.execution.process_config, "transport_mode"
    )
    assert config.parallel_consumer.execution.process_config.batch_size == 1
    assert config.parallel_consumer.execution.process_config.max_batch_wait_ms == 0


def test_build_kafka_config_rejects_non_positive_process_count() -> None:
    with pytest.raises(ValueError, match="process_count must be greater than 0"):
        pyrallel_consumer_test.build_kafka_config(process_count=0)


def test_build_kafka_config_sets_adaptive_concurrency_flag() -> None:
    config = pyrallel_consumer_test.build_kafka_config(
        adaptive_concurrency_enabled=True
    )

    assert config.parallel_consumer.adaptive_concurrency.enabled is True


def test_build_kafka_config_enables_metrics_when_port_provided() -> None:
    config = pyrallel_consumer_test.build_kafka_config(metrics_port=9091)

    assert config.metrics.enabled is True
    assert config.metrics.port == 9091


def test_normalize_metrics_port_treats_non_positive_values_as_disabled() -> None:
    assert run_parallel_benchmark._normalize_metrics_port(None) is None
    assert run_parallel_benchmark._normalize_metrics_port(0) is None
    assert run_parallel_benchmark._normalize_metrics_port(-1) is None
    assert run_parallel_benchmark._normalize_metrics_port(9091) == 9091


def test_ensure_metrics_port_available_reports_listening_pid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]

        monkeypatch.setattr(
            run_parallel_benchmark,
            "_list_listening_pids",
            lambda _port: ("1234",),
        )

        with pytest.raises(
            RuntimeError,
            match=r"Metrics port \d+ is already in use\(PID 1234\)",
        ):
            run_parallel_benchmark._ensure_metrics_port_available(port)


def test_run_benchmark_checks_metrics_port_before_running_rounds(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
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
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    with pytest.raises(RuntimeError, match="port occupied"):
        run_parallel_benchmark.run_benchmark(
            _build_args(metrics_port=9091), raw_argv=["--metrics-port", "9091"]
        )

    assert events == ["kafka"]


def test_get_or_create_prometheus_exporter_reuses_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: list[int] = []

    class _FakeExporter:
        def __init__(self, config):
            created.append(config.port)

    monkeypatch.setattr(
        pyrallel_consumer_test, "PrometheusMetricsExporter", _FakeExporter
    )
    pyrallel_consumer_test._PROMETHEUS_EXPORTERS.clear()

    first = pyrallel_consumer_test._get_or_create_prometheus_exporter(9091)
    second = pyrallel_consumer_test._get_or_create_prometheus_exporter(9091)

    assert first is second
    assert created == [9091]


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
        process_count=2,
        process_batch_size=1,
        process_max_batch_wait_ms=0,
        process_flush_policy="demand",
        process_demand_flush_min_residence_ms=2,
    )

    assert captured["process_count"] == 2
    assert captured["process_batch_size"] == 1
    assert captured["process_max_batch_wait_ms"] == 0
    assert captured["process_flush_policy"] == "demand"
    assert captured["process_demand_flush_min_residence_ms"] == 2


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_does_not_pass_process_transport_mode_to_build_kafka_config(
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

    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
    )

    assert "process_transport_mode" not in captured


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_route_batch_size_to_config_and_work_manager(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="process",
        process_worker_fn=lambda _item: None,
        process_batch_size=1,
        route_batch_size=64,
    )

    assert captured_config["route_batch_size"] == 64
    assert captured_config["process_batch_size"] == 1
    assert captured_work_manager["route_batch_size"] == 64


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_passes_adaptive_concurrency_to_build_kafka_config(
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

    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="async",
        adaptive_concurrency_enabled=True,
    )

    assert captured["adaptive_concurrency_enabled"] is True


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_wires_prometheus_exporter_when_metrics_port_provided(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_metrics_exporter: Any = None
    metrics_updates: list[Any] = []

    class _FakePrometheusExporter:
        def observe_completion(self, tp, status, duration_seconds: float) -> None:
            del tp, status, duration_seconds

        def update_from_system_metrics(self, metrics) -> None:
            metrics_updates.append(metrics)

    class _FakePoller:
        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[],
                process_batch_metrics=None,
            )

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

    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=0,
        execution_mode="async",
        metrics_port=9091,
    )

    assert captured_metrics_exporter is not None
    assert metrics_updates


def test_run_benchmark_passes_process_overrides_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    process_calls: list[tuple[int, int, int, str | None, int | None]] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
                    kwargs["process_count"],
                    kwargs["process_flush_policy"],
                    kwargs["process_demand_flush_min_residence_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
            process_count=2,
            process_batch_size=1,
            process_max_batch_wait_ms=0,
            process_flush_policy="demand_min_residence",
            process_demand_flush_min_residence_ms=2,
        ),
        raw_argv=[
            "--skip-async",
            "--process-count",
            "2",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
            "--process-flush-policy",
            "demand_min_residence",
            "--process-demand-flush-min-residence-ms",
            "2",
        ],
    )

    assert process_calls == [(1, 0, 2, "demand_min_residence", 2)]


def test_run_benchmark_does_not_pass_process_transport_mode_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    process_calls: list[bool] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
            process_calls.append("process_transport_mode" in kwargs)
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
        ),
        raw_argv=[
            "--skip-async",
        ],
    )

    assert process_calls == [False]


def test_run_benchmark_passes_route_batch_size_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    process_calls: list[int] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
            process_calls.append(kwargs["route_batch_size"])
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
            route_batch_size=64,
        ),
        raw_argv=[
            "--skip-async",
            "--process-route-batch-size",
            "64",
        ],
    )

    assert process_calls == [64]


def test_run_benchmark_does_not_forward_process_transport_mode_to_async_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    async_calls: list[str | None] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
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
        if kwargs["mode"].value == "async":
            async_calls.append(kwargs.get("process_transport_mode"))
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=False,
            skip_process=True,
        ),
        raw_argv=[
            "--skip-process",
        ],
    )

    assert async_calls == [None]


def test_run_benchmark_warns_for_tiny_partition_process_defaults(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_pyrparallel_round",
        lambda **_kwargs: asyncio.sleep(0, result=benchmark_result),
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--order",
            "partition",
        ],
    )

    output = capsys.readouterr().out
    assert "Tiny process partition benchmark detected" in output
    assert "--process-batch-size 1 --process-max-batch-wait-ms 0" in output


def test_run_benchmark_auto_tunes_tiny_partition_strict_process_defaults(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    process_calls: list[tuple[int | None, int | None]] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
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
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            strict_completion_monitor=["on"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--workloads",
            "sleep",
            "--order",
            "partition",
            "--strict-completion-monitor",
            "on",
        ],
    )

    output = capsys.readouterr().out
    assert "Auto-tuning process micro-batch for strict partition run" in output
    assert process_calls == [(1, 0)]


def test_run_benchmark_resolves_process_batching_per_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    process_calls: list[tuple[str, int | None, int | None]] = []

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
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(
                (
                    kwargs["run_name"],
                    kwargs["process_batch_size"],
                    kwargs["process_max_batch_wait_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            strict_completion_monitor=["on", "off"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--workloads",
            "sleep",
            "--order",
            "partition",
            "--strict-completion-monitor",
            "on,off",
        ],
    )

    assert process_calls == [
        ("sleep-partition-pyrallel-process-strict-on", 1, 0),
        ("sleep-partition-pyrallel-process-strict-off", None, None),
    ]


def test_run_benchmark_skips_tiny_partition_warning_when_batching_is_overridden(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_pyrparallel_round",
        lambda **_kwargs: asyncio.sleep(0, result=benchmark_result),
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            worker_sleep_ms=0.5,
            process_batch_size=1,
            process_max_batch_wait_ms=0,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--order",
            "partition",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
        ],
    )

    output = capsys.readouterr().out
    assert "Tiny process partition benchmark detected" not in output


def test_run_benchmark_filters_workload_options_per_selected_workload(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    selected_options: list[tuple[str, dict[str, dict[str, object]] | None]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )

    def _select_workers(**kwargs):
        selected_options.append((kwargs["workload"], kwargs["workload_options"]))
        return (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        )

    monkeypatch.setattr(run_parallel_benchmark, "_select_workers", _select_workers)
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            workloads=["sleep", "cpu"],
            workload_options={
                "sleep": {"sleep_ms": 1.25},
                "cpu": {"iterations": 2000},
            },
        ),
        raw_argv=[
            "--skip-async",
            "--workloads",
            "sleep,cpu",
            "--workload-option",
            "sleep.sleep_ms=1.25",
            "--workload-option",
            "cpu.iterations=2000",
        ],
    )

    assert selected_options == [
        ("sleep", {"sleep": {"sleep_ms": 1.25}}),
        ("cpu", {"cpu": {"iterations": 2000}}),
    ]


def test_run_benchmark_skips_process_worker_validation_when_process_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    validate_process_flags: list[bool] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )

    def _select_workers(**kwargs):
        validate_process_flags.append(kwargs["validate_process_worker"])
        return (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        )

    monkeypatch.setattr(run_parallel_benchmark, "_select_workers", _select_workers)
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(skip_async=True, skip_process=True),
        raw_argv=["--skip-async", "--skip-process"],
    )

    assert validate_process_flags == [False]


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


def test_ordering_validator_allows_nonzero_first_key_hash_sequence() -> None:
    validator = pyrallel_consumer_test.OrderingValidator(
        ordering_mode="key_hash", topic_name="demo-topic"
    )

    validator.observe(
        WorkItem(
            id="item-67",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=67,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":67}',
        )
    )
    validator.observe(
        WorkItem(
            id="item-68",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=68,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":68}',
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
