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
    # Given: inputs for `main launches tui when no args` are prepared.
    launch_tui = Mock()
    run_benchmark = Mock()

    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", launch_tui)
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", run_benchmark)

    # When: the benchmark CLI parser code path is exercised.
    run_parallel_benchmark.main([])

    # Then: the expected `main launches tui when no args` behavior is asserted.
    launch_tui.assert_called_once_with()
    run_benchmark.assert_not_called()


def test_main_runs_cli_when_args_are_supplied(monkeypatch) -> None:
    # Given: inputs for `main runs cli when args are supplied` are prepared.
    captured: dict[str, object] = {}

    def _fake_run_benchmark(args, raw_argv) -> None:
        captured["args"] = args
        captured["raw_argv"] = raw_argv

    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", Mock())
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", _fake_run_benchmark)

    # When: the benchmark CLI parser code path is exercised.
    run_parallel_benchmark.main(["--num-messages", "42", "--skip-process"])

    # Then: the expected `main runs cli when args are supplied` behavior is asserted.
    args = captured["args"]
    assert isinstance(args, argparse.Namespace)
    assert args.num_messages == 42
    assert args.skip_process is True
    assert captured["raw_argv"] == ["--num-messages", "42", "--skip-process"]


def test_main_reports_runtime_errors_without_traceback(monkeypatch) -> None:
    # Given: inputs for `main reports runtime errors without traceback` are prepared.
    def _fake_run_benchmark(_args, raw_argv) -> None:
        del raw_argv
        raise RuntimeError("metrics port is busy")

    # When: the benchmark CLI parser code path is exercised.
    monkeypatch.setattr(run_parallel_benchmark, "launch_tui", Mock())
    monkeypatch.setattr(run_parallel_benchmark, "run_benchmark", _fake_run_benchmark)

    # Then: the expected `main reports runtime errors without traceback` behavior is asserted.
    try:
        run_parallel_benchmark.main(["--num-messages", "42", "--skip-process"])
    except SystemExit as exc:
        assert exc.code == "error: metrics port is busy"
    else:
        raise AssertionError("Expected main to convert RuntimeError to SystemExit")


def test_script_path_execution_supports_help() -> None:
    # Given: inputs for `script path execution supports help` are prepared.
    script_path = (
        Path(__file__).resolve().parents[3] / "benchmarks" / "run_parallel_benchmark.py"
    )

    # When: the benchmark CLI parser code path is exercised.
    result = subprocess.run(
        [sys.executable, str(script_path), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )

    # Then: the expected `script path execution supports help` behavior is asserted.
    assert result.returncode == 0
    assert "Run Pyrallel throughput benchmarks" in result.stdout


def test_build_parser_accepts_comma_separated_workloads_and_order() -> None:
    # Given: inputs for `build parser accepts comma separated workload...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
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

    # Then: the expected `build parser accepts comma separated workload...` behavior is asserted.
    assert args.workloads == ["sleep", "cpu"]
    assert args.order == ["key_hash", "partition"]
    assert args.strict_completion_monitor == ["on", "off"]


def test_build_parser_accepts_process_batching_overrides() -> None:
    # Given: inputs for `build parser accepts process batching overrides` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
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

    # Then: the expected `build parser accepts process batching overrides` behavior is asserted.
    assert args.process_count == 2
    assert args.process_batch_size == 1
    assert args.process_max_batch_wait_ms == 0


def test_build_parser_accepts_process_route_batch_size_override() -> None:
    # Given: inputs for `build parser accepts process route batch size...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(["--process-route-batch-size", "32"])

    # Then: the expected `build parser accepts process route batch size...` behavior is asserted.
    assert args.process_route_batch_size == 32


def test_build_parser_keeps_route_batch_size_as_deprecated_alias() -> None:
    # Given: inputs for `build parser keeps route batch size as deprec...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(["--route-batch-size", "16"])

    # Then: the expected `build parser keeps route batch size as deprec...` behavior is asserted.
    assert args.process_route_batch_size == 16


def test_build_parser_defaults_process_route_batch_size_to_worker_pipes_profile() -> (
    None
):
    # Given: inputs for `build parser defaults process route batch siz...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args([])

    # Then: the expected `build parser defaults process route batch siz...` behavior is asserted.
    assert args.process_route_batch_size == 64


def test_build_parser_rejects_removed_process_transport_option() -> None:
    # Given: inputs for `build parser rejects removed process transpor...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    # Then: the expected `build parser rejects removed process transpor...` behavior is asserted.
    with pytest.raises(SystemExit):
        parser.parse_args(["--process-transport", "shared_queue"])


def test_build_parser_accepts_process_flush_policy_overrides() -> None:
    # Given: inputs for `build parser accepts process flush policy ove...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(
        [
            "--process-flush-policy",
            "demand_min_residence",
            "--process-demand-flush-min-residence-ms",
            "2",
        ]
    )

    # Then: the expected `build parser accepts process flush policy ove...` behavior is asserted.
    assert args.process_flush_policy == "demand_min_residence"
    assert args.process_demand_flush_min_residence_ms == 2


def test_build_parser_defaults_process_worker_pipes_profile() -> None:
    # Given: inputs for `build parser defaults process worker pipes pr...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args([])

    # Then: the expected `build parser defaults process worker pipes pr...` behavior is asserted.
    assert not hasattr(args, "process_transport")
    assert args.process_batch_size == 1
    assert args.process_max_batch_wait_ms == 0


