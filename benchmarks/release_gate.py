from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

RELEASE_GATE_MIN_MESSAGES = 10000
RELEASE_GATE_PARTITIONS = 8
DEFAULT_REQUIRED_REPETITIONS = 2

Combination = tuple[str, str, str]
BenchmarkEntry = tuple[Path, Mapping[str, Any], int | None]


@dataclass(frozen=True)
class ReleaseThreshold:
    tps_floor: float
    p99_ceiling_ms: float


RELEASE_THRESHOLDS: dict[Combination, ReleaseThreshold] = {
    ("async", "sleep", "key_hash"): ReleaseThreshold(4900, 13),
    ("async", "sleep", "partition"): ReleaseThreshold(2950, 2),
    ("async", "cpu", "key_hash"): ReleaseThreshold(2050, 30),
    ("async", "cpu", "partition"): ReleaseThreshold(2050, 3),
    ("async", "io", "key_hash"): ReleaseThreshold(4950, 15),
    ("async", "io", "partition"): ReleaseThreshold(2950, 2),
    ("process", "sleep", "key_hash"): ReleaseThreshold(2550, 30),
    ("process", "sleep", "partition"): ReleaseThreshold(380, 11),
    ("process", "cpu", "key_hash"): ReleaseThreshold(2100, 30),
    ("process", "cpu", "partition"): ReleaseThreshold(390, 11),
    ("process", "io", "key_hash"): ReleaseThreshold(2650, 30),
    ("process", "io", "partition"): ReleaseThreshold(390, 10),
}


