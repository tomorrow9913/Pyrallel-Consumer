from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

release_gate = importlib.import_module("benchmarks.release_gate")
RELEASE_VERIFY_WORKFLOW = ROOT / ".github" / "workflows" / "release-verify.yml"


def _result(
    *,
    run_type: str,
    workload: str,
    ordering: str,
    throughput_tps: float,
    p99_processing_ms: float,
    process_transport_mode: str = "shared_queue",
    messages_processed: int = 10000,
    final_lag: int = 0,
    final_gap_count: int = 0,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "run_name": "%s-%s-pyrallel-%s" % (workload, ordering, run_type),
        "run_type": run_type,
        "workload": workload,
        "ordering": ordering,
        "messages_processed": messages_processed,
        "throughput_tps": throughput_tps,
        "p99_processing_ms": p99_processing_ms,
        "final_lag": final_lag,
        "final_gap_count": final_gap_count,
    }
    if run_type == "process":
        payload["process_transport_mode"] = process_transport_mode
    return payload


def _passing_summary() -> dict[str, object]:
    results = []
    for (
        run_type,
        workload,
        ordering,
    ), threshold in release_gate.RELEASE_THRESHOLDS.items():
        process_transport_modes = (
            release_gate.REQUIRED_PROCESS_TRANSPORT_MODES
            if run_type == "process"
            else (None,)
        )
        for process_transport_mode in process_transport_modes:
            results.append(
                _result(
                    run_type=run_type,
                    workload=workload,
                    ordering=ordering,
                    process_transport_mode=process_transport_mode or "shared_queue",
                    throughput_tps=threshold.tps_floor + 1,
                    p99_processing_ms=threshold.p99_ceiling_ms - 0.1,
                )
            )
    return {
        "artifact_metadata": {
            "artifact_name": "release-gate-develop-123",
            "artifact_path": "benchmarks/results/release-gate.json",
            "execution_context": "github_actions",
            "generated_at_utc": "2026-04-25T05:00:00Z",
            "git_commit_sha": "0123456789abcdef0123456789abcdef01234567",
            "git_ref": "refs/heads/develop",
            "git_ref_name": "develop",
            "git_ref_type": "branch",
            "github_repository": "mqueue/Pyrallel-Consumer",
        },
        "options": {
            "num_messages": 10000,
            "num_partitions": 8,
            "strict_completion_monitor": ["on"],
            "profile": False,
            "py_spy": False,
        },
        "results": results,
    }


def test_evaluate_release_gate_passes_two_complete_threshold_runs(
    tmp_path: Path,
) -> None:
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "PASS"
    assert report["summary"]["required_repetitions"] == 2
    assert all(check["status"] == "PASS" for check in report["checks"])


