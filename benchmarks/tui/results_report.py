from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HEADERS = (
    ("Run", "run_name"),
    ("Type", "run_type"),
    ("Workload", "workload"),
    ("Messages", "messages_processed"),
    ("TPS", "throughput_tps"),
    ("Avg ms", "avg_processing_ms"),
    ("P99 ms", "p99_processing_ms"),
)


@dataclass(frozen=True, slots=True)
class ResultsOverview:
    total_runs: int
    workloads: tuple[str, ...]
    best_run_name: str
    best_tps: str
    output_path: str


@dataclass(frozen=True, slots=True)
class ResultsTableData:
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True)
class WorkloadWinner:
    workload: str
    run_type: str
    throughput_tps: str
    total_time_sec: str


def summarize_results_overview(output_path: str | Path) -> ResultsOverview | None:
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
) -> dict[str, WorkloadWinner]:
    path = Path(output_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return {}

    winners: dict[str, dict[str, Any]] = {}
    for result in results:
        if not isinstance(result, dict):
            continue
        workload = str(result.get("workload", "")).strip()
        if not workload:
            continue
        current_best = winners.get(workload)
        current_tps = _float_value(result.get("throughput_tps"))
        if current_best is None or current_tps > _float_value(
            current_best.get("throughput_tps")
        ):
            winners[workload] = result

    return {
        workload: WorkloadWinner(
            workload=workload,
            run_type=_display_run_type(
                str(result.get("run_type", "—")) or "—",
                str(result.get("run_name", "")),
            ),
            throughput_tps=_format_float(result.get("throughput_tps"), 2),
            total_time_sec=_format_float(result.get("total_time_sec"), 2),
        )
        for workload, result in winners.items()
    }


def render_results_summary(output_path: str | Path) -> str:
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
    return "\n".join([header_line, divider, *body])


def _format_row(result: dict[str, Any]) -> list[str]:
    return [
        str(result.get("run_name", "")),
        str(result.get("run_type", "")),
        str(result.get("workload", "")),
        _format_integer(result.get("messages_processed")),
        _format_float(result.get("throughput_tps"), 2),
        _format_float(result.get("avg_processing_ms"), 3),
        _format_float(result.get("p99_processing_ms"), 3),
    ]


def _format_integer(value: Any) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "0"


def _format_float(value: Any, precision: int) -> str:
    try:
        return f"{float(value):,.{precision}f}"
    except (TypeError, ValueError):
        return f"{0:.{precision}f}"


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _display_run_type(run_type: str, run_name: str) -> str:
    if run_type == "pyrallel":
        lowered = run_name.lower()
        if "async" in lowered:
            return "async"
        if "process" in lowered:
            return "process"
    return run_type
