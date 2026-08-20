from __future__ import annotations

import asyncio

import pytest

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.state import BenchmarkTuiState


def test_controller_uses_all_mode_when_multiple_workloads_selected() -> None:
    # Given: inputs for `controller uses all mode when multiple worklo...` are prepared.
    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep", "cpu")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller uses all mode when multiple worklo...` behavior is asserted.
    assert controller._parser._workload_mode == "all"


def test_controller_uses_single_workload_mode_when_one_workload_selected() -> None:
    # Given: inputs for `controller uses single workload mode when one...` are prepared.
    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep",)),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller uses single workload mode when one...` behavior is asserted.
    assert controller._parser._workload_mode == "sleep"


def test_controller_passes_ordering_modes_into_parser() -> None:
    # Given: inputs for `controller passes ordering modes into parser` are prepared.
    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(ordering_modes=("key_hash", "unordered")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller passes ordering modes into parser` behavior is asserted.
    assert controller._parser._active_orderings == ("key_hash", "unordered")


def test_controller_passes_exact_workload_subset_into_parser() -> None:
    # Given: inputs for `controller passes exact workload subset into...` are prepared.
    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep", "cpu")),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller passes exact workload subset into...` behavior is asserted.
    assert controller._parser._active_workloads == ("sleep", "cpu")
    assert controller._parser.snapshot.total_runs == 6


def test_controller_passes_custom_workload_subset_into_parser() -> None:
    # Given: inputs for `controller passes custom workload subset into...` are prepared.
    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("custom",)),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller passes custom workload subset into...` behavior is asserted.
    assert controller._parser._active_workloads == ("custom",)
    assert controller._parser.snapshot.total_runs == 3


def test_controller_empty_workload_fallback_uses_registry_default(monkeypatch) -> None:
    # Given: inputs for `controller empty workload fallback uses regis...` are prepared.
    import benchmarks.tui.controller as controller_module

    monkeypatch.setattr(controller_module, "default_workloads", lambda: ("custom",))

    # When: the benchmark TUI controller code path is exercised.
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=()),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=lambda _code: None,
    )

    # Then: the expected `controller empty workload fallback uses regis...` behavior is asserted.
    assert controller._parser._active_workloads == ("custom",)


@pytest.mark.asyncio
async def test_controller_marks_child_cli_process_as_tui_runner(monkeypatch) -> None:
    # Given: inputs for `controller marks child cli process as tui runner` are prepared.
    import benchmarks.tui.controller as controller_module

    captured: dict[str, object] = {}

    class _Process:
        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode = 0
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        async def wait(self) -> int:
            return 0

    async def _create_subprocess_exec(*argv: str, **kwargs: object) -> _Process:
        captured["argv"] = argv
        captured["env"] = kwargs["env"]
        return _Process()

    monkeypatch.setattr(
        controller_module.asyncio,
        "create_subprocess_exec",
        _create_subprocess_exec,
    )

    completed: list[int] = []
    controller = BenchmarkProcessController(
        state=BenchmarkTuiState(workloads=("sleep",)),
        on_output=lambda _line, _is_error: None,
        on_progress=lambda _snapshot: None,
        on_complete=completed.append,
    )

    # When: the benchmark TUI controller code path is exercised.
    await controller.run()

    # Then: the expected `controller marks child cli process as tui runner` behavior is asserted.
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["PYRALLEL_BENCHMARK_RUNNER_INTERFACE"] == "tui"
    assert completed == [0]
