from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from benchmarks import benchmark_cli, run_parallel_benchmark


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


def test_main_reports_runtime_errors_without_traceback(monkeypatch) -> None:
    def _fake_run_benchmark(_args, raw_argv) -> None:
        del raw_argv
        raise RuntimeError("metrics port is busy")

    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", Mock())
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", _fake_run_benchmark)

    try:
        run_parallel_benchmark.main(["--num-messages", "42", "--skip-process"])
    except SystemExit as exc:
        assert exc.code == "error: metrics port is busy"
    else:
        raise AssertionError("Expected main to convert RuntimeError to SystemExit")


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
            "--process-count",
            "2",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
        ]
    )

    assert args.process_count == 2
    assert args.process_batch_size == 1
    assert args.process_max_batch_wait_ms == 0


def test_build_parser_accepts_process_route_batch_size_override() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(["--process-route-batch-size", "32"])

    assert args.process_route_batch_size == 32


def test_build_parser_keeps_route_batch_size_as_deprecated_alias() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(["--route-batch-size", "16"])

    assert args.process_route_batch_size == 16


def test_build_parser_defaults_process_route_batch_size_to_worker_pipes_profile() -> (
    None
):
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args([])

    assert args.process_route_batch_size == 64


def test_build_parser_rejects_removed_process_transport_option() -> None:
    parser = run_parallel_benchmark.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["--process-transport", "shared_queue"])


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


def test_build_parser_defaults_process_worker_pipes_profile() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args([])

    assert not hasattr(args, "process_transport")
    assert args.process_batch_size == 1
    assert args.process_max_batch_wait_ms == 0


def test_build_parser_accepts_metrics_port() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(["--metrics-port", "9091"])

    assert args.metrics_port == 9091


def test_build_parser_accepts_generic_workload_option_override() -> None:
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(
        ["--workloads", "sleep", "--workload-option", "sleep.sleep_ms=1.5"]
    )

    assert args.workload_options == {"sleep": {"sleep_ms": 1.5}}
    assert args.worker_sleep_ms is None


def test_build_parser_omitted_legacy_default_does_not_conflict_with_generic_override() -> (
    None
):
    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args(
        ["--workloads", "cpu", "--workload-option", "cpu.iterations=2000"]
    )

    assert args.workload_options == {"cpu": {"iterations": 2000}}
    assert args.worker_cpu_iterations is None


def test_build_parser_rejects_explicit_legacy_and_generic_conflict() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(
            [
                "--workloads",
                "sleep",
                "--worker-sleep-ms",
                "1.5",
                "--workload-option",
                "sleep.sleep_ms=1.5",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError(
            "Expected parser to reject explicit duplicate workload option"
        )


def test_build_parser_rejects_generic_override_for_unselected_workload() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(
            ["--workloads", "sleep", "--workload-option", "cpu.iterations=2000"]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unselected workload option")


def test_build_parser_rejects_invalid_generic_override_value() -> None:
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(
            ["--workloads", "sleep", "--workload-option", "sleep.sleep_ms=nan"]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject invalid workload option value")


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


def test_build_parser_help_uses_registry_workload_records(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="sleep", available=True, reason=""),
            SimpleNamespace(name="custom", available=True, reason=""),
            SimpleNamespace(name="broken", available=False, reason="import failed"),
        ),
    )

    parser = run_parallel_benchmark.build_parser()

    help_text = " ".join(parser.format_help().split())
    assert "available: sleep,custom" in help_text
    assert "broken (unavailable) import failed" in help_text


def test_build_parser_default_workload_uses_first_registry_available(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="custom", available=True, reason=""),
            SimpleNamespace(name="sleep", available=True, reason=""),
        ),
    )

    parser = run_parallel_benchmark.build_parser()

    args = parser.parse_args([])
    assert args.workloads == ["custom"]


def test_build_parser_rejects_empty_implicit_workload_default(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="broken", available=False, reason="invalid class"),
        ),
    )
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject empty workload defaults")


def test_build_parser_deduplicates_duplicate_unavailable_records(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: benchmark_cli._coerce_workload_records(
            (
                SimpleNamespace(name="dupe", available=False, error="duplicate name"),
                SimpleNamespace(name="dupe", available=False, error="duplicate name"),
                SimpleNamespace(name="sleep", available=True, reason=""),
            )
        ),
    )

    parser = run_parallel_benchmark.build_parser()

    help_text = " ".join(parser.format_help().split())
    assert help_text.count("dupe (unavailable)") == 1
    assert "2 definitions" in help_text


def test_build_parser_rejects_unavailable_workload_token(monkeypatch) -> None:
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="sleep", available=True, reason=""),
            SimpleNamespace(name="broken", available=False, reason="invalid class"),
        ),
    )
    parser = run_parallel_benchmark.build_parser()

    try:
        parser.parse_args(["--workloads", "broken"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unavailable workload token")


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
