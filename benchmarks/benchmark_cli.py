from __future__ import annotations

import argparse
import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Iterable, Sequence

from pyrallel_consumer.dto import OrderingMode

_FALLBACK_WORKLOAD_CHOICES = ("sleep", "cpu", "io")
_ORDER_CHOICES = tuple(mode.value for mode in OrderingMode)
_STRICT_COMPLETION_MONITOR_CHOICES = ("on", "off")
_ADAPTIVE_CONCURRENCY_CHOICES = ("off", "on")
_WORKER_KIND_CHOICES = ("single", "batch")
_METRICS_CHOICES = ("off", "on")
_PROCESS_FLUSH_POLICY_CHOICES = (
    "size_or_timer",
    "demand",
    "demand_min_residence",
)
_LEGACY_WORKLOAD_FLAGS = {
    "sleep.sleep_ms": "worker_sleep_ms",
    "cpu.iterations": "worker_cpu_iterations",
    "io.sleep_ms": "worker_io_sleep_ms",
}


class BenchmarkArgumentParser(argparse.ArgumentParser):
    """Argument parser that validates workload option overrides after parsing."""

    def parse_args(self, args=None, namespace=None):  # noqa: ANN001, ANN201 - argparse API.
        """Parse CLI args and attach validated workload option overrides."""
        parsed = super().parse_args(args, namespace)
        if not getattr(parsed, "workloads", None):
            self.error("no available workloads discovered")
        try:
            parsed.workload_options = validate_workload_option_overrides(parsed)
        except argparse.ArgumentTypeError as exc:
            self.error(str(exc))
        return parsed


@dataclass(frozen=True)
class _WorkloadCliOption:
    """Handle  WorkloadCliOption for benchmark workload discovery."""

    name: str
    available: bool
    reason: str = ""


def _record_name(record: object) -> str | None:
    """Handle  record name for benchmark workload discovery."""
    name = getattr(record, "name", None)
    if isinstance(name, str) and name:
        return name
    workload_name = getattr(record, "workload_name", None)
    if isinstance(workload_name, str) and workload_name:
        return workload_name
    return None


def _record_available(record: object) -> bool:
    """Handle  record available for benchmark workload discovery."""
    available = getattr(record, "available", None)
    if isinstance(available, bool):
        return available
    is_available = getattr(record, "is_available", None)
    if isinstance(is_available, bool):
        return is_available
    status = getattr(record, "status", None)
    if isinstance(status, str):
        return status.lower() == "available"
    error = getattr(record, "error", None) or getattr(record, "reason", None)
    return not bool(error)


def _record_reason(record: object) -> str:
    """Handle  record reason for benchmark workload discovery."""
    for attr in ("reason", "error", "message"):
        value = getattr(record, attr, None)
        if isinstance(value, str) and value:
            return value
    return "unavailable"


def _coerce_workload_records(
    records: Iterable[object]
) -> tuple[_WorkloadCliOption, ...]:
    """Handle  coerce workload records for benchmark workload discovery."""
    record_tuple = tuple(records)
    options: list[_WorkloadCliOption] = []
    by_name: dict[str, int] = {}
    for record in record_tuple:
        name = _record_name(record)
        if name is None:
            continue
        available = _record_available(record)
        option = _WorkloadCliOption(
            name=name,
            available=available,
            reason="" if available else _record_reason(record),
        )
        existing_index = by_name.get(name)
        if existing_index is None:
            by_name[name] = len(options)
            options.append(option)
            continue

        existing = options[existing_index]
        if available and not existing.available:
            options[existing_index] = option
            continue
        if existing.available or available:
            continue

        reason = existing.reason
        if option.reason and option.reason not in reason:
            reason = "%s; %s" % (reason, option.reason)
        options[existing_index] = _WorkloadCliOption(
            name=name,
            available=False,
            reason="%s (%d definitions)"
            % (reason, _count_records_named(record_tuple, name)),
        )
    return tuple(options)


def _count_records_named(records: Iterable[object], name: str) -> int:
    """Handle  count records named for benchmark workload discovery."""
    return sum(1 for record in records if _record_name(record) == name)


