# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_benchmark_runtime_reporting.py
# Role: Verifies benchmark artifact metadata, workflow coverage, and result summary fields.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._benchmark_runtime_support import (
    E2E_WORKFLOW,
    Any,
    BenchmarkResult,
    SimpleNamespace,
    _build_args,
    pytest,
    run_parallel_benchmark,
)


def test_build_artifact_metadata_prefers_github_environment() -> None:
    # Given: Inputs and test doubles are prepared for build artifact metadata prefers github environment.
    # When: The benchmark runtime reporting path is exercised for build artifact metadata prefers github environment.
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

    # Then: The expected build artifact metadata prefers github environment behavior is asserted.
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
        "runner_interface": "cli",
    }


def test_build_artifact_metadata_records_tui_runner_interface() -> None:
    # Given: Inputs and test doubles are prepared for build artifact metadata records tui runner interface.
    # When: The benchmark runtime reporting path is exercised for build artifact metadata records tui runner interface.
    metadata = run_parallel_benchmark._build_artifact_metadata(
        output_path="benchmarks/results/tui.json",
        environ={"PYRALLEL_BENCHMARK_RUNNER_INTERFACE": "tui"},
    )

    # Then: The expected build artifact metadata records tui runner interface behavior is asserted.
    assert metadata["runner_interface"] == "tui"


def test_run_benchmark_writes_artifact_metadata(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark writes artifact metadata.
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

    # When: The benchmark runtime reporting path is exercised for run benchmark writes artifact metadata.
    run_parallel_benchmark.run_benchmark(
        _build_args(skip_async=True, skip_process=True),
        raw_argv=["--skip-async", "--skip-process"],
    )

    # Then: The expected run benchmark writes artifact metadata behavior is asserted.
    assert captured["artifact_metadata"] == {
        "artifact_path": "benchmarks/results/test-runtime.json",
        "git_commit_sha": "deadbeef",
    }


def test_e2e_workflow_runs_monitoring_smoke_as_test_code() -> None:
    # Given: Inputs and test doubles are prepared for e2e workflow runs monitoring smoke as test code.
    # When: The benchmark runtime reporting path is exercised for e2e workflow runs monitoring smoke as test code.
    text = E2E_WORKFLOW.read_text(encoding="utf-8")

    # Then: The expected e2e workflow runs monitoring smoke as test code behavior is asserted.
    assert "kafka-1 kafka-exporter prometheus grafana" in text
    assert "uv run pytest tests/e2e -q" in text
    assert "actions/upload-artifact@v7" in text
    assert "path: .artifacts/e2e-junit*.xml" in text


def test_benchmark_stats_summary_carries_process_transport_mode() -> None:
    # Given: Inputs and test doubles are prepared for benchmark stats summary carries process transport mode.
    stats = run_parallel_benchmark.BenchmarkStats(
        run_name="process-run",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        process_transport_mode="worker_pipes",
        target_messages=1,
    )

    # When: The benchmark runtime reporting path is exercised for benchmark stats summary carries process transport mode.
    stats.start()
    # Then: The expected benchmark stats summary carries process transport mode behavior is asserted.
    assert stats._start_time is not None
    stats.record(0.001, completed_at=stats._start_time + 0.001)
    stats.stop()

    summary = stats.summary()

    assert summary.process_transport_mode == "worker_pipes"


def test_benchmark_stats_summary_carries_route_batch_size() -> None:
    # Given: Inputs and test doubles are prepared for benchmark stats summary carries route batch size.
    stats = run_parallel_benchmark.BenchmarkStats(
        run_name="process-run",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        route_batch_size=64,
        target_messages=1,
    )

    # When: The benchmark runtime reporting path is exercised for benchmark stats summary carries route batch size.
    stats.start()
    # Then: The expected benchmark stats summary carries route batch size behavior is asserted.
    assert stats._start_time is not None
    stats.record(0.001, completed_at=stats._start_time + 0.001)
    stats.stop()

    summary = stats.summary()

    assert summary.route_batch_size == 64


def test_benchmark_stats_summary_carries_runtime_ipc_metrics() -> None:
    # Given: Inputs and test doubles are prepared for benchmark stats summary carries runtime ipc metrics.
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

    # When: The benchmark runtime reporting path is exercised for benchmark stats summary carries runtime ipc metrics.
    stats.start()
    # Then: The expected benchmark stats summary carries runtime ipc metrics behavior is asserted.
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
