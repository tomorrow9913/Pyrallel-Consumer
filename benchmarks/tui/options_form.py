from __future__ import annotations

from dataclasses import dataclass, replace

from benchmarks.tui.state import BenchmarkTuiState
from benchmarks.workloads import build_workload_options, describe_workload_options
from benchmarks.workloads.base import BenchmarkWorkload


@dataclass(slots=True)
class OptionsFormDraft:
    """Plain form values collected from the benchmark options screen."""

    input_values: dict[str, str]
    switch_values: dict[str, bool]
    select_values: dict[str, str]
    workloads: tuple[str, ...]
    ordering_modes: tuple[str, ...]
    workload_option_values: dict[str, dict[str, object]]


@dataclass(slots=True)
class OptionsValidationResult:
    """Validated options form state or field errors."""

    state: BenchmarkTuiState | None
    errors: dict[str, str]


POSITIVE_INT_FIELDS = {
    "num-messages": 1,
    "num-keys": 1,
    "num-partitions": 1,
    "timeout-sec": 1,
    "process-route-batch-size": 1,
}
NON_NEGATIVE_INT_FIELDS = {
    "metrics-port": 0,
    "profile-top-n": 0,
}
OPTIONAL_POSITIVE_INT_FIELDS = {
    "process-count": 1,
    "process-batch-size": 1,
}
OPTIONAL_NON_NEGATIVE_INT_FIELDS = {
    "process-max-batch-wait-ms": 0,
}
NON_NEGATIVE_FLOAT_FIELDS: dict[str, float] = {}


def workload_option_widget_id(workload: str, field_name: str) -> str:
    """Return the stable widget id for one workload option field."""
    return "workload-option-%s-%s" % (workload, field_name)


def validate_options_form(
    draft: OptionsFormDraft,
    *,
    base_state: BenchmarkTuiState,
    unavailable_workloads: dict[str, str],
    workload_classes: dict[str, type[BenchmarkWorkload[object]]],
) -> OptionsValidationResult:
    """Validate raw TUI form values and build the next benchmark state."""
    errors: dict[str, str] = {}
    parsed_ints: dict[str, int] = {}
    parsed_floats: dict[str, float] = {}

    profiling_enabled = draft.switch_values["profiling-enabled"]

    for widget_id, minimum in POSITIVE_INT_FIELDS.items():
        _validate_int(draft, widget_id, minimum, parsed_ints, errors)
    for widget_id, minimum in NON_NEGATIVE_INT_FIELDS.items():
        if widget_id == "profile-top-n" and not profiling_enabled:
            continue
        _validate_int(draft, widget_id, minimum, parsed_ints, errors)
    for widget_id, minimum in OPTIONAL_POSITIVE_INT_FIELDS.items():
        _validate_optional_int(draft, widget_id, minimum, parsed_ints, errors)
    for widget_id, minimum in OPTIONAL_NON_NEGATIVE_INT_FIELDS.items():
        _validate_optional_int(draft, widget_id, minimum, parsed_ints, errors)
    for widget_id, minimum_float in NON_NEGATIVE_FLOAT_FIELDS.items():
        _validate_float(draft, widget_id, minimum_float, parsed_floats, errors)

    if not draft.workloads:
        errors["workloads"] = "Select at least one workload."
    selected_unavailable = [
        workload for workload in draft.workloads if workload in unavailable_workloads
    ]
    if selected_unavailable:
        workload = selected_unavailable[0]
        errors["workloads"] = "Workload %s is unavailable: %s" % (
            workload,
            unavailable_workloads[workload],
        )

    workload_options = _validate_workload_option_fields(
        workloads=draft.workloads,
        raw_workload_options=draft.workload_option_values,
        workload_classes=workload_classes,
        errors=errors,
    )

    if not draft.ordering_modes:
        errors["ordering-modes"] = "Select at least one ordering mode."

    skip_baseline = draft.switch_values["skip-baseline"]
    skip_async = draft.switch_values["skip-async"]
    skip_process = draft.switch_values["skip-process"]
    if skip_baseline and skip_async and skip_process:
        errors["skip-phase-group"] = "Keep at least one execution mode enabled."

    if errors:
        return OptionsValidationResult(state=None, errors=errors)

    state = replace(
        base_state,
        bootstrap_servers=draft.input_values["bootstrap-servers"],
        json_output=draft.input_values["json-output"],
        num_messages=parsed_ints["num-messages"],
        num_keys=parsed_ints["num-keys"],
        num_partitions=parsed_ints["num-partitions"],
        timeout_sec=parsed_ints["timeout-sec"],
        metrics_port=parsed_ints["metrics-port"],
        process_count=parsed_ints.get("process-count"),
        process_batch_size=parsed_ints.get("process-batch-size"),
        process_max_batch_wait_ms=parsed_ints.get("process-max-batch-wait-ms"),
        route_batch_size=parsed_ints["process-route-batch-size"],
        topic_prefix=draft.input_values["topic-prefix"],
        workloads=draft.workloads,
        ordering_modes=draft.ordering_modes,
        log_level=draft.select_values["log-level"],
        skip_reset=draft.switch_values["skip-reset"],
        profiling_enabled=profiling_enabled,
        profile=draft.switch_values["profile"],
        profile_dir=draft.input_values["profile-dir"],
        py_spy=draft.switch_values["py-spy"],
        py_spy_output=draft.input_values["py-spy-output"],
        skip_baseline=skip_baseline,
        skip_async=skip_async,
        skip_process=skip_process,
        profile_top_n=parsed_ints.get("profile-top-n", base_state.profile_top_n),
        py_spy_format=draft.select_values["py-spy-format"],
        py_spy_native=draft.switch_values["py-spy-native"],
        py_spy_idle=draft.switch_values["py-spy-idle"],
        worker_sleep_ms=_float_workload_option_value(
            workload_options, "sleep", "sleep_ms", base_state.worker_sleep_ms
        ),
        worker_cpu_iterations=_int_workload_option_value(
            workload_options,
            "cpu",
            "iterations",
            base_state.worker_cpu_iterations,
        ),
        worker_io_sleep_ms=_float_workload_option_value(
            workload_options, "io", "sleep_ms", base_state.worker_io_sleep_ms
        ),
        workload_options=workload_options,
    )
    return OptionsValidationResult(state=state, errors={})


