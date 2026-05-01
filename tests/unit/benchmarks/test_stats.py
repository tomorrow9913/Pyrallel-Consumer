from __future__ import annotations

import json

from benchmarks.stats import BenchmarkResult, BenchmarkStats, write_results_json


def test_benchmark_stats_summary_reports_windowed_tps_percentiles() -> None:
    stats = BenchmarkStats(
        run_name="demo",
        run_type="baseline",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
    )
    stats.start()
    stats._start_time = 0.0

    for _ in range(100):
        stats.record(0.001, completed_at=1.0)
    for _ in range(100):
        stats.record(0.002, completed_at=3.0)
    for _ in range(100):
        stats.record(0.003, completed_at=4.0)
    stats.stop()

    summary = stats.summary()

    assert summary.window_size_messages == 100
    assert summary.tps_min_window == 50.0
    assert summary.tps_p10_window == 60.0
    assert summary.tps_p50_window == 100.0


def test_benchmark_stats_summary_omits_windowed_tps_when_too_few_messages() -> None:
    stats = BenchmarkStats(
        run_name="demo",
        run_type="baseline",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
    )
    stats.start()

    for _ in range(99):
        stats.record(0.001, completed_at=1.0)
    stats.stop()

    summary = stats.summary()

    assert summary.window_size_messages == 100
    assert summary.tps_p50_window is None
    assert summary.tps_p10_window is None
    assert summary.tps_min_window is None


def test_benchmark_stats_summary_includes_release_gate_lag_gap_evidence() -> None:
    stats = BenchmarkStats(
        run_name="demo",
        run_type="async",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
    )
    stats.start()
    stats.record(0.001, completed_at=1.0)
    stats.record_release_gate_observation(
        elapsed_sec=5.0,
        consumer_parallel_lag=2,
        consumer_gap_count=1,
    )
    stats.record_release_gate_observation(
        elapsed_sec=10.0,
        consumer_parallel_lag=0,
        consumer_gap_count=0,
    )

    summary = stats.summary()

    assert summary.final_lag == 0
    assert summary.final_gap_count == 0
    assert summary.metrics_observations == [
        {
            "elapsed_sec": 5.0,
            "consumer_parallel_lag": 2,
            "consumer_gap_count": 1,
        },
        {
            "elapsed_sec": 10.0,
            "consumer_parallel_lag": 0,
            "consumer_gap_count": 0,
        },
    ]


