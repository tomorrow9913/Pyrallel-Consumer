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
    process_transport_mode: str = "worker_pipes",
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
                    process_transport_mode=process_transport_mode or "worker_pipes",
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


def _batch_worker_v1_result(
    *,
    run_type: str,
    workload: str,
    ordering: str,
    worker_kind: str,
    metrics_enabled: bool,
    large_payload: bool = False,
) -> dict[str, object]:
    constructor = (
        "PyrallelConsumer.from_batch_worker"
        if worker_kind == "batch_worker"
        else "PyrallelConsumer"
    )
    result = _result(
        run_type=run_type,
        workload=workload,
        ordering=ordering,
        throughput_tps=99999,
        p99_processing_ms=0.1,
    )
    result.update(
        {
            "run_name": "%s-%s-%s-%s-metrics-%s"
            % (
                workload,
                ordering,
                run_type,
                worker_kind,
                "on" if metrics_enabled else "off",
            ),
            "worker_kind": worker_kind,
            "constructor": constructor,
            "metrics_enabled": metrics_enabled,
            "callback_invocation_count": 10,
            "callback_item_count": 10000,
            "rss_max_mb": 64.0,
            "input_ipc_bytes": 1024,
            "completion_ipc_bytes": 512,
            "input_ipc_chunks": 10,
            "completion_ipc_chunks": 10,
            "large_payload": large_payload,
        }
    )
    return result


def _batch_worker_v1_summary() -> dict[str, object]:
    summary = _passing_summary()
    batch_worker_v1_results: list[dict[str, object]] = []
    for run_type in ("async", "process"):
        for workload in ("sleep", "io"):
            for ordering in ("key_hash", "unordered"):
                for worker_kind in ("single_item_worker", "batch_worker"):
                    for metrics_enabled in (False, True):
                        batch_worker_v1_results.append(
                            _batch_worker_v1_result(
                                run_type=run_type,
                                workload=workload,
                                ordering=ordering,
                                worker_kind=worker_kind,
                                metrics_enabled=metrics_enabled,
                                large_payload=(
                                    run_type == "process"
                                    and workload == "io"
                                    and ordering == "unordered"
                                    and worker_kind == "batch_worker"
                                    and metrics_enabled
                                ),
                            )
                        )
    existing_results = summary["results"]
    assert isinstance(existing_results, list)
    summary["results"] = [*existing_results, *batch_worker_v1_results]
    return summary


def _batch_worker_v1_only_summary() -> dict[str, object]:
    summary = _passing_summary()
    batch_worker_v1_results: list[dict[str, object]] = []
    for run_type in ("async", "process"):
        for workload in ("sleep", "io"):
            for ordering in ("key_hash", "unordered"):
                for worker_kind in ("single_item_worker", "batch_worker"):
                    for metrics_enabled in (False, True):
                        batch_worker_v1_results.append(
                            _batch_worker_v1_result(
                                run_type=run_type,
                                workload=workload,
                                ordering=ordering,
                                worker_kind=worker_kind,
                                metrics_enabled=metrics_enabled,
                                large_payload=(
                                    run_type == "process"
                                    and workload == "io"
                                    and ordering == "unordered"
                                    and worker_kind == "batch_worker"
                                    and metrics_enabled
                                ),
                            )
                        )
    summary["results"] = batch_worker_v1_results
    return summary


def test_evaluate_release_gate_passes_two_complete_threshold_runs(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate passes two complete thr...` are prepared.
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate passes two complete thr...` behavior is asserted.
    assert report["verdict"] == "PASS"
    assert report["summary"]["required_repetitions"] == 2
    assert all(check["status"] == "PASS" for check in report["checks"])


def test_evaluate_release_gate_requires_batch_worker_v1_matrix(
    tmp_path: Path,
) -> None:
    path = tmp_path / "batch-worker-v1-complete.json"
    path.write_text(json.dumps(_batch_worker_v1_summary()), encoding="utf-8")

    report = release_gate.evaluate_release_gate(
        [path],
        required_repetitions=1,
        required_matrix="batch-worker-v1",
    )

    assert report["verdict"] == "PASS"
    assert report["summary"]["required_matrix"] == "batch-worker-v1"
    assert any(
        check["code"] == "batch_worker_v1_matrix" and check["status"] == "PASS"
        for check in report["checks"]
    )


def test_evaluate_release_gate_require_matrix_accepts_batch_worker_v1_only_artifact(
    tmp_path: Path,
) -> None:
    payload = _batch_worker_v1_only_summary()
    results = payload["results"]
    assert isinstance(results, list)
    for result in results:
        result["throughput_tps"] = 1
        result["p99_processing_ms"] = 9999
    path = tmp_path / "batch-worker-v1-only.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = release_gate.evaluate_release_gate(
        [path],
        required_repetitions=1,
        required_matrix="batch-worker-v1",
    )

    assert report["verdict"] == "PASS"
    assert [check["code"] for check in report["checks"]] == ["batch_worker_v1_matrix"]