def _discover_workload_options() -> tuple[_WorkloadCliOption, ...]:
    """Handle  discover workload options for benchmark workload discovery."""
    workloads: ModuleType | None
    try:
        workloads = importlib.import_module("benchmarks.workloads")
    except ImportError:
        workloads = None

    if workloads is not None:
        all_records = getattr(workloads, "all_records", None)
        if callable(all_records):
            records = _coerce_workload_records(all_records())
            if records:
                return records

        discover_workloads = getattr(workloads, "discover_workloads", None)
        if callable(discover_workloads):
            registry = discover_workloads()
            registry_records = getattr(registry, "all_records", None)
            if callable(registry_records):
                records = _coerce_workload_records(registry_records())
                if records:
                    return records

        available_names = getattr(workloads, "available_names", None)
        if callable(available_names):
            names = tuple(str(name) for name in available_names())
            if names:
                return tuple(
                    _WorkloadCliOption(name=name, available=True) for name in names
                )

    return tuple(
        _WorkloadCliOption(name=name, available=True)
        for name in _FALLBACK_WORKLOAD_CHOICES
    )


def _format_workload_help(options: Sequence[_WorkloadCliOption]) -> str:
    """Handle  format workload help for benchmark workload discovery."""
    available = [option.name for option in options if option.available]
    unavailable = [
        f"{option.name} (unavailable) {option.reason}"
        for option in options
        if not option.available
    ]
    parts = ["Comma-separated workloads to run"]
    if available:
        parts.append("available: " + ",".join(available))
    if unavailable:
        parts.append("unavailable: " + "; ".join(unavailable))
    return "; ".join(parts)


def _default_workload_selection(options: Sequence[_WorkloadCliOption]) -> list[str]:
    """Return the registry-derived default workload selection."""
    for option in options:
        if option.available:
            return [option.name]
    return []


def parse_csv_selection(
    value: str,
    *,
    argument_name: str,
    choices: Sequence[str],
    unavailable_reasons: dict[str, str] | None = None,
) -> list[str]:
    """Parse csv selection for benchmark cli."""
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if unavailable_reasons and item in unavailable_reasons:
            raise argparse.ArgumentTypeError(
                "%s workload %r is unavailable: %s"
                % (argument_name, item, unavailable_reasons[item])
            )
        if item not in choices:
            choices_str = ", ".join(choices)
            raise argparse.ArgumentTypeError(
                "%s must contain only %s (got %r)" % (argument_name, choices_str, item)
            )
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
    if not items:
        raise argparse.ArgumentTypeError(
            "%s must contain at least one value" % argument_name
        )
    return items


def validate_workload_option_overrides(
    args: argparse.Namespace,
) -> dict[str, dict[str, object]]:
    """Validate and coerce generic workload option overrides."""
    raw_overrides = getattr(args, "workload_option", None) or []
    if not raw_overrides:
        return {}

    from benchmarks.workloads import get_available
    from benchmarks.workloads.base import (
        build_workload_options,
        describe_workload_options,
    )

    selected_workloads = set(args.workloads)
    raw_by_workload: dict[str, dict[str, object]] = {}
    seen: set[str] = set()
    for raw_override in raw_overrides:
        canonical_name, raw_value = _split_workload_option(raw_override)
        if canonical_name in seen:
            raise argparse.ArgumentTypeError(
                "duplicate workload option %r" % canonical_name
            )
        seen.add(canonical_name)
        workload_name, field_name = _split_canonical_option(canonical_name)
        if workload_name not in selected_workloads:
            raise argparse.ArgumentTypeError(
                "workload option %r targets an unselected workload" % canonical_name
            )
        legacy_attr = _LEGACY_WORKLOAD_FLAGS.get(canonical_name)
        if legacy_attr is not None and getattr(args, legacy_attr) is not None:
            raise argparse.ArgumentTypeError(
                "workload option %r conflicts with explicit legacy flag"
                % canonical_name
            )
        raw_by_workload.setdefault(workload_name, {})[field_name] = raw_value

    coerced: dict[str, dict[str, object]] = {}
    for workload_name, values in raw_by_workload.items():
        workload_cls = get_available(workload_name)
        try:
            options = build_workload_options(
                workload_cls, workload_options={workload_name: values}
            )
        except ValueError as exc:
            raise argparse.ArgumentTypeError(str(exc)) from exc
        schema_fields = {
            schema.field_name for schema in describe_workload_options(workload_cls)
        }
        coerced[workload_name] = {
            field_name: getattr(options, field_name)
            for field_name in values
            if field_name in schema_fields
        }
    return coerced


