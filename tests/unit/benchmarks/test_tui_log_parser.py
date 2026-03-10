from __future__ import annotations

from benchmarks.tui.log_parser import BenchmarkLogParser


def test_log_parser_tracks_workload_phase_progress() -> None:
    parser = BenchmarkLogParser(workload_mode="all")

    parser.consume(
        "Resetting benchmark topics/groups: demo-sleep-baseline | groups=baseline"
    )
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-async'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-process'.")
    parser.consume("JSON summary written to benchmarks/results/demo.json")

    snapshot = parser.snapshot
    assert snapshot.status_message == "JSON summary written"
    assert snapshot.current_workload == "sleep"
    assert snapshot.phase_statuses["baseline"] == "completed"
    assert snapshot.phase_statuses["async"] == "completed"
    assert snapshot.phase_statuses["process"] == "running"
    assert snapshot.output_path == "benchmarks/results/demo.json"


def test_log_parser_marks_all_workloads_complete_from_result_rows() -> None:
    parser = BenchmarkLogParser(workload_mode="all")

    parser.consume("sleep-baseline | baseline | topic-a | 100 | 10.00 | 1.000 | 1.000")
    parser.consume(
        "sleep-pyrallel-async | pyrallel | topic-b | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-pyrallel-process | pyrallel | topic-c | 100 | 10.00 | 1.000 | 1.000"
    )

    snapshot = parser.snapshot
    assert snapshot.workload_statuses["sleep"] == "completed"
