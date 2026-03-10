from __future__ import annotations

import argparse
from unittest.mock import Mock

from benchmarks import run_parallel_benchmark


def test_main_launches_tui_when_no_args(monkeypatch) -> None:
    launch_tui = Mock()
    run_benchmark = Mock()

    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", launch_tui)
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", run_benchmark)

    run_parallel_benchmark.main([])

    launch_tui.assert_called_once_with()
    run_benchmark.assert_not_called()


def test_main_runs_cli_when_args_are_supplied(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run_benchmark(args, raw_argv) -> None:
        captured["args"] = args
        captured["raw_argv"] = raw_argv

    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", Mock())
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", _fake_run_benchmark)

    run_parallel_benchmark.main(["--num-messages", "42", "--skip-process"])

    args = captured["args"]
    assert isinstance(args, argparse.Namespace)
    assert args.num_messages == 42
    assert args.skip_process is True
    assert captured["raw_argv"] == ["--num-messages", "42", "--skip-process"]
