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
    assert "--workloads" in argv
    assert "sleep" in argv
    assert "--order" in argv
    assert "key_hash" in argv
    assert "--skip-reset" not in argv
    assert "--py-spy" not in argv


def test_tui_state_to_argv_includes_advanced_flags() -> None:
    state = BenchmarkTuiState(
        workloads=("sleep", "cpu"),
        ordering_modes=("key_hash", "partition"),
        profiling_enabled=True,
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

    assert "--workloads" in argv
    assert "sleep,cpu" in argv
    assert "--order" in argv
    assert "key_hash,partition" in argv
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


def test_tui_state_to_argv_omits_profiling_flags_when_master_toggle_disabled() -> None:
    state = BenchmarkTuiState(
        profile=True,
        profile_dir="custom/profiles",
        profile_clock="cpu",
        profile_top_n=15,
        profile_threads=True,
        profile_greenlets=True,
        profile_process_workers=True,
        py_spy=True,
        py_spy_format="speedscope",
        py_spy_output="custom/pyspy",
        py_spy_rate=250,
        py_spy_native=True,
        py_spy_idle=True,
        py_spy_top=True,
    )

    argv = state.to_argv()

    assert "--profile" not in argv
    assert "--profile-dir" not in argv
    assert "--profile-clock" not in argv
    assert "--profile-top-n" not in argv
    assert "--profile-threads" not in argv
    assert "--profile-greenlets" not in argv
    assert "--profile-process-workers" not in argv
    assert "--py-spy" not in argv
    assert "--py-spy-format" not in argv
    assert "--py-spy-output" not in argv
    assert "--py-spy-rate" not in argv
    assert "--py-spy-native" not in argv
    assert "--py-spy-idle" not in argv
    assert "--py-spy-top" not in argv


def test_tui_state_to_argv_restores_profiling_flags_when_master_toggle_enabled() -> (
    None
):
    state = BenchmarkTuiState(
        profiling_enabled=True,
        profile=True,
        profile_dir="custom/profiles",
        profile_clock="cpu",
        profile_top_n=15,
        profile_threads=True,
        profile_greenlets=True,
        profile_process_workers=True,
        py_spy=True,
        py_spy_format="speedscope",
        py_spy_output="custom/pyspy",
        py_spy_rate=250,
        py_spy_native=True,
        py_spy_idle=True,
        py_spy_top=True,
    )

    argv = state.to_argv()

    assert "--profile" in argv
    assert "--profile-dir" in argv
    assert "custom/profiles" in argv
    assert "--profile-clock" in argv
    assert "cpu" in argv
    assert "--profile-top-n" in argv
    assert "15" in argv
    assert "--profile-threads" in argv
    assert "--profile-greenlets" in argv
    assert "--profile-process-workers" in argv
    assert "--py-spy" in argv
    assert "--py-spy-format" in argv
    assert "speedscope" in argv
    assert "--py-spy-output" in argv
    assert "custom/pyspy" in argv
    assert "--py-spy-rate" in argv
    assert "250" in argv
    assert "--py-spy-native" in argv
    assert "--py-spy-idle" in argv
    assert "--py-spy-top" in argv
