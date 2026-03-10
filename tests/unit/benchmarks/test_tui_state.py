from __future__ import annotations

from benchmarks.tui.state import BenchmarkTuiState


def test_tui_state_to_argv_uses_cli_defaults() -> None:
    state = BenchmarkTuiState()

    argv = state.to_argv()

    assert argv[:8] == [
        "--bootstrap-servers",
        "localhost:9092",
        "--num-messages",
        "100000",
        "--num-keys",
        "100",
        "--num-partitions",
        "8",
    ]
    assert "--workload" in argv
    assert "--skip-reset" not in argv
    assert "--py-spy" not in argv


def test_tui_state_to_argv_includes_advanced_flags() -> None:
    state = BenchmarkTuiState(
        workload="all",
        skip_reset=True,
        profile=True,
        profile_top_n=15,
        py_spy=True,
        py_spy_format="speedscope",
        py_spy_native=True,
        py_spy_idle=True,
        skip_process=True,
    )

    argv = state.to_argv()

    assert "--workload" in argv
    assert "all" in argv
    assert "--skip-reset" in argv
    assert "--profile" in argv
    assert "--profile-top-n" in argv
    assert "15" in argv
    assert "--py-spy" in argv
    assert "--py-spy-format" in argv
    assert "speedscope" in argv
    assert "--py-spy-native" in argv
    assert "--py-spy-idle" in argv
    assert "--skip-process" in argv
