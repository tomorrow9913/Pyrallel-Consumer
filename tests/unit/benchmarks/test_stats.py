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
