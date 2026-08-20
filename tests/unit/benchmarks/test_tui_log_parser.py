from __future__ import annotations

from collections import deque

from benchmarks.tui.log_parser import BenchmarkLogParser


def test_log_parser_tracks_ordering_combinations_in_summary_rows() -> None:
    # Given: inputs for `log parser tracks ordering combinations in su...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "partition"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "sleep-key_hash-baseline | baseline | demo-sleep-key_hash-baseline | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-partition-pyrallel-async | pyrallel | demo-sleep-partition-async | 100 | 20.00 | 1.000 | 1.000"
    )

    # Then: the expected `log parser tracks ordering combinations in su...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.total_runs == 18
    assert snapshot.current_workload == "sleep"
    assert snapshot.current_ordering == "partition"
    assert snapshot.tps_by_workload_ordering["sleep"]["key_hash"]["baseline"] == "10.00"
    assert snapshot.tps_by_workload_ordering["sleep"]["partition"]["async"] == "20.00"


def test_log_parser_tracks_workload_phase_progress() -> None:
    # Given: inputs for `log parser tracks workload phase progress` are prepared.
    parser = BenchmarkLogParser(workload_mode="all")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Resetting benchmark topics/groups: demo-sleep-baseline | groups=baseline"
    )
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-async'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-process'.")
    parser.consume("JSON summary written to benchmarks/results/demo.json")

    # Then: the expected `log parser tracks workload phase progress` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.status_message == "JSON summary written"
    assert snapshot.current_workload == "sleep"
    assert snapshot.phase_statuses["baseline"] == "completed"
    assert snapshot.phase_statuses["async"] == "completed"
    assert snapshot.phase_statuses["process"] == "running"
    assert snapshot.output_path == "benchmarks/results/demo.json"


def test_log_parser_advances_progress_on_run_start_before_results() -> None:
    # Given: inputs for `log parser advances progress on run start bef...` are prepared.
    parser = BenchmarkLogParser(workload_mode="all")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")

    # Then: the expected `log parser advances progress on run start bef...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.progress_value > 0
    assert snapshot.completed_runs == 0
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "--"


def test_log_parser_marks_all_workloads_complete_from_result_rows() -> None:
    # Given: inputs for `log parser marks all workloads complete from...` are prepared.
    parser = BenchmarkLogParser(workload_mode="all")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("sleep-baseline | baseline | topic-a | 100 | 10.00 | 1.000 | 1.000")
    parser.consume(
        "sleep-pyrallel-async | pyrallel | topic-b | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-pyrallel-process | pyrallel | topic-c | 100 | 10.00 | 1.000 | 1.000"
    )

    # Then: the expected `log parser marks all workloads complete from...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.workload_statuses["sleep"] == "completed"
    assert snapshot.completed_runs == 3
    assert snapshot.total_runs == 9
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "10.00"
    assert snapshot.tps_by_workload["sleep"]["async"] == "10.00"
    assert snapshot.tps_by_workload["sleep"]["process"] == "10.00"


def test_log_parser_tracks_tps_table_for_single_workload_mode() -> None:
    # Given: inputs for `log parser tracks tps table for single worklo...` are prepared.
    parser = BenchmarkLogParser(workload_mode="cpu")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("baseline | baseline | topic-a | 100 | 11.11 | 1.000 | 1.000")
    parser.consume("pyrallel-async | pyrallel | topic-b | 100 | 22.22 | 1.000 | 1.000")

    # Then: the expected `log parser tracks tps table for single worklo...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.completed_runs == 2
    assert snapshot.total_runs == 3
    assert snapshot.tps_by_workload["cpu"]["baseline"] == "11.11"
    assert snapshot.tps_by_workload["cpu"]["async"] == "22.22"
    assert snapshot.tps_by_workload["cpu"]["process"] == "--"


def test_log_parser_assigns_final_tps_to_earliest_started_run_when_logs_interleave() -> (
    None
):
    # Given: inputs for `log parser assigns final tps to earliest star...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-async'.")
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    # Then: the expected `log parser assigns final tps to earliest star...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1386.01"
    assert snapshot.tps_by_workload["sleep"]["async"] == "2500.50"


