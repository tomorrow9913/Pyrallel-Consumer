from __future__ import annotations

from typing import cast

from benchmarks.tui.options_form import OptionsFormDraft, validate_options_form
from benchmarks.tui.state import BenchmarkTuiState
from benchmarks.workloads.base import BenchmarkWorkload
from benchmarks.workloads.sleep import SleepWorkload


def _workload_classes() -> dict[str, type[BenchmarkWorkload[object]]]:
    return {"sleep": cast(type[BenchmarkWorkload[object]], SleepWorkload)}


def _draft(
    *,
    input_values: dict[str, str] | None = None,
    switch_values: dict[str, bool] | None = None,
    workload_option_values: dict[str, dict[str, object]] | None = None,
) -> OptionsFormDraft:
    state = BenchmarkTuiState()
    default_input_values = {
        "bootstrap-servers": state.bootstrap_servers,
        "json-output": state.json_output,
        "num-messages": str(state.num_messages),
        "num-keys": str(state.num_keys),
        "num-partitions": str(state.num_partitions),
        "timeout-sec": str(state.timeout_sec),
        "metrics-port": str(state.metrics_port),
        "process-count": ""
        if state.process_count is None
        else str(state.process_count),
        "process-batch-size": ""
        if state.process_batch_size is None
        else str(state.process_batch_size),
        "process-max-batch-wait-ms": ""
        if state.process_max_batch_wait_ms is None
        else str(state.process_max_batch_wait_ms),
        "route-batch-size": str(state.route_batch_size),
        "topic-prefix": state.topic_prefix,
        "profile-dir": state.profile_dir,
        "profile-top-n": str(state.profile_top_n),
        "py-spy-output": state.py_spy_output,
    }
    default_switch_values = {
        "profiling-enabled": state.profiling_enabled,
        "skip-reset": state.skip_reset,
        "profile": state.profile,
        "py-spy": state.py_spy,
        "skip-baseline": state.skip_baseline,
        "skip-async": state.skip_async,
        "skip-process": state.skip_process,
        "py-spy-native": state.py_spy_native,
        "py-spy-idle": state.py_spy_idle,
    }
    select_values = {
        "process-transport": state.process_transport,
        "log-level": state.log_level,
        "py-spy-format": state.py_spy_format,
    }
    return OptionsFormDraft(
        input_values=input_values or default_input_values,
        switch_values=switch_values or default_switch_values,
        select_values=select_values,
        workloads=state.workloads,
        ordering_modes=state.ordering_modes,
        workload_option_values=workload_option_values or {},
    )


def test_tui_app_preserves_validation_result_export() -> None:
    from benchmarks.tui import app as tui_app
    from benchmarks.tui.options_form import OptionsValidationResult

    assert tui_app._ValidationResult is OptionsValidationResult


def test_validate_options_form_rejects_invalid_numeric_input() -> None:
    draft = _draft(
        input_values={
            **_draft().input_values,
            "num-messages": "oops",
        }
    )

    result = validate_options_form(
        draft,
        base_state=BenchmarkTuiState(),
        unavailable_workloads={},
        workload_classes=_workload_classes(),
    )

    assert result.state is None
    assert result.errors["num-messages"] == "Enter a whole number."


def test_validate_options_form_builds_state_with_workload_options() -> None:
    result = validate_options_form(
        _draft(workload_option_values={"sleep": {"sleep_ms": "1.25"}}),
        base_state=BenchmarkTuiState(),
        unavailable_workloads={},
        workload_classes=_workload_classes(),
    )

    assert result.errors == {}
    assert result.state is not None
    assert result.state.worker_sleep_ms == 1.25
    assert result.state.workload_options == {"sleep": {"sleep_ms": 1.25}}


def test_validate_options_form_rejects_all_execution_modes_skipped() -> None:
    draft = _draft(
        switch_values={
            **_draft().switch_values,
            "skip-baseline": True,
            "skip-async": True,
            "skip-process": True,
        }
    )

    result = validate_options_form(
        draft,
        base_state=BenchmarkTuiState(),
        unavailable_workloads={},
        workload_classes=_workload_classes(),
    )

    assert result.state is None
    assert (
        result.errors["skip-phase-group"] == "Keep at least one execution mode enabled."
    )
