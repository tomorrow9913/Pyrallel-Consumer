from __future__ import annotations

from collections import deque

from benchmarks.tui.log_parser import BenchmarkLogParser


def test_log_parser_tracks_ordering_combinations_in_summary_rows() -> None:
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "partition"),
    )

    parser.consume(
        "sleep-key_hash-baseline | baseline | demo-sleep-key_hash-baseline | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-partition-pyrallel-async | pyrallel | demo-sleep-partition-async | 100 | 20.00 | 1.000 | 1.000"
    )

    snapshot = parser.snapshot
    assert snapshot.total_runs == 18
    assert snapshot.current_workload == "sleep"
    assert snapshot.current_ordering == "partition"
    assert snapshot.tps_by_workload_ordering["sleep"]["key_hash"]["baseline"] == "10.00"
    assert snapshot.tps_by_workload_ordering["sleep"]["partition"]["async"] == "20.00"


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


def test_log_parser_advances_progress_on_run_start_before_results() -> None:
    parser = BenchmarkLogParser(workload_mode="all")

    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")

    snapshot = parser.snapshot
    assert snapshot.progress_value > 0
    assert snapshot.completed_runs == 0
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "--"


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
    assert snapshot.completed_runs == 3
    assert snapshot.total_runs == 9
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "10.00"
    assert snapshot.tps_by_workload["sleep"]["async"] == "10.00"
    assert snapshot.tps_by_workload["sleep"]["process"] == "10.00"


def test_log_parser_tracks_tps_table_for_single_workload_mode() -> None:
    parser = BenchmarkLogParser(workload_mode="cpu")

    parser.consume("baseline | baseline | topic-a | 100 | 11.11 | 1.000 | 1.000")
    parser.consume("pyrallel-async | pyrallel | topic-b | 100 | 22.22 | 1.000 | 1.000")

    snapshot = parser.snapshot
    assert snapshot.completed_runs == 2
    assert snapshot.total_runs == 3
    assert snapshot.tps_by_workload["cpu"]["baseline"] == "11.11"
    assert snapshot.tps_by_workload["cpu"]["async"] == "22.22"
    assert snapshot.tps_by_workload["cpu"]["process"] == "--"


def test_log_parser_assigns_final_tps_to_earliest_started_run_when_logs_interleave() -> (
    None
):
    parser = BenchmarkLogParser(workload_mode="sleep")

    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-async'.")
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1386.01"
    assert snapshot.tps_by_workload["sleep"]["async"] == "2500.50"


def test_log_parser_handles_realistic_baseline_completion_sequence_before_async() -> (
    None
):
    parser = BenchmarkLogParser(workload_mode="all")

    lines = [
        "Starting baseline consumer for topic 'pyrallel-benchmark-sleep-baseline'.",
        "Target messages to process: 100000",
        "Timeout: 600s",
        "Reached target of 100000 messages. Committing final offsets.",
        "Committing final offsets and closing consumer...",
        "Consumer closed. Total messages processed (approx): 100000",
        "Total runtime: 7.21 seconds",
        "Final TPS: 1386.01",
        "Starting PyrallelConsumer test for topic 'pyrallel-benchmark-sleep-async'.",
    ]

    for line in lines:
        parser.consume(line)

    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1386.01"
    assert snapshot.phase_statuses["baseline"] == "completed"
    assert snapshot.phase_statuses["async"] == "running"


def test_log_parser_maps_baseline_result_row_in_all_workload_mode_using_topic_name() -> (
    None
):
    parser = BenchmarkLogParser(workload_mode="all")

    parser.consume(
        "baseline | baseline | pyrallel-benchmark-sleep-baseline | 10000 | 1109.49 | 0.866 | 3.128"
    )

    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1109.49"


def test_log_parser_preserves_partial_workload_subset_in_total_runs() -> None:
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("sleep", "cpu"),
    )

    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")

    snapshot = parser.snapshot
    assert snapshot.total_runs == 6
    assert snapshot.progress_value == 0.5


def test_log_parser_counts_ordering_modes_in_total_runs() -> None:
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "unordered"),
    )

    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-unordered-baseline'."
    )

    snapshot = parser.snapshot
    assert snapshot.total_runs == 18
    assert snapshot.progress_value == 0.5
    assert snapshot.completed_runs == 0


def test_log_parser_tracks_ordering_aware_tps_rows_from_results_table() -> None:
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "unordered"),
    )

    parser.consume(
        "sleep-unordered-baseline | baseline | unordered | topic-a | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-unordered-pyrallel-async | pyrallel | unordered | topic-b | 100 | 20.00 | 1.000 | 1.000"
    )

    snapshot = parser.snapshot
    assert snapshot.completed_runs == 2
    assert snapshot.total_runs == 18
    assert (
        snapshot.tps_by_workload_ordering["sleep"]["unordered"]["baseline"] == "10.00"
    )
    assert snapshot.tps_by_workload_ordering["sleep"]["unordered"]["async"] == "20.00"


def test_log_parser_assigns_interleaved_final_tps_by_workload_ordering_start_order() -> (
    None
):
    parser = BenchmarkLogParser(
        workload_mode="sleep",
        active_orderings=("key_hash", "unordered"),
    )

    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-key_hash-baseline'."
    )
    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-unordered-baseline'."
    )
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    snapshot = parser.snapshot
    assert (
        snapshot.tps_by_workload_ordering["sleep"]["key_hash"]["baseline"] == "1386.01"
    )
    assert (
        snapshot.tps_by_workload_ordering["sleep"]["unordered"]["baseline"] == "2500.50"
    )


def test_log_parser_tracks_strict_mode_variants_as_distinct_runs() -> None:
    parser = BenchmarkLogParser(workload_mode="sleep")

    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-on'."
    )
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-off'."
    )
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    assert parser.snapshot.completed_runs == 2
    assert parser._completed_runs == {
        ("sleep", "key_hash", "on", "async"),
        ("sleep", "key_hash", "off", "async"),
    }
    assert parser.snapshot.total_runs == 4
    assert parser._started_run_order == deque()


def test_log_parser_detects_strict_suffix_phases_from_topic_names() -> None:
    parser = BenchmarkLogParser(workload_mode="sleep")

    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-off'."
    )

    assert parser.snapshot.phase_statuses["async"] == "running"
    assert parser._started_run_order[0] == ("sleep", "key_hash", "off", "async")

    parser.consume("Final TPS: 1386.01")
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-process-strict-off'."
    )

    assert parser.snapshot.phase_statuses["process"] == "running"
    assert parser._started_run_order[0] == (
        "sleep",
        "key_hash",
        "off",
        "process",
    )

    parser.consume("Final TPS: 2500.50")

    assert parser.snapshot.completed_runs == 2
    assert parser.snapshot.tps_by_workload["sleep"]["async"] == "1386.01"
    assert parser.snapshot.tps_by_workload["sleep"]["process"] == "2500.50"


def test_log_parser_counts_strict_result_rows_as_distinct_completed_runs() -> None:
    parser = BenchmarkLogParser(workload_mode="sleep")

    parser.consume(
        "sleep-key_hash-async-strict-on | pyrallel | strict-on | topic-a | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-key_hash-async-strict-off | pyrallel | strict-off | topic-b | 100 | 20.00 | 1.000 | 1.000"
    )

    assert parser.snapshot.completed_runs == 2
    assert parser.snapshot.total_runs == 4