def test_log_parser_handles_realistic_baseline_completion_sequence_before_async() -> (
    None
):
    # Given: inputs for `log parser handles realistic baseline complet...` are prepared.
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

    # When: the benchmark TUI log parser code path is exercised.
    for line in lines:
        parser.consume(line)

    # Then: the expected `log parser handles realistic baseline complet...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1386.01"
    assert snapshot.phase_statuses["baseline"] == "completed"
    assert snapshot.phase_statuses["async"] == "running"


def test_log_parser_maps_baseline_result_row_in_all_workload_mode_using_topic_name() -> (
    None
):
    # Given: inputs for `log parser maps baseline result row in all wo...` are prepared.
    parser = BenchmarkLogParser(workload_mode="all")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "baseline | baseline | pyrallel-benchmark-sleep-baseline | 10000 | 1109.49 | 0.866 | 3.128"
    )

    # Then: the expected `log parser maps baseline result row in all wo...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.tps_by_workload["sleep"]["baseline"] == "1109.49"


def test_log_parser_preserves_partial_workload_subset_in_total_runs() -> None:
    # Given: inputs for `log parser preserves partial workload subset...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("sleep", "cpu"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")

    # Then: the expected `log parser preserves partial workload subset...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.total_runs == 6
    assert snapshot.progress_value == 0.5


def test_log_parser_extracts_custom_active_workload() -> None:
    # Given: inputs for `log parser extracts custom active workload` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("custom",),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "custom-baseline | baseline | demo-custom-baseline | 100 | 12.34 | 1.000 | 1.000"
    )

    # Then: the expected `log parser extracts custom active workload` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.total_runs == 3
    assert snapshot.current_workload == "custom"
    assert snapshot.tps_by_workload["custom"]["baseline"] == "12.34"


def test_log_parser_keeps_separator_aware_custom_workload_matching() -> None:
    # Given: inputs for `log parser keeps separator aware custom workl...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("io", "custom"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "customize-baseline | baseline | demo-customize-baseline | 100 | 12.34 | 1.000 | 1.000"
    )

    # Then: the expected `log parser keeps separator aware custom workl...` behavior is asserted.
    assert parser.snapshot.current_workload is None
    assert parser.snapshot.completed_runs == 0


def test_log_parser_ignores_workload_names_in_topic_prefix() -> None:
    # Given: inputs for `log parser ignores workload names in topic pr...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("cpu", "sleep"),
        active_orderings=("key_hash",),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting baseline consumer for topic 'demo-cpu-sleep-key_hash-baseline'."
    )

    # Then: the expected `log parser ignores workload names in topic pr...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.current_workload == "sleep"
    assert snapshot.workload_statuses["sleep"] == "running"
    assert snapshot.workload_statuses["cpu"] == "pending"


def test_log_parser_uses_suffix_workload_when_prefix_contains_later_workload() -> None:
    # Given: inputs for `log parser uses suffix workload when prefix c...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_workloads=("cpu", "sleep"),
        active_orderings=("key_hash",),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-cpu-key_hash-async'."
    )

    # Then: the expected `log parser uses suffix workload when prefix c...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.current_workload == "cpu"
    assert snapshot.phase_statuses["async"] == "running"
    assert snapshot.workload_statuses["sleep"] == "pending"


def test_log_parser_counts_ordering_modes_in_total_runs() -> None:
    # Given: inputs for `log parser counts ordering modes in total runs` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "unordered"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-unordered-baseline'."
    )

    # Then: the expected `log parser counts ordering modes in total runs` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.total_runs == 18
    assert snapshot.progress_value == 0.5
    assert snapshot.completed_runs == 0


