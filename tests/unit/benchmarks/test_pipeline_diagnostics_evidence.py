from __future__ import annotations

import pytest

from benchmarks.pipeline_diagnostics_evidence import build_evidence, write_evidence


@pytest.mark.asyncio
async def test_pipeline_diagnostics_evidence_distinguishes_bottlenecks() -> None:
    # Given: inputs for `pipeline diagnostics evidence distinguishes b...` are prepared.
    # When: the pipeline diagnostics evidence code path is exercised.
    evidence = await build_evidence()
    scenarios = evidence["scenarios"]

    # Then: the expected `pipeline diagnostics evidence distinguishes b...` behavior is asserted.
    assert evidence["prometheus_metrics_added"] is True
    assert evidence["runtime_snapshot_v1_changed"] is False
    assert scenarios["normal"]["subqueues"]["queued_items"] == 0
    assert scenarios["ordering_lock"]["blocked"]["ordering_lock"] == 1
    assert (
        scenarios["max_in_flight_dispatch_capacity"]["dispatch_capacity"]["reason"]
        == "max_in_flight"
    )
    assert scenarios["process_worker_occupancy"]["workers"]["executing"] == 1
    assert scenarios["process_worker_occupancy"]["workers"]["admitted"] == 1
    assert (
        scenarios["settlement_commit_pending"]["settlement"]["blocker_reason"]
        == "commit_pending"
    )
    assert (
        scenarios["dlq_publish_pending"]["settlement"]["blocker_reason"]
        == "dlq_publish_pending"
    )
    assert scenarios["dlq_publish_pending"]["stages"]["dlq"] == 1
    assert scenarios["dlq_publish_pending"]["stages"]["completed_unsettled"] == 0


def test_pipeline_diagnostics_evidence_writes_json_artifact(tmp_path) -> None:
    # Given: inputs for `pipeline diagnostics evidence writes json art...` are prepared.
    output_path = tmp_path / "pipeline-diagnostics-evidence.json"

    # When: the pipeline diagnostics evidence code path is exercised.
    evidence = write_evidence(output_path)

    # Then: the expected `pipeline diagnostics evidence writes json art...` behavior is asserted.
    output_text = output_path.read_text(encoding="utf-8")
    assert "pipeline_diagnostics_evidence" in output_text
    assert evidence["forbidden_prometheus_labels"] == [
        "topic",
        "partition",
        "key",
        "route",
        "worker_id",
        "subqueue_id",
        "offset",
        "exception_text",
    ]
    for candidate in evidence["bounded_prometheus_candidates"].values():
        assert "topic" not in candidate["labels"]
        assert "partition" not in candidate["labels"]
        assert "offset" not in candidate["labels"]
    assert (
        evidence["bounded_prometheus_candidates"]["worker_capacity"]["metric"]
        == "pyrallel_pipeline_worker_capacity_units"
    )
    assert evidence["bounded_prometheus_candidates"]["worker_capacity"]["labels"] == [
        "state",
        "engine_type",
    ]
    assert "workers.top_k_loads" in evidence["snapshot_debug_only_fields"]
    assert "workers.total" not in evidence["snapshot_debug_only_fields"]
    assert "workers.executing" not in evidence["snapshot_debug_only_fields"]
    assert "workers.admitted" not in evidence["snapshot_debug_only_fields"]