def test_build_parser_accepts_metrics_port() -> None:
    # Given: inputs for `build parser accepts metrics port` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(["--metrics-port", "9091"])

    # Then: the expected `build parser accepts metrics port` behavior is asserted.
    assert args.metrics_port == 9091


def test_build_parser_accepts_generic_workload_option_override() -> None:
    # Given: inputs for `build parser accepts generic workload option...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(
        ["--workloads", "sleep", "--workload-option", "sleep.sleep_ms=1.5"]
    )

    # Then: the expected `build parser accepts generic workload option...` behavior is asserted.
    assert args.workload_options == {"sleep": {"sleep_ms": 1.5}}
    assert args.worker_sleep_ms is None


def test_build_parser_omitted_legacy_default_does_not_conflict_with_generic_override() -> (
    None
):
    # Given: inputs for `build parser omitted legacy default does not...` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args(
        ["--workloads", "cpu", "--workload-option", "cpu.iterations=2000"]
    )

    # Then: the expected `build parser omitted legacy default does not...` behavior is asserted.
    assert args.workload_options == {"cpu": {"iterations": 2000}}
    assert args.worker_cpu_iterations is None


def test_build_parser_rejects_explicit_legacy_and_generic_conflict() -> None:
    # Given: inputs for `build parser rejects explicit legacy and gene...` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects explicit legacy and gene...` behavior is asserted.
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
    # Given: inputs for `build parser rejects generic override for uns...` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects generic override for uns...` behavior is asserted.
    try:
        parser.parse_args(
            ["--workloads", "sleep", "--workload-option", "cpu.iterations=2000"]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unselected workload option")


def test_build_parser_rejects_invalid_generic_override_value() -> None:
    # Given: inputs for `build parser rejects invalid generic override...` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects invalid generic override...` behavior is asserted.
    try:
        parser.parse_args(
            ["--workloads", "sleep", "--workload-option", "sleep.sleep_ms=nan"]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject invalid workload option value")


def test_build_parser_defaults_metrics_port_to_9091() -> None:
    # Given: inputs for `build parser defaults metrics port to 9091` are prepared.
    parser = run_parallel_benchmark.build_parser()

    # When: the benchmark CLI parser code path is exercised.
    args = parser.parse_args([])

    # Then: the expected `build parser defaults metrics port to 9091` behavior is asserted.
    assert args.metrics_port == 9091


def test_build_parser_rejects_unknown_workload_token() -> None:
    # Given: inputs for `build parser rejects unknown workload token` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects unknown workload token` behavior is asserted.
    try:
        parser.parse_args(["--workloads", "sleep,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unknown workload token")


def test_build_parser_help_uses_registry_workload_records(monkeypatch) -> None:
    # Given: inputs for `build parser help uses registry workload records` are prepared.
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="sleep", available=True, reason=""),
            SimpleNamespace(name="custom", available=True, reason=""),
            SimpleNamespace(name="broken", available=False, reason="import failed"),
        ),
    )

    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser help uses registry workload records` behavior is asserted.
    help_text = " ".join(parser.format_help().split())
    assert "available: sleep,custom" in help_text
    assert "broken (unavailable) import failed" in help_text


def test_build_parser_default_workload_uses_first_registry_available(
    monkeypatch,
) -> None:
    # Given: inputs for `build parser default workload uses first regi...` are prepared.
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="custom", available=True, reason=""),
            SimpleNamespace(name="sleep", available=True, reason=""),
        ),
    )

    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser default workload uses first regi...` behavior is asserted.
    args = parser.parse_args([])
    assert args.workloads == ["custom"]


def test_build_parser_rejects_empty_implicit_workload_default(monkeypatch) -> None:
    # Given: inputs for `build parser rejects empty implicit workload...` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="broken", available=False, reason="invalid class"),
        ),
    )
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects empty implicit workload...` behavior is asserted.
    try:
        parser.parse_args([])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject empty workload defaults")


def test_build_parser_deduplicates_duplicate_unavailable_records(monkeypatch) -> None:
    # Given: inputs for `build parser deduplicates duplicate unavailab...` are prepared.
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

    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser deduplicates duplicate unavailab...` behavior is asserted.
    help_text = " ".join(parser.format_help().split())
    assert help_text.count("dupe (unavailable)") == 1
    assert "2 definitions" in help_text


def test_build_parser_rejects_unavailable_workload_token(monkeypatch) -> None:
    # Given: inputs for `build parser rejects unavailable workload token` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    monkeypatch.setattr(
        benchmark_cli,
        "_discover_workload_options",
        lambda: (
            SimpleNamespace(name="sleep", available=True, reason=""),
            SimpleNamespace(name="broken", available=False, reason="invalid class"),
        ),
    )
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects unavailable workload token` behavior is asserted.
    try:
        parser.parse_args(["--workloads", "broken"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unavailable workload token")


def test_build_parser_rejects_unknown_order_token() -> None:
    # Given: inputs for `build parser rejects unknown order token` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects unknown order token` behavior is asserted.
    try:
        parser.parse_args(["--order", "key_hash,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("Expected parser to reject unknown order token")


def test_build_parser_rejects_unknown_strict_completion_monitor_token() -> None:
    # Given: inputs for `build parser rejects unknown strict completio...` are prepared.
    # When: the benchmark CLI parser code path is exercised.
    parser = run_parallel_benchmark.build_parser()

    # Then: the expected `build parser rejects unknown strict completio...` behavior is asserted.
    try:
        parser.parse_args(["--strict-completion-monitor", "on,wat"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError(
            "Expected parser to reject unknown strict completion monitor token"
        )
