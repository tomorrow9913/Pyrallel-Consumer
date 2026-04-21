from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

FORBIDDEN_RUNTIME_KNOBS = ("process_count", "queue_size")
PROCESS_NEXT_RUN_FLAGS = (
    "--process-batch-size",
    "1",
    "--process-max-batch-wait-ms",
    "0",
    "--process-flush-policy",
    "demand_min_residence",
    "--process-demand-flush-min-residence-ms",
    "1",
)

_TIMER_DOMINATED_RATIO = 0.80
_SMALL_BATCH_AVG_SIZE = 2.5


@dataclass(frozen=True)
class ProcessBatchAdvice:
    run_name: str
    recommendation: str
    next_run_flags: tuple[str, ...]
    evidence: dict[str, float | int | str]
    forbidden_knobs: tuple[str, ...] = FORBIDDEN_RUNTIME_KNOBS
    runtime_mutation: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_benchmark_summary(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Expected benchmark summary JSON object")
    return payload


def build_process_batch_advice(summary: Mapping[str, Any]) -> list[ProcessBatchAdvice]:
    results = summary.get("results", [])
    if not isinstance(results, list):
        raise ValueError("Expected benchmark summary 'results' list")

    advice: list[ProcessBatchAdvice] = []
    for result in results:
        if not isinstance(result, Mapping):
            continue
        item = _build_result_advice(result)
        if item is not None:
            advice.append(item)
    return advice


def format_advice_markdown(advice: Sequence[ProcessBatchAdvice]) -> str:
    if not advice:
        return "No process batch advisor recommendations generated."

    lines = ["# Process Batch Advisor", ""]
    for item in advice:
        lines.extend(
            [
                "## %s" % item.run_name,
                "- Recommendation: %s" % item.recommendation,
                "- Next-run flags: `%s`" % _format_flag_command(item.next_run_flags),
                "- Runtime mutation: %s" % str(item.runtime_mutation).lower(),
                "- Forbidden knobs: `%s`" % "`, `".join(item.forbidden_knobs),
                "- Evidence:",
            ]
        )
        for key, value in item.evidence.items():
            lines.append("  - `%s`: %s" % (key, value))
        lines.append("")
    return "\n".join(lines).rstrip()


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Generate advisor-only process batch follow-up recommendations."
    )
    parser.add_argument("summary_json", type=Path)
    parser.add_argument(
        "--format",
        choices=("markdown", "json"),
        default="markdown",
        help="Output format for advisor recommendations",
    )
    args = parser.parse_args(argv)

    summary = load_benchmark_summary(args.summary_json)
    advice = build_process_batch_advice(summary)
    if args.format == "json":
        print(json.dumps([item.to_dict() for item in advice], indent=2))
        return
    print(format_advice_markdown(advice))


def _build_result_advice(result: Mapping[str, Any]) -> ProcessBatchAdvice | None:
    if result.get("run_type") != "process":
        return None

    metrics = result.get("process_batch_metrics")
    if not isinstance(metrics, Mapping):
        return None

    total_flushes = _total_flushes(metrics)
    total_flushed_items = _coerce_int(metrics.get("total_flushed_items"))
    if total_flushes <= 0 or total_flushed_items <= 0:
        return None

    timer_flush_count = _coerce_int(metrics.get("timer_flush_count"))
    timer_flush_ratio = timer_flush_count / total_flushes
    avg_batch_size = total_flushed_items / total_flushes
    last_flush_size = _coerce_int(metrics.get("last_flush_size"))
    ordering = str(result.get("ordering", "unknown"))

    evidence: dict[str, float | int | str] = {
        "ordering": ordering,
        "throughput_tps": _coerce_float(result.get("throughput_tps")),
        "timer_flush_ratio": round(timer_flush_ratio, 4),
        "avg_batch_size": round(avg_batch_size, 4),
        "last_flush_size": last_flush_size,
        "avg_main_to_worker_ipc_seconds": _coerce_float(
            metrics.get("avg_main_to_worker_ipc_seconds")
        ),
        "avg_worker_exec_seconds": _coerce_float(
            metrics.get("avg_worker_exec_seconds")
        ),
        "avg_worker_to_main_ipc_seconds": _coerce_float(
            metrics.get("avg_worker_to_main_ipc_seconds")
        ),
    }

    if timer_flush_ratio < _TIMER_DOMINATED_RATIO:
        return None
    if avg_batch_size > _SMALL_BATCH_AVG_SIZE:
        return None

    return ProcessBatchAdvice(
        run_name=str(result.get("run_name", "unknown-process-run")),
        recommendation=(
            "Run a follow-up benchmark with single-item micro-batches and "
            "demand_min_residence flush policy; keep this advisor-only and compare "
            "against the original run before changing defaults."
        ),
        next_run_flags=PROCESS_NEXT_RUN_FLAGS,
        evidence=evidence,
    )


def _total_flushes(metrics: Mapping[str, Any]) -> int:
    return (
        _coerce_int(metrics.get("size_flush_count"))
        + _coerce_int(metrics.get("timer_flush_count"))
        + _coerce_int(metrics.get("close_flush_count"))
        + _coerce_int(metrics.get("demand_flush_count"))
    )


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    return 0


def _coerce_float(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    return 0.0


def _format_flag_command(flags: Sequence[str]) -> str:
    pairs: list[str] = []
    for index in range(0, len(flags), 2):
        flag = flags[index]
        try:
            value = flags[index + 1]
        except IndexError:
            pairs.append(flag)
            continue
        pairs.append("%s %s" % (flag, value))
    return " ".join(pairs)


if __name__ == "__main__":
    main()
