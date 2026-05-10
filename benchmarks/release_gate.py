from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

RELEASE_GATE_MIN_MESSAGES = 10000
RELEASE_GATE_PARTITIONS = 8
DEFAULT_REQUIRED_REPETITIONS = 2
REQUIRED_PROCESS_TRANSPORT_MODES = ("worker_pipes",)
BATCH_WORKER_V1_MATRIX = "batch-worker-v1"
BATCH_WORKER_V1_WORKER_KINDS = ("single_item_worker", "batch_worker")
BATCH_WORKER_V1_CONSTRUCTORS = {
    "single_item_worker": "PyrallelConsumer",
    "batch_worker": "PyrallelConsumer.from_batch_worker",
}
BATCH_WORKER_V1_RUN_TYPES = ("async", "process")
BATCH_WORKER_V1_WORKLOADS = ("sleep", "io")
BATCH_WORKER_V1_ORDERINGS = ("key_hash", "unordered")
BATCH_WORKER_V1_METRICS_MODES = (False, True)
BATCH_WORKER_V1_REQUIRED_FIELDS = (
    "callback_invocation_count",
    "callback_item_count",
    "rss_max_mb",
    "input_ipc_bytes",
    "completion_ipc_bytes",
    "input_ipc_chunks",
    "completion_ipc_chunks",
)

Combination = tuple[str, str, str]
BenchmarkEntry = tuple[Path, Mapping[str, Any], int | None]
BatchWorkerV1Key = tuple[str, str, str, str, bool]


@dataclass(frozen=True)
class ReleaseThreshold:
    """Represent release threshold data used by release gate."""

    tps_floor: float
    p99_ceiling_ms: float


@dataclass(frozen=True)
class ArtifactProvenanceBinding:
    """Represent artifact provenance binding data used by release gate."""

    repository: str
    git_ref: str
    git_sha: str


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
    """Handle check within release gate."""
    check: dict[str, Any] = {"code": code, "status": status, "message": message}
    if details:
        check["details"] = details
    return check


def _load_summary(path: Path) -> Mapping[str, Any]:
    """Handle load summary within release gate."""
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=lambda constant: (_raise_invalid_json_constant(constant)),
        )
    except json.JSONDecodeError as exc:
        raise ValueError("invalid benchmark JSON at %s: %s" % (path, exc)) from exc
    if not isinstance(payload, dict):
        raise ValueError("benchmark JSON must be an object: %s" % path)
    return payload


def _as_number(value: Any, field: str, *, path: Path) -> float:
    """Handle as number within release gate."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s must be numeric in %s" % (field, path))
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("%s must be finite in %s" % (field, path))
    return number


def _raise_invalid_json_constant(constant: str) -> None:
    raise ValueError("invalid non-finite JSON constant: %s" % constant)


def _as_int(value: Any, field: str, *, path: Path) -> int:
    """Handle as int within release gate."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("%s must be an integer in %s" % (field, path))
    return value


def _result_combination(result: Mapping[str, Any], *, path: Path) -> Combination:
    """Handle result combination within release gate."""
    run_type = result.get("run_type")
    workload = result.get("workload")
    ordering = result.get("ordering")
    if not all(isinstance(part, str) for part in (run_type, workload, ordering)):
        raise ValueError("result is missing run_type/workload/ordering in %s" % path)
    return (str(run_type), str(workload), str(ordering))


def _final_lag(result: Mapping[str, Any]) -> int | None:
    """Handle final lag within release gate."""
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
    """Handle final gap count within release gate."""
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


def _process_transport_mode(result: Mapping[str, Any]) -> str | None:
    """Handle process transport mode within release gate."""
    value = result.get("process_transport_mode")
    if value is None:
        return "worker_pipes"
    if isinstance(value, str) and value in REQUIRED_PROCESS_TRANSPORT_MODES:
        return value
    return None