def test_evaluate_release_gate_reports_no_go_for_threshold_and_completion_failures(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    results = bad["results"]
    assert isinstance(results, list)
    for result in results:
        if (
            result["run_type"] == "async"
            and result["workload"] == "sleep"
            and result["ordering"] == "key_hash"
        ):
            result["throughput_tps"] = 10
            result["messages_processed"] = 9999
            result["final_gap_count"] = 1
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-bad-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "thresholds" in failed_codes
    assert "completion" in failed_codes
    assert "lag_gap" in failed_codes


def test_evaluate_release_gate_reports_no_go_for_persistent_gap_observations(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    bad["metrics_observations"] = [
        {"elapsed_sec": 10, "consumer_gap_count": 1},
        {"elapsed_sec": 71, "consumer_gap_count": 1},
    ]
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-gap-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "persistent_gap" in failed_codes


def test_evaluate_release_gate_resets_persistent_gap_timer_per_run_name(
    tmp_path: Path,
) -> None:
    good = _passing_summary()
    good["metrics_observations"] = [
        {"run_name": "run-a", "elapsed_sec": 10, "consumer_gap_count": 1},
        {"run_name": "run-a", "elapsed_sec": 50, "consumer_gap_count": 1},
        {"run_name": "run-b", "elapsed_sec": 5, "consumer_gap_count": 1},
        {"run_name": "run-b", "elapsed_sec": 45, "consumer_gap_count": 1},
    ]
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-grouped-gap-%d.json" % index)
        path.write_text(json.dumps(good), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "PASS"
    assert all(
        check["code"] != "persistent_gap" or check["status"] == "PASS"
        for check in report["checks"]
    )


def test_evaluate_release_gate_requires_repeated_full_release_matrix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "single.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    report = release_gate.evaluate_release_gate([path])

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "repetitions" in failed_codes


def test_evaluate_release_gate_counts_repetitions_by_distinct_artifact(
    tmp_path: Path,
) -> None:
    duplicate_rows = _passing_summary()
    results = duplicate_rows["results"]
    assert isinstance(results, list)
    results.extend(dict(result) for result in list(results))
    path = tmp_path / "single-with-duplicates.json"
    path.write_text(json.dumps(duplicate_rows), encoding="utf-8")

    report = release_gate.evaluate_release_gate([path])

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "repetitions" in failed_codes


def test_cli_emits_machine_readable_no_go_and_nonzero_exit(tmp_path: Path) -> None:
    path = tmp_path / "single.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.release_gate",
            "--benchmark-json",
            str(path),
        ],
        check=False,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "NO-GO"


def test_evaluate_release_gate_rejects_duplicate_artifact_paths(tmp_path: Path) -> None:
    path = tmp_path / "release-gate.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    report = release_gate.evaluate_release_gate([path, path])

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "artifacts" in failed_codes


def test_evaluate_release_gate_rejects_invalid_repetition_count(tmp_path: Path) -> None:
    path = tmp_path / "release-gate.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    report = release_gate.evaluate_release_gate([path], required_repetitions=0)

    assert report["verdict"] == "NO-GO"
    assert report["checks"][0]["code"] == "repetitions"


def test_evaluate_release_gate_reports_schema_failure_for_missing_num_messages(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    options = bad["options"]
    assert isinstance(options, dict)
    del options["num_messages"]
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-missing-options-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "measurement_conditions" in failed_codes


def test_evaluate_release_gate_requires_artifact_metadata_provenance_binding(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    bad.pop("artifact_metadata")
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-no-provenance-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "provenance_binding" in failed_codes


def test_evaluate_release_gate_rejects_mismatched_artifact_metadata_provenance_binding(
    tmp_path: Path,
) -> None:
    first = _passing_summary()
    second = _passing_summary()
    artifact_metadata = second["artifact_metadata"]
    assert isinstance(artifact_metadata, dict)
    artifact_metadata["git_ref"] = "refs/heads/release"
    artifact_metadata["git_commit_sha"] = "fedcba9876543210fedcba9876543210fedcba98"
    paths = []
    for index, payload in enumerate((first, second)):
        path = tmp_path / ("release-gate-provenance-%d.json" % index)
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "provenance_binding" in failed_codes


def test_evaluate_release_gate_surfaces_artifact_metadata_provenance_binding_in_summary(
    tmp_path: Path,
) -> None:
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-provenance-summary-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "PASS"
    assert report["summary"]["provenance_binding"] == {
        "repository": "mqueue/Pyrallel-Consumer",
        "git_ref": "refs/heads/develop",
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
    }


def test_evaluate_release_gate_accepts_legacy_artifact_provenance_binding(
    tmp_path: Path,
) -> None:
    payload = _passing_summary()
    payload.pop("artifact_metadata")
    payload["artifact_provenance"] = {
        "repository": "mqueue/Pyrallel-Consumer",
        "git_ref": "refs/heads/develop",
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
    }
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-legacy-provenance-%d.json" % index)
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "PASS"


def test_evaluate_release_gate_surfaces_process_transport_modes_in_summary(
    tmp_path: Path,
) -> None:
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-transport-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "PASS"
    assert report["summary"]["process_transport_modes"] == [
        "shared_queue",
        "worker_pipes",
    ]


def test_evaluate_release_gate_requires_each_process_transport_mode(
    tmp_path: Path,
) -> None:
    summary = _passing_summary()
    results = summary["results"]
    assert isinstance(results, list)
    summary["results"] = [
        result
        for result in results
        if result.get("process_transport_mode") != "worker_pipes"
    ]
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-shared-queue-only-%d.json" % index)
        path.write_text(json.dumps(summary), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_combinations = {
        check["details"]["combination"]
        for check in report["checks"]
        if check["status"] == "FAIL" and check["code"] == "repetitions"
    }
    assert "process/sleep/key_hash/worker_pipes" in failed_combinations


def test_evaluate_release_gate_rejects_process_results_missing_transport_mode(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    results = bad["results"]
    assert isinstance(results, list)
    for result in results:
        if result["run_type"] == "process":
            del result["process_transport_mode"]
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-missing-transport-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "measurement_conditions" in failed_codes


def test_evaluate_release_gate_rejects_unknown_process_transport_mode(
    tmp_path: Path,
) -> None:
    bad = _passing_summary()
    results = bad["results"]
    assert isinstance(results, list)
    for result in results:
        if result.get("process_transport_mode") == "worker_pipes":
            result["process_transport_mode"] = "experimental"
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-unknown-transport-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    report = release_gate.evaluate_release_gate(paths)

    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "measurement_conditions" in failed_codes
    assert "repetitions" in failed_codes


def test_cli_emits_machine_readable_no_go_for_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "benchmarks.release_gate",
            "--benchmark-json",
            str(path),
        ],
        check=False,
        capture_output=True,
        cwd=ROOT,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "NO-GO"
    assert payload["checks"][0]["code"] == "schema"


def test_release_verify_workflow_defers_release_gate_to_benchmark_workflow() -> None:
    text = RELEASE_VERIFY_WORKFLOW.read_text(encoding="utf-8")

    assert "benchmarks.release_gate" not in text
    assert "release-gate-*.json" not in text


BENCHMARK_WORKFLOW = ROOT / ".github" / "workflows" / "benchmarks.yml"


def test_benchmark_workflow_exposes_release_gate_evaluator_job() -> None:
    text = BENCHMARK_WORKFLOW.read_text(encoding="utf-8")

    assert "release_gate_artifacts" in text
    assert "release_gate_artifact_run_id" in text
    assert "actions: read" in text
    assert "actions/download-artifact@v8" in text
    assert "run-id: ${{ inputs.release_gate_artifact_run_id }}" in text
    assert "merge-multiple: true" in text
    assert "benchmarks.release_gate" in text
    assert "--benchmark-json" in text
    assert "shell: bash" in text
    assert "set -o pipefail" in text
    assert "Upload release performance gate verdict" in text
    assert "release-performance-gate-${{ github.run_id }}" in text