def _split_workload_option(raw_override: str) -> tuple[str, str]:
    """Split one raw workload option override into canonical name and value."""
    if "=" not in raw_override:
        raise argparse.ArgumentTypeError(
            "--workload-option must be <workload>.<option>=<value>"
        )
    canonical_name, raw_value = raw_override.split("=", 1)
    return canonical_name, raw_value


def _split_canonical_option(canonical_name: str) -> tuple[str, str]:
    """Split a canonical workload option name into workload and field names."""
    if "." not in canonical_name:
        raise argparse.ArgumentTypeError(
            "--workload-option must be <workload>.<option>=<value>"
        )
    workload_name, field_name = canonical_name.split(".", 1)
    if not workload_name or not field_name:
        raise argparse.ArgumentTypeError(
            "--workload-option must be <workload>.<option>=<value>"
        )
    return workload_name, field_name


def build_parser() -> argparse.ArgumentParser:
    """Build parser for benchmark cli."""
    workload_options = _discover_workload_options()
    workload_choices = tuple(
        option.name for option in workload_options if option.available
    )
    workload_unavailable_reasons = {
        option.name: option.reason
        for option in workload_options
        if not option.available
    }

    parser = BenchmarkArgumentParser(
        description="Run Pyrallel throughput benchmarks",
        formatter_class=lambda prog: argparse.HelpFormatter(prog, width=200),
    )
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--num-messages", type=int, default=100_000)
    parser.add_argument("--num-keys", type=int, default=100)
    parser.add_argument("--num-partitions", type=int, default=8)
    parser.add_argument("--topic-prefix", default="pyrallel-benchmark")
    parser.add_argument("--baseline-group", default="baseline-benchmark-group")
    parser.add_argument("--async-group", default="async-benchmark-group")
    parser.add_argument("--process-group", default="process-benchmark-group")
    parser.add_argument(
        "--json-output",
        default=None,
        help="Path to write JSON summary (default benchmarks/results/<timestamp>.json)",
    )
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-async", action="store_true")
    parser.add_argument("--skip-process", action="store_true")
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Skip deleting/recreating topics and consumer groups before benchmarks",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="Timeout in seconds for each Pyrallel consumer run",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level for benchmark run (use WARNING for cleaner TPS measurements)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable yappi profiling for each run",
    )
    parser.add_argument(
        "--profile-dir",
        default="benchmarks/results/profiles",
        help="Directory to write .prof files when profiling",
    )
    parser.add_argument(
        "--profile-clock",
        choices=["wall", "cpu"],
        default="wall",
        help="yappi clock type",
    )
    parser.add_argument(
        "--profile-top-n",
        type=int,
        default=0,
        help="Print top N functions by total time after each profiled run (0 to skip)",
    )
    parser.add_argument(
        "--profile-threads",
        action="store_true",
        help="Profile threads when profiling is enabled",
    )
    parser.add_argument(
        "--profile-greenlets",
        action="store_true",
        help="Profile greenlets/async tasks when profiling is enabled",
    )
    parser.add_argument(
        "--profile-process-workers",
        action="store_true",
        help="Also profile process workers (off by default; can emit yappi internal errors).",
    )
    parser.add_argument(
        "--workloads",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--workloads",
            choices=workload_choices,
            unavailable_reasons=workload_unavailable_reasons,
        ),
        default=_default_workload_selection(workload_options),
        help=_format_workload_help(workload_options),
    )
    parser.add_argument(
        "--order",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--order",
            choices=_ORDER_CHOICES,
        ),
        default=["key_hash"],
        help="Comma-separated ordering modes to run (choices: key_hash,partition,unordered)",
    )
    parser.add_argument(
        "--strict-completion-monitor",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--strict-completion-monitor",
            choices=_STRICT_COMPLETION_MONITOR_CHOICES,
        ),
        default=["on"],
        help="Comma-separated strict completion monitor modes to run (choices: on,off)",
    )
    parser.add_argument(
        "--adaptive-concurrency",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--adaptive-concurrency",
            choices=_ADAPTIVE_CONCURRENCY_CHOICES,
        ),
        default=["off"],
        help="Comma-separated adaptive concurrency modes for Pyrallel runs (choices: off,on)",
    )
    parser.add_argument(
        "--worker-kind",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--worker-kind",
            choices=_WORKER_KIND_CHOICES,
        ),
        default=["single"],
        help="Comma-separated worker API kinds for Pyrallel runs (choices: single,batch)",
    )
    parser.add_argument(
        "--metrics",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--metrics",
            choices=_METRICS_CHOICES,
        ),
        default=["off"],
        help="Comma-separated benchmark metrics modes for Pyrallel runs (choices: off,on)",
    )
    parser.add_argument(
        "--worker-sleep-ms",
        type=float,
        default=None,
        help="Sleep per message for sleep workload",
    )
    parser.add_argument(
        "--worker-cpu-iterations",
        type=int,
        default=None,
        help="Iterations for CPU workload",
    )
    parser.add_argument(
        "--worker-io-sleep-ms",
        type=float,
        default=None,
        help="Sleep per message for IO workload (simulated IO wait)",
    )
    parser.add_argument(
        "--payload-bytes",
        type=int,
        default=0,
        help="Add benchmark payload padding bytes per produced message (0 disables)",
    )
    parser.add_argument(
        "--workload-option",
        action="append",
        default=[],
        help="Override workload option as <workload>.<option>=<value>",
    )
    parser.add_argument(
        "--process-count",
        type=int,
        default=None,
        help="Override process-mode worker count for benchmark runs",
    )
    parser.add_argument(
        "--process-batch-size",
        type=int,
        default=1,
        help="Override process-mode micro-batch size for benchmark runs",
    )
    parser.add_argument(
        "--process-route-batch-size",
        type=int,
        dest="process_route_batch_size",
        default=64,
        help="Override process worker-pipe route-batch size for benchmark runs",
    )
    parser.add_argument(
        "--route-batch-size",
        type=int,
        dest="process_route_batch_size",
        help="Deprecated alias for --process-route-batch-size",
    )
    parser.add_argument(
        "--process-max-batch-wait-ms",
        type=int,
        default=0,
        help="Override process-mode micro-batch wait in milliseconds for benchmark runs",
    )
    parser.add_argument(
        "--process-flush-policy",
        choices=_PROCESS_FLUSH_POLICY_CHOICES,
        default=None,
        help="Override process-mode flush policy for benchmark runs",
    )
    parser.add_argument(
        "--process-demand-flush-min-residence-ms",
        type=int,
        default=None,
        help="Override minimum residence time before demand flush is allowed",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=9091,
        help="Expose Prometheus metrics on the host at this port during Pyrallel benchmark runs (default: 9091, use 0 to disable)",
    )
    # -- py-spy profiling options (process mode) --
    parser.add_argument(
        "--py-spy",
        action="store_true",
        help="Enable py-spy profiling for process mode (wraps the benchmark via self-relaunch)",
    )
    parser.add_argument(
        "--py-spy-format",
        choices=["flamegraph", "speedscope", "raw", "chrometrace"],
        default="flamegraph",
        help="py-spy output format (default: flamegraph)",
    )
    parser.add_argument(
        "--py-spy-output",
        default="benchmarks/results/pyspy",
        help="Directory to write py-spy output files (default: benchmarks/results/pyspy)",
    )
    parser.add_argument(
        "--py-spy-rate",
        type=int,
        default=100,
        help="py-spy sampling rate in Hz (default: 100)",
    )
    parser.add_argument(
        "--py-spy-native",
        action="store_true",
        help="Include native C extension frames in py-spy output",
    )
    parser.add_argument(
        "--py-spy-idle",
        action="store_true",
        help="Include idle thread stacks in py-spy output",
    )
    parser.add_argument(
        "--py-spy-top",
        action="store_true",
        help="Use py-spy top (live view) instead of record",
    )
    parser.add_argument(
        "--_pyspy-child",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,  # internal: marks this as the child process under py-spy
    )
    return parser