def _evaluate_options(path: Path, options: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Handle evaluate options within release gate."""
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
    """Handle evaluate persistent gap within release gate."""
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
    """Handle expected messages within release gate."""
    value = options.get("num_messages")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _group_results(
    summaries: Iterable[tuple[Path, Mapping[str, Any]]],
) -> tuple[dict[Combination, list[BenchmarkEntry]], list[dict[str, Any]], set[str]]:
    """Handle group results within release gate."""
    grouped: dict[Combination, list[BenchmarkEntry]] = {
        combination: [] for combination in RELEASE_THRESHOLDS
    }
    checks: list[dict[str, Any]] = []
    process_transport_modes: set[str] = set()
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
            if combination[0] == "process":
                transport_mode = _process_transport_mode(result)
                raw_transport_mode = result.get("process_transport_mode")
                if transport_mode is None:
                    checks.append(
                        _check(
                            "measurement_conditions",
                            "FAIL",
                            "process benchmark results must use worker_pipes process evidence",
                            path=str(path),
                            combination="/".join(combination),
                            process_transport_mode=raw_transport_mode,
                        )
                    )
                    continue
                process_transport_modes.add(transport_mode)
            if combination in grouped:
                grouped[combination].append((path, result, expected_messages))
    return grouped, checks, process_transport_modes


def _evaluate_matrix(
    grouped: Mapping[Combination, list[BenchmarkEntry]],
    required_repetitions: int,
) -> list[dict[str, Any]]:
    """Handle evaluate matrix within release gate."""
    checks: list[dict[str, Any]] = []
    for combination, threshold in RELEASE_THRESHOLDS.items():
        entries = grouped[combination]
        entries_by_label: dict[str, list[BenchmarkEntry]]
        if combination[0] == "process":
            entries_by_label = {
                "%s/%s" % ("/".join(combination), transport_mode): [
                    entry
                    for entry in entries
                    if _process_transport_mode(entry[1]) == transport_mode
                ]
                for transport_mode in REQUIRED_PROCESS_TRANSPORT_MODES
            }
        else:
            entries_by_label = {"/".join(combination): entries}

        for label, labeled_entries in entries_by_label.items():
            distinct_artifact_count = len(
                {path.resolve() for path, _result, _ in labeled_entries}
            )
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
            for path, result, expected_messages in labeled_entries:
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
                    _as_number(
                        result.get("throughput_tps"), "throughput_tps", path=path
                    )
                )
                p99_values.append(
                    _as_number(
                        result.get("p99_processing_ms"),
                        "p99_processing_ms",
                        path=path,
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


def _evaluate_required_matrix(
    required_matrix: str | None,
    summaries: Iterable[tuple[Path, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Evaluate an optional named release evidence matrix."""
    if required_matrix is None:
        return []
    if required_matrix != BATCH_WORKER_V1_MATRIX:
        return [
            _check(
                "required_matrix",
                "FAIL",
                "unknown required release matrix",
                required_matrix=required_matrix,
            )
        ]
    return _evaluate_batch_worker_v1_matrix(summaries)


def _evaluate_batch_worker_v1_matrix(
    summaries: Iterable[tuple[Path, Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """Evaluate Batch-worker v1 release matrix completeness."""
    observed: dict[BatchWorkerV1Key, list[tuple[Path, Mapping[str, Any]]]] = {}
    checks: list[dict[str, Any]] = []

    for path, summary in summaries:
        options = summary.get("options")
        if isinstance(options, Mapping) and options.get(
            "batch_worker_v1_matrix_smoke_artifact"
        ):
            checks.append(
                _check(
                    "batch_worker_v1_matrix",
                    "FAIL",
                    "Batch-worker v1 matrix cannot use smoke artifacts",
                    path=str(path),
                )
            )
        results = summary.get("results")
        if not isinstance(results, list):
            continue
        for result in results:
            if not isinstance(result, Mapping):
                continue
            worker_kind = result.get("worker_kind")
            if worker_kind not in BATCH_WORKER_V1_WORKER_KINDS:
                continue
            if not _has_batch_worker_v1_dimensions(result):
                continue
            key = _batch_worker_v1_key(result)
            if key is None:
                checks.append(
                    _check(
                        "batch_worker_v1_matrix",
                        "FAIL",
                        "Batch-worker v1 matrix result has invalid dimensions",
                        path=str(path),
                        run_name=result.get("run_name"),
                    )
                )
                continue
            checks.extend(_evaluate_batch_worker_v1_result_fields(path, result))
            observed.setdefault(key, []).append((path, result))

    missing = _missing_batch_worker_v1_keys(observed)
    if missing:
        first_missing = missing[0]
        metrics_label = "on" if first_missing[4] else "off"
        checks.append(
            _check(
                "batch_worker_v1_matrix",
                "FAIL",
                (
                    "Batch-worker v1 matrix is missing baseline/batch "
                    "or metrics on/off paired evidence"
                ),
                missing_count=len(missing),
                first_missing={
                    "run_type": first_missing[0],
                    "workload": first_missing[1],
                    "ordering": first_missing[2],
                    "worker_kind": first_missing[3],
                    "metrics": metrics_label,
                },
            )
        )
    if not any(
        _batch_worker_v1_large_payload_process(result)
        for entries in observed.values()
        for _, result in entries
    ):
        checks.append(
            _check(
                "batch_worker_v1_matrix",
                "FAIL",
                "Batch-worker v1 matrix is missing large-payload process evidence",
            )
        )
    if not checks:
        checks.append(
            _check(
                "batch_worker_v1_matrix",
                "PASS",
                "Batch-worker v1 public matrix evidence is complete",
            )
        )
    return checks


def _batch_worker_v1_key(result: Mapping[str, Any]) -> BatchWorkerV1Key | None:
    run_type = result.get("run_type")
    workload = result.get("workload")
    ordering = result.get("ordering")
    worker_kind = result.get("worker_kind")
    metrics_enabled = result.get("metrics_enabled")
    if run_type not in BATCH_WORKER_V1_RUN_TYPES:
        return None
    if workload not in BATCH_WORKER_V1_WORKLOADS:
        return None
    if ordering not in BATCH_WORKER_V1_ORDERINGS:
        return None
    if worker_kind not in BATCH_WORKER_V1_WORKER_KINDS:
        return None
    if not isinstance(metrics_enabled, bool):
        return None
    return (run_type, workload, ordering, worker_kind, metrics_enabled)


def _has_batch_worker_v1_dimensions(result: Mapping[str, Any]) -> bool:
    return (
        result.get("run_type") in BATCH_WORKER_V1_RUN_TYPES
        and result.get("workload") in BATCH_WORKER_V1_WORKLOADS
        and result.get("ordering") in BATCH_WORKER_V1_ORDERINGS
    )


def _evaluate_batch_worker_v1_result_fields(
    path: Path,
    result: Mapping[str, Any],
) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    worker_kind = result.get("worker_kind")
    expected_constructor = BATCH_WORKER_V1_CONSTRUCTORS.get(str(worker_kind))
    if result.get("constructor") != expected_constructor:
        checks.append(
            _check(
                "batch_worker_v1_matrix",
                "FAIL",
                "Batch-worker v1 result constructor must match worker_kind",
                path=str(path),
                run_name=result.get("run_name"),
                worker_kind=worker_kind,
                expected_constructor=expected_constructor,
                actual_constructor=result.get("constructor"),
            )
        )
    for field in BATCH_WORKER_V1_REQUIRED_FIELDS:
        value = result.get(field)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
        ):
            checks.append(
                _check(
                    "batch_worker_v1_matrix",
                    "FAIL",
                    "Batch-worker v1 result is missing required numeric evidence field",
                    path=str(path),
                    run_name=result.get("run_name"),
                    field=field,
                )
            )
    return checks


def _missing_batch_worker_v1_keys(
    observed: Mapping[BatchWorkerV1Key, list[tuple[Path, Mapping[str, Any]]]],
) -> list[BatchWorkerV1Key]:
    missing: list[BatchWorkerV1Key] = []
    for run_type in BATCH_WORKER_V1_RUN_TYPES:
        for workload in BATCH_WORKER_V1_WORKLOADS:
            for ordering in BATCH_WORKER_V1_ORDERINGS:
                for worker_kind in BATCH_WORKER_V1_WORKER_KINDS:
                    for metrics_enabled in BATCH_WORKER_V1_METRICS_MODES:
                        key = (
                            run_type,
                            workload,
                            ordering,
                            worker_kind,
                            metrics_enabled,
                        )
                        if key not in observed:
                            missing.append(key)
    return missing


def _batch_worker_v1_large_payload_process(result: Mapping[str, Any]) -> bool:
    return (
        result.get("run_type") == "process"
        and result.get("worker_kind") == "batch_worker"
        and result.get("large_payload") is True
    )


def _artifact_provenance_binding(
    path: Path, summary: Mapping[str, Any]
) -> tuple[ArtifactProvenanceBinding | None, list[dict[str, Any]]]:
    """Handle artifact provenance binding within release gate."""
    metadata = summary.get("artifact_metadata")
    values: dict[str, str] = {}
    if isinstance(metadata, Mapping):
        metadata_field_map = {
            "repository": "github_repository",
            "git_ref": "git_ref",
            "git_sha": "git_commit_sha",
        }
        missing_fields = []
        for field, metadata_field in metadata_field_map.items():
            value = metadata.get(metadata_field)
            if not isinstance(value, str) or not value:
                missing_fields.append(metadata_field)
                continue
            values[field] = value
        if not missing_fields:
            return (
                ArtifactProvenanceBinding(
                    repository=values["repository"],
                    git_ref=values["git_ref"],
                    git_sha=values["git_sha"],
                ),
                [],
            )

    provenance = summary.get("artifact_provenance")
    if not isinstance(provenance, Mapping):
        return None, [
            _check(
                "provenance_binding",
                "FAIL",
                (
                    "benchmark summary must include artifact_metadata "
                    "(github_repository/git_ref/git_commit_sha) or legacy "
                    "artifact_provenance binding metadata"
                ),
                path=str(path),
            )
        ]

    for field in ("repository", "git_ref", "git_sha"):
        value = provenance.get(field)
        if not isinstance(value, str) or not value:
            return None, [
                _check(
                    "provenance_binding",
                    "FAIL",
                    "artifact_provenance.%s must be a non-empty string" % field,
                    path=str(path),
                )
            ]
        values[field] = value
    return (
        ArtifactProvenanceBinding(
            repository=values["repository"],
            git_ref=values["git_ref"],
            git_sha=values["git_sha"],
        ),
        [],
    )


def evaluate_release_gate(
    benchmark_json_paths: Iterable[str | Path],
    *,
    required_repetitions: int = DEFAULT_REQUIRED_REPETITIONS,
    required_matrix: str | None = None,
) -> dict[str, Any]:
    """Handle evaluate release gate within release gate."""
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
    provenance_binding: ArtifactProvenanceBinding | None = None
    for path, summary in summaries:
        candidate_binding, binding_checks = _artifact_provenance_binding(path, summary)
        checks.extend(binding_checks)
        if candidate_binding is None:
            continue
        if provenance_binding is None:
            provenance_binding = candidate_binding
            continue
        if candidate_binding != provenance_binding:
            checks.append(
                _check(
                    "provenance_binding",
                    "FAIL",
                    "release-gate artifacts must bind to the same repository/ref/sha",
                    path=str(path),
                    expected={
                        "repository": provenance_binding.repository,
                        "git_ref": provenance_binding.git_ref,
                        "git_sha": provenance_binding.git_sha,
                    },
                    actual={
                        "repository": candidate_binding.repository,
                        "git_ref": candidate_binding.git_ref,
                        "git_sha": candidate_binding.git_sha,
                    },
                )
            )
    process_transport_modes: set[str] = set()
    if required_matrix is None:
        grouped, grouped_checks, process_transport_modes = _group_results(summaries)
        checks.extend(grouped_checks)
        checks.extend(_evaluate_matrix(grouped, required_repetitions))
    checks.extend(_evaluate_required_matrix(required_matrix, summaries))
    verdict = "NO-GO" if any(check["status"] == "FAIL" for check in checks) else "PASS"
    return {
        "verdict": verdict,
        "summary": {
            "artifacts": [str(path) for path in paths],
            "required_repetitions": required_repetitions,
            "required_matrix": required_matrix,
            "expected_combinations": len(RELEASE_THRESHOLDS),
            "required_process_transport_modes": list(REQUIRED_PROCESS_TRANSPORT_MODES),
            "process_transport_modes": sorted(process_transport_modes),
            "provenance_binding": (
                {
                    "repository": provenance_binding.repository,
                    "git_ref": provenance_binding.git_ref,
                    "git_sha": provenance_binding.git_sha,
                }
                if provenance_binding is not None
                else None
            ),
        },
        "checks": checks,
    }


def _build_parser() -> argparse.ArgumentParser:
    """Build parser for release gate."""
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
    parser.add_argument(
        "--require-matrix",
        choices=[BATCH_WORKER_V1_MATRIX],
        default=None,
        help="Require a named release evidence matrix in the benchmark artifacts.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the command-line entrypoint."""
    args = _build_parser().parse_args(argv)
    try:
        report = evaluate_release_gate(
            args.benchmark_json,
            required_repetitions=args.required_repetitions,
            required_matrix=args.require_matrix,
        )
    except (OSError, ValueError) as exc:
        report = {
            "verdict": "NO-GO",
            "summary": {
                "artifacts": args.benchmark_json,
                "required_repetitions": args.required_repetitions,
                "required_matrix": args.require_matrix,
                "expected_combinations": len(RELEASE_THRESHOLDS),
                "process_transport_modes": [],
                "provenance_binding": None,
            },
            "checks": [
                _check("schema", "FAIL", str(exc), artifacts=args.benchmark_json)
            ],
        }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
