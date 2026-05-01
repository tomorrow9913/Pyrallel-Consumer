from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_ORDERING_NAMES = ("key_hash", "partition", "unordered")
_HEADERS = (
    ("실행", "run_name"),
    ("유형", "run_type"),
    ("워크로드", "workload"),
    ("메시지", "messages_processed"),
    ("TPS", "throughput_tps"),
    ("TPS P50 (100)", "tps_p50_window"),
    ("TPS P10 (100)", "tps_p10_window"),
    ("TPS 최소 (100)", "tps_min_window"),
    ("평균 ms", "avg_processing_ms"),
    ("P99 ms", "p99_processing_ms"),
)


@dataclass(frozen=True, slots=True)
class ResultsOverview:
    """Represent results overview data used by results report."""

    total_runs: int
    workloads: tuple[str, ...]
    best_run_name: str
    best_tps: str
    output_path: str


@dataclass(frozen=True, slots=True)
class ResultsTableData:
    """Represent results table data data used by results report."""

    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class WorkloadWinner:
    """Represent workload winner data used by results report."""

    workload: str
    ordering: str
    run_type: str
    throughput_tps: str
    avg_processing_ms: str
    p99_processing_ms: str


def summarize_results_overview(output_path: str | Path) -> ResultsOverview | None:
    """Handle summarize results overview within results report."""
    path = Path(output_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None

    rows = [result for result in results if isinstance(result, dict)]
    if not rows:
        return None

    workloads = tuple(
        sorted(
            {
                str(result.get("workload", "")).strip()
                for result in rows
                if str(result.get("workload", "")).strip()
            }
        )
    )
    best_result = max(
        rows, key=lambda result: _float_value(result.get("throughput_tps"))
    )
    return ResultsOverview(
        total_runs=len(rows),
        workloads=workloads,
        best_run_name=str(best_result.get("run_name", "—")) or "—",
        best_tps=_format_float(best_result.get("throughput_tps"), 2),
        output_path=str(path),
    )


def load_results_table_data(output_path: str | Path) -> ResultsTableData | None:
    """Handle load results table data within results report."""
    path = Path(output_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None

    rows = tuple(
        tuple(_format_row(result)) for result in results if isinstance(result, dict)
    )
    if not rows:
        return None

    return ResultsTableData(
        headers=tuple(header for header, _ in _HEADERS),
        rows=rows,
    )


def summarize_workload_winners(
    output_path: str | Path,
) -> dict[str, dict[str, WorkloadWinner]]:
    """Handle summarize workload winners within results report."""
    payload = _load_results_payload(output_path)
    if payload is None:
        return {}

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return {}

    winners: dict[tuple[str, str], dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        workload = str(result.get("workload", "")).strip()
        ordering = _resolve_ordering(result, payload)
        if not workload or ordering is None:
            continue
        winner_key = (ordering, workload)
        current_best = winners.get(winner_key)
        current_tps = _float_value(result.get("throughput_tps"))
        if current_best is None or current_tps > _float_value(
            current_best.get("throughput_tps")
        ):
            winners[winner_key] = result

    ordering_groups: dict[str, dict[str, WorkloadWinner]] = {}
    for (ordering, workload), result in winners.items():
        ordering_groups.setdefault(ordering, {})[workload] = WorkloadWinner(
            workload=workload,
            ordering=ordering,
            run_type=_display_run_type(
                str(result.get("run_type", "—")) or "—",
                str(result.get("run_name", "")),
            ),
            throughput_tps=_format_float(result.get("throughput_tps"), 2),
            avg_processing_ms=_format_float(result.get("avg_processing_ms"), 3),
            p99_processing_ms=_format_float(result.get("p99_processing_ms"), 3),
        )
    return ordering_groups


def render_results_summary(output_path: str | Path) -> str:
    """Handle render results summary within results report."""
    path = Path(output_path).expanduser()
    table_data = load_results_table_data(path)
    if table_data is None:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return "Results summary unavailable: file not found (%s)" % path
        except json.JSONDecodeError:
            return "Results summary unavailable: invalid JSON (%s)" % path
        return "No benchmark results found in %s" % path

    widths = [
        max(len(header), *(len(row[index]) for row in table_data.rows))
        for index, (header, _) in enumerate(_HEADERS)
    ]
    header_line = " | ".join(
        header.ljust(widths[index]) for index, (header, _) in enumerate(_HEADERS)
    )
    divider = "-+-".join("-" * width for width in widths)
    body = [
        " | ".join(row[index].ljust(widths[index]) for index in range(len(_HEADERS)))
        for row in table_data.rows
    ]
    sections = [header_line, divider, *body]
    performance_summary = _format_performance_improvements(path)
    if performance_summary:
        sections.extend(["", "성능 개선", performance_summary])
    return "\n".join(sections)


def _format_row(result: dict[str, Any]) -> list[str]:
    """Handle format row within results report."""
    return [
        str(result.get("run_name", "")),
        str(result.get("run_type", "")),
        str(result.get("workload", "")),
        _format_integer(result.get("messages_processed")),
        _format_float(result.get("throughput_tps"), 2),
        _format_optional_float(result.get("tps_p50_window"), 2),
        _format_optional_float(result.get("tps_p10_window"), 2),
        _format_optional_float(result.get("tps_min_window"), 2),
        _format_float(result.get("avg_processing_ms"), 3),
        _format_float(result.get("p99_processing_ms"), 3),
    ]


def _format_integer(value: Any) -> str:
    """Handle format integer within results report."""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _format_float(value: Any, precision: int) -> str:
    """Handle format float within results report."""
    try:
        return f"{float(value):,.{precision}f}"
    except (TypeError, ValueError):
        return f"{0:.{precision}f}"


def _format_optional_float(value: Any, precision: int) -> str:
    """Handle format optional float within results report."""
    if value is None:
        return "—"
    try:
        return f"{float(value):,.{precision}f}"
    except (TypeError, ValueError):
        return "—"


def _float_value(value: Any) -> float:
    """Handle float value within results report."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _display_run_type(run_type: str, run_name: str) -> str:
    """Handle display run type within results report."""
    if run_type == "pyrallel":
        lowered = run_name.lower()
        if "async" in lowered:
            return "async"
        if "process" in lowered:
            return "process"
    return run_type


def _load_results_payload(output_path: str | Path) -> dict[str, Any] | None:
    """Handle load results payload within results report."""
    path = Path(output_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _format_performance_improvements(output_path: str | Path) -> str:
    """Handle format performance improvements within results report."""
    payload = _load_results_payload(output_path)
    if payload is None:
        return ""
    improvements = payload.get("performance_improvements")
    if not isinstance(improvements, list) or not improvements:
        return ""

    lines: list[str] = []
    for improvement in improvements:
        if not isinstance(improvement, dict):
            continue
        comparison = str(improvement.get("comparison", "unknown"))
        workload = str(improvement.get("workload", "unknown"))
        ordering = str(improvement.get("ordering", "unknown"))
        run_type = str(improvement.get("run_type", "unknown"))
        delta = _format_signed_optional_float(
            improvement.get("throughput_tps_delta"),
            precision=2,
            suffix=" TPS",
        )
        delta_pct = _format_signed_optional_float(
            improvement.get("throughput_tps_delta_pct"),
            precision=2,
            suffix="%",
        )
        ratio = _format_ratio(improvement.get("improvement_ratio"))
        candidate = str(improvement.get("candidate_run_name", "unknown"))
        reference = str(improvement.get("reference_run_name", "unknown"))
        lines.append(
            "%s/%s/%s | %s | %s (%s) | %s | %s vs %s"
            % (
                workload,
                ordering,
                run_type,
                comparison,
                delta,
                delta_pct,
                ratio,
                candidate,
                reference,
            )
        )
    return "\n".join(lines)


def _format_signed_optional_float(
    value: Any,
    *,
    precision: int,
    suffix: str,
) -> str:
    """Handle format signed optional float within results report."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "n/a"
    sign = "+" if number >= 0 else ""
    return "%s%.*f%s" % (sign, precision, number, suffix)


def _format_ratio(value: Any) -> str:
    """Handle format ratio within results report."""
    try:
        return "%.2fx" % float(value)
    except (TypeError, ValueError):
        return "n/a"


def _resolve_ordering(result: dict[str, Any], payload: dict[str, Any]) -> str | None:
    """Resolve ordering for results report."""
    explicit = str(result.get("ordering", "")).strip()
    if explicit in _ORDERING_NAMES:
        return explicit

    for field in (str(result.get("run_name", "")), str(result.get("topic", ""))):
        for ordering in _ORDERING_NAMES:
            if (
                f"-{ordering}-" in field
                or field.startswith(f"{ordering}-")
                or field.endswith(f"-{ordering}")
                or field == ordering
            ):
                return ordering

    options = payload.get("options")
    if isinstance(options, dict):
        for key in ("order", "ordering_modes"):
            value = options.get(key)
            if isinstance(value, str):
                selected = [item.strip() for item in value.split(",") if item.strip()]
            elif isinstance(value, list):
                selected = [str(item).strip() for item in value if str(item).strip()]
            else:
                continue
            for ordering in selected:
                if ordering in _ORDERING_NAMES:
                    return ordering

    return "key_hash"
