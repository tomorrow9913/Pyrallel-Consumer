from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
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


def test_script_path_execution_supports_help() -> None:
    script_path = (
        Path(__file__).resolve().parents[3] / "benchmarks" / "run_parallel_benchmark.py"
    )

    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Run Pyrallel throughput benchmarks" in result.stdout


def test_build_parser_accepts_comma_separated_workloads_and_order() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(
        [
            "--workloads",
            "sleep,cpu",
            "--order",
            "key_hash,partition",
            "--strict-completion-monitor",
            "on,off",
        ]
    )

    assert args.workloads == ["sleep", "cpu"]
    assert args.order == ["key_hash", "partition"]
    assert args.strict_completion_monitor == ["on", "off"]


def test_build_parser_accepts_process_batching_overrides() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(
        [
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
        ]
    )

    assert args.process_batch_size == 1
    assert args.process_max_batch_wait_ms == 0


def test_build_parser_accepts_process_flush_policy_overrides() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(
        [
            "--process-flush-policy",
            "demand_min_residence",
            "--process-demand-flush-min-residence-ms",
            "2",
        ]
    )

    assert args.process_flush_policy == "demand_min_residence"
    assert args.process_demand_flush_min_residence_ms == 2


def test_build_parser_accepts_metrics_port() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(["--metrics-port", "9091"])

    assert args.metrics_port == 9091


def test_build_parser_defaults_metrics_port_to_9091() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args([])

    assert args.metrics_port == 9091


def test_build_parser_rejects_unknown_workload_token() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(["--workloads", "sleep,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unknown workload token")


def test_build_parser_rejects_unknown_order_token() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(["--order", "key_hash,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unknown order token")


def test_build_parser_rejects_unknown_strict_completion_monitor_token() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(["--strict-completion-monitor", "on,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError(
            "Expected parser to reject unknown strict completion monitor token"
        )