def test_write_results_json_includes_performance_improvement_analysis(tmp_path) -> None:
    output_path = tmp_path / "summary.json"

    write_results_json(
        [
            BenchmarkResult(
                run_name="sleep-key_hash-baseline",
                run_type="baseline",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-baseline",
                messages_processed=100,
                total_time_sec=2.0,
                throughput_tps=50.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-async-adaptive-off",
                run_type="async",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-async-adaptive-off",
                messages_processed=100,
                total_time_sec=1.0,
                throughput_tps=100.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-async-adaptive-on",
                run_type="async",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-async-adaptive-on",
                messages_processed=100,
                total_time_sec=0.8,
                throughput_tps=125.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
        ],
        output_path,
        options={"adaptive_concurrency": ["off", "on"]},
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["performance_improvements"] == [
        {
            "comparison": "adaptive_on_vs_off",
            "workload": "sleep",
            "ordering": "key_hash",
            "run_type": "async",
            "candidate_run_name": "sleep-key_hash-pyrallel-async-adaptive-on",
            "reference_run_name": "sleep-key_hash-pyrallel-async-adaptive-off",
            "candidate_throughput_tps": 125.0,
            "reference_throughput_tps": 100.0,
            "throughput_tps_delta": 25.0,
            "throughput_tps_delta_pct": 25.0,
            "improvement_ratio": 1.25,
        },
        {
            "comparison": "best_pyrallel_vs_baseline",
            "workload": "sleep",
            "ordering": "key_hash",
            "run_type": "async",
            "candidate_run_name": "sleep-key_hash-pyrallel-async-adaptive-on",
            "reference_run_name": "sleep-key_hash-baseline",
            "candidate_throughput_tps": 125.0,
            "reference_throughput_tps": 50.0,
            "throughput_tps_delta": 75.0,
            "throughput_tps_delta_pct": 150.0,
            "improvement_ratio": 2.5,
        },
    ]


def test_write_results_json_marks_improvement_percent_unknown_for_zero_reference(
    tmp_path,
) -> None:
    output_path = tmp_path / "summary.json"

    write_results_json(
        [
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-process-adaptive-off",
                run_type="process",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-process-adaptive-off",
                messages_processed=0,
                total_time_sec=0.0,
                throughput_tps=0.0,
                avg_processing_ms=0.0,
                p99_processing_ms=0.0,
            ),
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-process-adaptive-on",
                run_type="process",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-process-adaptive-on",
                messages_processed=10,
                total_time_sec=1.0,
                throughput_tps=10.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
        ],
        output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    analysis = payload["performance_improvements"][0]

    assert analysis["throughput_tps_delta"] == 10.0
    assert analysis["throughput_tps_delta_pct"] is None
    assert analysis["improvement_ratio"] is None


def test_adaptive_improvements_match_full_run_variant_key(tmp_path) -> None:
    output_path = tmp_path / "summary.json"

    write_results_json(
        [
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-async-strict-on-adaptive-off",
                run_type="async",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-async-strict-on-adaptive-off",
                messages_processed=100,
                total_time_sec=2.0,
                throughput_tps=50.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-async-strict-off-adaptive-off",
                run_type="async",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-async-strict-off-adaptive-off",
                messages_processed=100,
                total_time_sec=1.0,
                throughput_tps=100.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
            BenchmarkResult(
                run_name="sleep-key_hash-pyrallel-async-strict-on-adaptive-on",
                run_type="async",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-async-strict-on-adaptive-on",
                messages_processed=100,
                total_time_sec=1.0,
                throughput_tps=75.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            ),
        ],
        output_path,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    adaptive_analysis = [
        row
        for row in payload["performance_improvements"]
        if row["comparison"] == "adaptive_on_vs_off"
    ]

    assert adaptive_analysis == [
        {
            "comparison": "adaptive_on_vs_off",
            "workload": "sleep",
            "ordering": "key_hash",
            "run_type": "async",
            "candidate_run_name": "sleep-key_hash-pyrallel-async-strict-on-adaptive-on",
            "reference_run_name": "sleep-key_hash-pyrallel-async-strict-on-adaptive-off",
            "candidate_throughput_tps": 75.0,
            "reference_throughput_tps": 50.0,
            "throughput_tps_delta": 25.0,
            "throughput_tps_delta_pct": 50.0,
            "improvement_ratio": 1.5,
        }
    ]


def test_write_results_json_preserves_artifact_metadata(tmp_path) -> None:
    output_path = tmp_path / "summary.json"
    artifact_metadata = {
        "generated_at_utc": "2026-04-25T05:00:00Z",
        "git_commit_sha": "abc123",
        "git_ref_name": "develop",
        "git_ref_type": "branch",
        "github_run_id": "12345",
        "artifact_name": "release-gate-develop-12345",
    }

    write_results_json(
        [
            BenchmarkResult(
                run_name="sleep-key_hash-baseline",
                run_type="baseline",
                workload="sleep",
                ordering="key_hash",
                topic="demo-sleep-key_hash-baseline",
                messages_processed=100,
                total_time_sec=2.0,
                throughput_tps=50.0,
                avg_processing_ms=1.0,
                p99_processing_ms=2.0,
            )
        ],
        output_path,
        artifact_metadata=artifact_metadata,
    )

    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["artifact_metadata"] == artifact_metadata