def test_evaluate_release_gate_reports_no_go_for_missing_batch_worker_v1_metrics_pair(
    tmp_path: Path,
) -> None:
    payload = _batch_worker_v1_summary()
    results = payload["results"]
    assert isinstance(results, list)
    payload["results"] = [
        result
        for result in results
        if not (
            result.get("worker_kind") == "batch_worker"
            and result.get("run_type") == "process"
            and result.get("workload") == "io"
            and result.get("ordering") == "unordered"
            and result.get("metrics_enabled") is True
        )
    ]
    path = tmp_path / "batch-worker-v1-missing-metrics-pair.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = release_gate.evaluate_release_gate(
        [path],
        required_repetitions=1,
        required_matrix="batch-worker-v1",
    )

    assert report["verdict"] == "NO-GO"
    failed_checks = [
        check
        for check in report["checks"]
        if check["status"] == "FAIL" and check["code"] == "batch_worker_v1_matrix"
    ]
    assert failed_checks
    assert "metrics" in failed_checks[0]["message"]


def test_evaluate_release_gate_rejects_batch_worker_v1_smoke_artifacts(
    tmp_path: Path,
) -> None:
    payload = _batch_worker_v1_summary()
    options = payload["options"]
    assert isinstance(options, dict)
    options["batch_worker_v1_matrix_smoke_artifact"] = True
    path = tmp_path / "batch-worker-v1-smoke.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    report = release_gate.evaluate_release_gate(
        [path],
        required_repetitions=1,
        required_matrix="batch-worker-v1",
    )

    assert report["verdict"] == "NO-GO"
    assert any(
        check["status"] == "FAIL"
        and check["code"] == "batch_worker_v1_matrix"
        and "smoke" in check["message"]
        for check in report["checks"]
    )


def test_evaluate_release_gate_rejects_non_finite_batch_worker_v1_numeric_evidence(
    tmp_path: Path,
) -> None:
    payload = _batch_worker_v1_summary()
    results = payload["results"]
    assert isinstance(results, list)
    for result in results:
        if result.get("worker_kind") == "batch_worker":
            result["rss_max_mb"] = "NON_FINITE_PLACEHOLDER"
            break
    path = tmp_path / "batch-worker-v1-nan.json"
    path.write_text(
        json.dumps(payload).replace('"NON_FINITE_PLACEHOLDER"', "1e999"),
        encoding="utf-8",
    )

    report = release_gate.evaluate_release_gate(
        [path],
        required_repetitions=1,
        required_matrix="batch-worker-v1",
    )

    assert report["verdict"] == "NO-GO"
    assert any(
        check["status"] == "FAIL"
        and check["code"] == "batch_worker_v1_matrix"
        and check["details"]["field"] == "rss_max_mb"
        for check in report["checks"]
    )


def test_cli_emits_no_go_for_json_constants(tmp_path: Path) -> None:
    path = tmp_path / "non-finite.json"
    path.write_text('{"results": [NaN]}', encoding="utf-8")

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