def _check(code: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    check: dict[str, Any] = {"code": code, "status": status, "message": message}
    if details:
        check["details"] = details
    return check


def _load_summary(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("invalid benchmark JSON at %s: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("benchmark JSON must be an object: %s" % path)
    return payload


def _as_number(value: Any, field: str, *, path: Path) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric in %s" % (field, path))
    return float(value)


def _as_int(value: Any, field: str, *, path: Path) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer in %s" % (field, path))
    return value


def _result_combination(result: Mapping[str, Any], *, path: Path) -> Combination:
    run_type = result.get("run_type")
    workload = result.get("workload")
    ordering = result.get("ordering")
    if not all(isinstance(part, str) for part in (run_type, workload, ordering)):
        raise ValueError("result is missing run_type/workload/ordering in %s" % path)
    return (str(run_type), str(workload), str(ordering))


def _final_lag(result: Mapping[str, Any]) -> int | None:
    for field in ("final_lag", "consumer_parallel_lag"):
        value = result.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    completion = result.get("completion_metrics")
    if isinstance(completion, Mapping):
        value = completion.get("consumer_parallel_lag")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _final_gap_count(result: Mapping[str, Any]) -> int | None:
    for field in ("final_gap_count", "consumer_gap_count"):
        value = result.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    completion = result.get("completion_metrics")
    if isinstance(completion, Mapping):
        value = completion.get("consumer_gap_count")
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    return None


def _evaluate_options(path: Path, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if options.get("num_messages") != RELEASE_GATE_MIN_MESSAGES:
        checks.append(
            _check(
                "measurement_conditions",
                "FAIL",
                "num_messages must equal the fixed release-gate sample size",
                path=str(path),
                expected=RELEASE_GATE_MIN_MESSAGES,
                actual=options.get("num_messages"),
            )
        )
    if options.get("num_partitions") != RELEASE_GATE_PARTITIONS:
        checks.append(
            _check(
                "measurement_conditions",
                "FAIL",
                "num_partitions must match the release-gate baseline",
                path=str(path),
                expected=RELEASE_GATE_PARTITIONS,
                actual=options.get("num_partitions"),
            )
        )
    strict_modes = options.get("strict_completion_monitor")
    if strict_modes != ["on"]:
        checks.append(
            _check(
                "measurement_conditions",
                "FAIL",
                "strict completion monitor must be on only",
                path=str(path),
                expected=["on"],
                actual=strict_modes,
            )
        )
    if options.get("profile") is True or options.get("py_spy") is True:
        checks.append(
            _check(
                "measurement_conditions",
                "FAIL",
                "release performance gate must run with profiling disabled",
                path=str(path),
                profile=options.get("profile"),
                py_spy=options.get("py_spy"),
            )
        )
    return checks


def _evaluate_persistent_gap(
    path: Path, summary: Mapping[str, Any]
) -> list[dict[str, Any]]:
    observations = summary.get("metrics_observations")
    if observations is None:
        return []
    if not isinstance(observations, list):
        return [
            _check(
                "schema",
                "FAIL",
                "metrics_observations must be a list when provided",
                path=str(path),
            )
        ]
    positive_started_at: float | None = None
    longest_positive_gap_sec = 0.0
    current_run_name: str | None = None
    for observation in observations:
        if not isinstance(observation, Mapping):
            return [
                _check(
                    "schema",
                    "FAIL",
                    "metrics_observations entries must be objects",
                    path=str(path),
                )
            ]
        run_name_value = observation.get("run_name")
        run_name = (
            str(run_name_value)
            if isinstance(run_name_value, str) and run_name_value
            else None
        )
        if current_run_name != run_name:
            current_run_name = run_name
            positive_started_at = None

        elapsed_sec = _as_number(
            observation.get("elapsed_sec"), "elapsed_sec", path=path
        )
        gap_count = _as_int(
            observation.get("consumer_gap_count"), "consumer_gap_count", path=path
        )
        if gap_count > 0:
            if positive_started_at is None:
                positive_started_at = elapsed_sec
            longest_positive_gap_sec = max(
                longest_positive_gap_sec, elapsed_sec - positive_started_at
            )
            continue
        positive_started_at = None
    if longest_positive_gap_sec > 60:
        return [
            _check(
                "persistent_gap",
                "FAIL",
                "consumer_gap_count persisted above zero for over 60 seconds",
                path=str(path),
                longest_positive_gap_sec=longest_positive_gap_sec,
            )
        ]
    return []


def _expected_messages(path: Path, options: Mapping[str, Any]) -> int | None:
    value = options.get("num_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _group_results(
    summaries: Iterable[tuple[Path, Mapping[str, Any]]],
) -> tuple[dict[Combination, list[BenchmarkEntry]], list[dict[str, Any]]]:
    grouped: dict[Combination, list[BenchmarkEntry]] = {
        combination: [] for combination in RELEASE_THRESHOLDS
    }
    checks: list[dict[str, Any]] = []
    for path, summary in summaries:
        expected_messages = None
        options = summary.get("options")
        if not isinstance(options, Mapping):
            checks.append(
                _check(
                    "schema",
                    "FAIL",
                    "benchmark summary is missing options",
                    path=str(path),
                )
            )
            options = {}
        else:
            expected_messages = _expected_messages(path, options)
        checks.extend(_evaluate_options(path, options))
        checks.extend(_evaluate_persistent_gap(path, summary))
        results = summary.get("results")
        if not isinstance(results, list):
            checks.append(
                _check(
                    "schema",
                    "FAIL",
                    "benchmark summary is missing results",
                    path=str(path),
                )
            )
            continue
        for result in results:
            if not isinstance(result, Mapping):
                checks.append(
                    _check(
                        "schema",
                        "FAIL",
                        "benchmark result is not an object",
                        path=str(path),
                    )
                )
                continue
            combination = _result_combination(result, path=path)
            if combination in grouped:
                grouped[combination].append((path, result, expected_messages))
    return grouped, checks


def _evaluate_matrix(
    grouped: Mapping[Combination, list[BenchmarkEntry]],
    required_repetitions: int,
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for combination, threshold in RELEASE_THRESHOLDS.items():
        entries = grouped[combination]
        label = "/".join(combination)
        distinct_artifact_count = len({path.resolve() for path, _result, _ in entries})
        if distinct_artifact_count < required_repetitions:
            checks.append(
                _check(
                    "repetitions",
                    "FAIL",
                    "release gate requires repeated runs per combination",
                    combination=label,
                    expected=required_repetitions,
                    actual=distinct_artifact_count,
                )
            )
            continue

        tps_values = []
        p99_values = []
        for path, result, expected_messages in entries:
            if expected_messages is None:
                checks.append(
                    _check(
                        "measurement_conditions",
                        "FAIL",
                        "options.num_messages must be an integer",
                        path=str(path),
                        combination=label,
                    )
                )
                continue
            messages_processed = _as_int(
                result.get("messages_processed"), "messages_processed", path=path
            )
            if messages_processed != expected_messages:
                checks.append(
                    _check(
                        "completion",
                        "FAIL",
                        "messages_processed must equal num_messages",
                        path=str(path),
                        combination=label,
                        expected=expected_messages,
                        actual=messages_processed,
                    )
                )
            final_lag = _final_lag(result)
            final_gap_count = _final_gap_count(result)
            if final_lag != 0 or final_gap_count != 0:
                checks.append(
                    _check(
                        "lag_gap",
                        "FAIL",
                        "final lag and final gap count must be explicitly zero",
                        path=str(path),
                        combination=label,
                        final_lag=final_lag,
                        final_gap_count=final_gap_count,
                    )
                )
            tps_values.append(
                _as_number(result.get("throughput_tps"), "throughput_tps", path=path)
            )
            p99_values.append(
                _as_number(
                    result.get("p99_processing_ms"), "p99_processing_ms", path=path
                )
            )

        if not tps_values or not p99_values:
            continue
        worst_tps = min(tps_values)
        worst_p99 = max(p99_values)
        if worst_tps < threshold.tps_floor or worst_p99 > threshold.p99_ceiling_ms:
            checks.append(
                _check(
                    "thresholds",
                    "FAIL",
                    "worst-case TPS and p99 must satisfy release thresholds",
                    combination=label,
                    tps_floor=threshold.tps_floor,
                    worst_tps=worst_tps,
                    p99_ceiling_ms=threshold.p99_ceiling_ms,
                    worst_p99_ms=worst_p99,
                )
            )
    if not checks:
        checks.append(
            _check(
                "release_gate",
                "PASS",
                "all release benchmark repetitions, completion, lag/gap, and thresholds passed",
            )
        )
    return checks


def evaluate_release_gate(
    benchmark_json_paths: Iterable[str | Path],
    *,
    required_repetitions: int = DEFAULT_REQUIRED_REPETITIONS,
) -> dict[str, Any]:
    paths = [Path(path) for path in benchmark_json_paths]
    checks: list[dict[str, Any]] = []
    if required_repetitions < 1:
        checks.append(
            _check(
                "repetitions",
                "FAIL",
                "required_repetitions must be at least 1",
                actual=required_repetitions,
            )
        )
    normalized_paths = [str(path.resolve()) for path in paths]
    if len(set(normalized_paths)) != len(normalized_paths):
        checks.append(
            _check(
                "artifacts",
                "FAIL",
                "benchmark_json paths must be distinct release-gate artifacts",
                artifacts=[str(path) for path in paths],
            )
        )
    summaries = [(path, _load_summary(path)) for path in paths]
    grouped, grouped_checks = _group_results(summaries)
    checks.extend(grouped_checks)
    checks.extend(_evaluate_matrix(grouped, required_repetitions))
    verdict = "NO-GO" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    return {
        "verdict": verdict,
        "summary": {
            "artifacts": [str(path) for path in paths],
            "required_repetitions": required_repetitions,
            "expected_combinations": len(RELEASE_THRESHOLDS),
        },
        "checks": checks,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate benchmark JSON artifacts against release performance gates."
    )
    parser.add_argument(
        "--benchmark-json",
        action="append",
        required=True,
        help="Benchmark JSON artifact to evaluate. Repeat for release-gate repetitions.",
    )
    parser.add_argument(
        "--required-repetitions",
        type=int,
        default=DEFAULT_REQUIRED_REPETITIONS,
        help="Minimum artifact count required for each release-gate combination.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        report = evaluate_release_gate(
            args.benchmark_json,
            required_repetitions=args.required_repetitions,
        )
    except (OSError, ValueError) as exc:
        report = {
            "verdict": "NO-GO",
            "summary": {
                "artifacts": args.benchmark_json,
                "required_repetitions": args.required_repetitions,
                "expected_combinations": len(RELEASE_THRESHOLDS),
            },
            "checks": [
                _check("schema", "FAIL", str(exc), artifacts=args.benchmark_json)
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