def _validate_workload_option_fields(
    *,
    workloads: tuple[str, ...],
    raw_workload_options: dict[str, dict[str, object]],
    workload_classes: dict[str, type[BenchmarkWorkload[object]]],
    errors: dict[str, str],
) -> dict[str, dict[str, object]]:
    """Validate selected dynamic workload option values."""
    workload_options: dict[str, dict[str, object]] = {}
    for workload in workloads:
        workload_cls = workload_classes.get(workload)
        if workload_cls is None:
            continue
        raw_options = raw_workload_options.get(workload, {})
        if not raw_options:
            continue
        schemas = describe_workload_options(workload_cls)
        visible_schemas = [
            schema for schema in schemas if schema.field_name in raw_options
        ]
        try:
            options = build_workload_options(
                workload_cls,
                workload_options={workload: raw_options},
            )
        except ValueError as exc:
            schema = visible_schemas[0] if visible_schemas else schemas[0]
            errors[workload_option_widget_id(workload, schema.field_name)] = str(exc)
            continue

        selected_options: dict[str, object] = {}
        for schema in visible_schemas:
            selected_options[schema.field_name] = getattr(options, schema.field_name)
        if selected_options:
            workload_options[workload] = selected_options
    return workload_options


def _validate_int(
    draft: OptionsFormDraft,
    widget_id: str,
    minimum: int,
    parsed_values: dict[str, int],
    errors: dict[str, str],
) -> None:
    """Validate a required integer field from a raw form draft."""
    raw_value = draft.input_values[widget_id].strip()
    try:
        value = int(raw_value)
    except ValueError:
        errors[widget_id] = "Enter a whole number."
        return
    if value < minimum:
        errors[widget_id] = "Enter a whole number >= %d." % minimum
        return
    parsed_values[widget_id] = value


def _validate_optional_int(
    draft: OptionsFormDraft,
    widget_id: str,
    minimum: int,
    parsed_values: dict[str, int],
    errors: dict[str, str],
) -> None:
    """Validate an optional integer field from a raw form draft."""
    raw_value = draft.input_values[widget_id].strip()
    if not raw_value:
        return
    try:
        value = int(raw_value)
    except ValueError:
        errors[widget_id] = "Enter a whole number or leave blank."
        return
    if value < minimum:
        errors[widget_id] = "Enter a whole number >= %d or leave blank." % minimum
        return
    parsed_values[widget_id] = value


def _validate_float(
    draft: OptionsFormDraft,
    widget_id: str,
    minimum: float,
    parsed_values: dict[str, float],
    errors: dict[str, str],
) -> None:
    """Validate a required floating-point field from a raw form draft."""
    raw_value = draft.input_values[widget_id].strip()
    try:
        value = float(raw_value)
    except ValueError:
        errors[widget_id] = "Enter a number."
        return
    if value < minimum:
        errors[widget_id] = "Enter a number >= %.1f." % minimum
        return
    parsed_values[widget_id] = value


def _float_workload_option_value(
    workload_options: dict[str, dict[str, object]],
    workload: str,
    option_name: str,
    default: float,
) -> float:
    """Return a validated workload option value as float."""
    value = workload_options.get(workload, {}).get(option_name, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimeError(
            "Validated workload option %s.%s is not numeric" % (workload, option_name)
        )
    return float(value)


def _int_workload_option_value(
    workload_options: dict[str, dict[str, object]],
    workload: str,
    option_name: str,
    default: int,
) -> int:
    """Return a validated workload option value as int."""
    value = workload_options.get(workload, {}).get(option_name, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise RuntimeError(
            "Validated workload option %s.%s is not an integer"
            % (workload, option_name)
        )
    return value