def test_evaluate_release_gate_reports_no_go_for_threshold_and_completion_failures(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate reports no go for thres...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate reports no go for thres...` behavior is asserted.
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
    # Given: inputs for `evaluate release gate reports no go for persi...` are prepared.
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

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate reports no go for persi...` behavior is asserted.
    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "persistent_gap" in failed_codes


def test_evaluate_release_gate_resets_persistent_gap_timer_per_run_name(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate resets persistent gap t...` are prepared.
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

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate resets persistent gap t...` behavior is asserted.
    assert report["verdict"] == "PASS"
    assert all(
        check["code"] != "persistent_gap" or check["status"] == "PASS"
        for check in report["checks"]
    )


def test_evaluate_release_gate_requires_repeated_full_release_matrix(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate requires repeated full...` are prepared.
    path = tmp_path / "single.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate([path])

    # Then: the expected `evaluate release gate requires repeated full...` behavior is asserted.
    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "repetitions" in failed_codes


def test_evaluate_release_gate_counts_repetitions_by_distinct_artifact(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate counts repetitions by d...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate counts repetitions by d...` behavior is asserted.
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
    # Given: inputs for `cli emits machine readable no go and nonzero...` are prepared.
    path = tmp_path / "single.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    # When: the release gate evaluator code path is exercised.
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

    # Then: the expected `cli emits machine readable no go and nonzero...` behavior is asserted.
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "NO-GO"


def test_evaluate_release_gate_rejects_duplicate_artifact_paths(tmp_path: Path) -> None:
    # Given: inputs for `evaluate release gate rejects duplicate artif...` are prepared.
    path = tmp_path / "release-gate.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate([path, path])

    # Then: the expected `evaluate release gate rejects duplicate artif...` behavior is asserted.
    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "artifacts" in failed_codes


def test_evaluate_release_gate_rejects_invalid_repetition_count(tmp_path: Path) -> None:
    # Given: inputs for `evaluate release gate rejects invalid repetit...` are prepared.
    path = tmp_path / "release-gate.json"
    path.write_text(json.dumps(_passing_summary()), encoding="utf-8")

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate([path], required_repetitions=0)

    # Then: the expected `evaluate release gate rejects invalid repetit...` behavior is asserted.
    assert report["verdict"] == "NO-GO"
    assert report["checks"][0]["code"] == "repetitions"


def test_evaluate_release_gate_reports_schema_failure_for_missing_num_messages(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate reports schema failure...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate reports schema failure...` behavior is asserted.
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
    # Given: inputs for `evaluate release gate requires artifact metad...` are prepared.
    bad = _passing_summary()
    bad.pop("artifact_metadata")
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-no-provenance-%d.json" % index)
        path.write_text(json.dumps(bad), encoding="utf-8")
        paths.append(path)

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate requires artifact metad...` behavior is asserted.
    assert report["verdict"] == "NO-GO"
    failed_codes = {
        check["code"] for check in report["checks"] if check["status"] == "FAIL"
    }
    assert "provenance_binding" in failed_codes


def test_evaluate_release_gate_rejects_mismatched_artifact_metadata_provenance_binding(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate rejects mismatched arti...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate rejects mismatched arti...` behavior is asserted.
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
    # Given: inputs for `evaluate release gate surfaces artifact metad...` are prepared.
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-provenance-summary-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate surfaces artifact metad...` behavior is asserted.
    assert report["verdict"] == "PASS"
    assert report["summary"]["provenance_binding"] == {
        "repository": "mqueue/Pyrallel-Consumer",
        "git_ref": "refs/heads/develop",
        "git_sha": "0123456789abcdef0123456789abcdef01234567",
    }


def test_evaluate_release_gate_accepts_legacy_artifact_provenance_binding(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate accepts legacy artifact...` are prepared.
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

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate accepts legacy artifact...` behavior is asserted.
    assert report["verdict"] == "PASS"


def test_evaluate_release_gate_surfaces_process_transport_modes_in_summary(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate surfaces process transp...` are prepared.
    paths = []
    for index in range(2):
        path = tmp_path / ("release-gate-transport-%d.json" % index)
        path.write_text(json.dumps(_passing_summary()), encoding="utf-8")
        paths.append(path)

    # When: the release gate evaluator code path is exercised.
    report = release_gate.evaluate_release_gate(paths)

    # Then: the expected `evaluate release gate surfaces process transp...` behavior is asserted.
    assert report["verdict"] == "PASS"
    assert report["summary"]["process_transport_modes"] == ["worker_pipes"]


def test_evaluate_release_gate_requires_worker_pipes_transport_mode(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate requires worker pipes t...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate requires worker pipes t...` behavior is asserted.
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


def test_evaluate_release_gate_accepts_process_results_missing_transport_mode(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate accepts process results...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate accepts process results...` behavior is asserted.
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

    assert report["verdict"] == "PASS"
    assert report["summary"]["process_transport_modes"] == ["worker_pipes"]


def test_evaluate_release_gate_rejects_unknown_process_transport_mode(
    tmp_path: Path,
) -> None:
    # Given: inputs for `evaluate release gate rejects unknown process...` are prepared.
    # When: the release gate evaluator code path is exercised.
    # Then: the expected `evaluate release gate rejects unknown process...` behavior is asserted.
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
    # Given: inputs for `cli emits machine readable no go for invalid...` are prepared.
    path = tmp_path / "bad.json"
    path.write_text("{", encoding="utf-8")

    # When: the release gate evaluator code path is exercised.
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

    # Then: the expected `cli emits machine readable no go for invalid...` behavior is asserted.
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["verdict"] == "NO-GO"
    assert payload["checks"][0]["code"] == "schema"


def test_release_verify_workflow_defers_release_gate_to_benchmark_workflow() -> None:
    # Given: inputs for `release verify workflow defers release gate t...` are prepared.
    # When: the release gate evaluator code path is exercised.
    text = RELEASE_VERIFY_WORKFLOW.read_text(encoding="utf-8")

    # Then: the expected `release verify workflow defers release gate t...` behavior is asserted.
    assert "benchmarks.release_gate" not in text
    assert "release-gate-*.json" not in text


BENCHMARK_WORKFLOW = ROOT / ".github" / "workflows" / "benchmarks.yml"


def test_benchmark_workflow_exposes_release_gate_evaluator_job() -> None:
    # Given: inputs for `benchmark workflow exposes release gate evalu...` are prepared.
    # When: the release gate evaluator code path is exercised.
    text = BENCHMARK_WORKFLOW.read_text(encoding="utf-8")

    # Then: the expected `benchmark workflow exposes release gate evalu...` behavior is asserted.
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
