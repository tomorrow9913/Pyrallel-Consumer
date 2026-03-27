from __future__ import annotations

from benchmarks.stats import BenchmarkStats


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
