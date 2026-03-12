from __future__ import annotations

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.state import BenchmarkTuiState


def test_controller_uses_all_mode_when_multiple_workloads_selected() -> None:
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep", "cpu")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    assert controller._parser._workload_mode == "all"


def test_controller_uses_single_workload_mode_when_one_workload_selected() -> None:
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep",)),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    assert controller._parser._workload_mode == "sleep"


def test_controller_passes_ordering_modes_into_parser() -> None:
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(ordering_modes=("key_hash", "unordered")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    assert controller._parser._active_orderings == ("key_hash", "unordered")


def test_controller_passes_exact_workload_subset_into_parser() -> None:
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep", "cpu")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    assert controller._parser._active_workloads == ("sleep", "cpu")
    assert controller._parser.snapshot.total_runs == 6