def test_log_parser_tracks_ordering_aware_tps_rows_from_results_table() -> None:
    # Given: inputs for `log parser tracks ordering aware tps rows fro...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="all",
        active_orderings=("key_hash", "unordered"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "sleep-unordered-baseline | baseline | unordered | topic-a | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-unordered-pyrallel-async | pyrallel | unordered | topic-b | 100 | 20.00 | 1.000 | 1.000"
    )

    # Then: the expected `log parser tracks ordering aware tps rows fro...` behavior is asserted.
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
    # Given: inputs for `log parser assigns interleaved final tps by w...` are prepared.
    parser = BenchmarkLogParser(
        workload_mode="sleep",
        active_orderings=("key_hash", "unordered"),
    )

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-key_hash-baseline'."
    )
    parser.consume(
        "Starting baseline consumer for topic 'demo-sleep-unordered-baseline'."
    )
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    # Then: the expected `log parser assigns interleaved final tps by w...` behavior is asserted.
    snapshot = parser.snapshot
    assert (
        snapshot.tps_by_workload_ordering["sleep"]["key_hash"]["baseline"] == "1386.01"
    )
    assert (
        snapshot.tps_by_workload_ordering["sleep"]["unordered"]["baseline"] == "2500.50"
    )


def test_log_parser_tracks_strict_mode_variants_as_distinct_runs() -> None:
    # Given: inputs for `log parser tracks strict mode variants as dis...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-on'."
    )
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-off'."
    )
    parser.consume("Final TPS: 1386.01")
    parser.consume("Final TPS: 2500.50")

    # Then: the expected `log parser tracks strict mode variants as dis...` behavior is asserted.
    assert parser.snapshot.completed_runs == 2
    assert parser._completed_runs == {
        ("sleep", "key_hash", "on", "async"),
        ("sleep", "key_hash", "off", "async"),
    }
    assert parser.snapshot.total_runs == 4
    assert parser._started_run_order == deque()


def test_log_parser_detects_strict_suffix_phases_from_topic_names() -> None:
    # Given: inputs for `log parser detects strict suffix phases from...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "Starting PyrallelConsumer test for topic 'demo-sleep-key_hash-async-strict-off'."
    )

    # Then: the expected `log parser detects strict suffix phases from...` behavior is asserted.
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
    # Given: inputs for `log parser counts strict result rows as disti...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume(
        "sleep-key_hash-async-strict-on | pyrallel | strict-on | topic-a | 100 | 10.00 | 1.000 | 1.000"
    )
    parser.consume(
        "sleep-key_hash-async-strict-off | pyrallel | strict-off | topic-b | 100 | 20.00 | 1.000 | 1.000"
    )

    # Then: the expected `log parser counts strict result rows as disti...` behavior is asserted.
    assert parser.snapshot.completed_runs == 2
    assert parser.snapshot.total_runs == 4


def test_log_parser_tracks_current_run_message_progress_from_logs() -> None:
    # Given: inputs for `log parser tracks current run message progres...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("Starting baseline consumer for topic 'demo-sleep-baseline'.")
    parser.consume(
        "Will process up to 100000 messages if specified, otherwise indefinitely."
    )
    parser.consume("Processed 100 messages. Committed offsets. Current TPS: 500.00")

    # Then: the expected `log parser tracks current run message progres...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.current_run_target_messages == 100000
    assert snapshot.current_run_processed_messages == 100


def test_log_parser_tracks_pyrallel_current_run_target_and_final_processed() -> None:
    # Given: inputs for `log parser tracks pyrallel current run target...` are prepared.
    parser = BenchmarkLogParser(workload_mode="sleep")

    # When: the benchmark TUI log parser code path is exercised.
    parser.consume("Starting PyrallelConsumer test for topic 'demo-sleep-async'.")
    parser.consume("Target messages to process: 50000")
    parser.consume("Processed 1000 messages. Current TPS: 1200.00")
    parser.consume("Total messages processed: 50000")

    # Then: the expected `log parser tracks pyrallel current run target...` behavior is asserted.
    snapshot = parser.snapshot
    assert snapshot.current_run_target_messages == 50000
    assert snapshot.current_run_processed_messages == 50000
