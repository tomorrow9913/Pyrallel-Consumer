from dataclasses import fields
from pathlib import Path

from pyrallel_consumer import (
    PipelineDiagnosticsSnapshot as ExportedPipelineDiagnosticsSnapshot,
)
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.dto import (
    AdaptiveBackpressureSnapshot,
    AdaptiveConcurrencyRuntimeSnapshot,
    DlqRuntimeSnapshot,
    EngineRuntimeDiagnostics,
    PartitionRuntimeSnapshot,
    PipelineAdmissionDiagnostics,
    PipelineDiagnostics,
    PipelineDiagnosticsScope,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSnapshot,
    PipelineDiagnosticsSupportState,
    PipelineDispatchCapacityDiagnostics,
    PipelinePollDiagnostics,
    PipelineSettlementDiagnostics,
    PipelineStage,
    PipelineSubqueueDiagnostics,
    PipelineWorkerDiagnostics,
    PoisonMessageRuntimeSnapshot,
    ProcessBatchMetrics,
    ProcessRuntimeDiagnostics,
    QueueRuntimeSnapshot,
    RetryPolicySnapshot,
    RuntimeSnapshot,
    WorkManagerPipelineDiagnostics,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_CONTRACT_DOC = ROOT / "docs" / "operations" / "public-contract-v1.md"


def test_public_contract_doc_lists_runtime_snapshot_regression_tests() -> None:
    document = PUBLIC_CONTRACT_DOC.read_text(encoding="utf-8")

    expected_tests = [
        "tests/unit/test_public_contract_v1.py",
        "tests/unit/test_consumer.py",
        "tests/unit/control_plane/test_broker_poller_metrics.py",
        "tests/unit/control_plane/test_broker_runtime_support.py",
    ]

    for expected_test in expected_tests:
        assert expected_test in document
        assert (ROOT / expected_test).exists()


def test_public_contract_doc_freezes_runtime_snapshot_field_boundary() -> None:
    document = PUBLIC_CONTRACT_DOC.read_text(encoding="utf-8")

    expected_terms = [
        "Runtime diagnostics field boundary",
        "queue.total_in_flight",
        "queue.configured_max_in_flight",
        "retry.max_retries",
        "dlq.message_cache_entry_count",
        "partitions[].blocking_duration_sec",
        "The retry section is a policy snapshot",
        "does not expose per-message retry attempts",
        "The DLQ section is a configuration/cache snapshot",
        "does not expose a DLQ publish ledger",
        "poison_message.enabled",
        "poison_message.open_circuit_count",
        "adaptive_concurrency.configured_max_in_flight",
        "adaptive_concurrency.effective_max_in_flight",
        "adaptive_backpressure.last_decision",
        "Commit clamping is computed from the control-plane `WorkManager` dispatch ledger",
        "`process_batch_metrics` remains the frozen v1 compatibility projection",
        "Pipeline diagnostics sidecar surface",
        "Pipeline diagnostics stay separate from RuntimeSnapshot v1",
        "Prometheus `pyrallel_pipeline_*` metrics project this sidecar",
        "Process-engine shutdown drain log lines are diagnostic-only reconciliation evidence",
        "not a retry ledger",
        "not be interpreted as commit-safety or DLQ-publish authority",
    ]

    for expected_term in expected_terms:
        assert expected_term in document


def test_runtime_snapshot_public_field_names_remain_stable() -> None:
    assert [field.name for field in fields(RuntimeSnapshot)] == [
        "queue",
        "retry",
        "dlq",
        "partitions",
        "adaptive_backpressure",
        "adaptive_concurrency",
        "process_batch_metrics",
        "poison_message",
    ]
    assert [field.name for field in fields(QueueRuntimeSnapshot)] == [
        "total_in_flight",
        "total_queued",
        "max_in_flight",
        "is_paused",
        "is_rebalancing",
        "ordering_mode",
        "configured_max_in_flight",
    ]
    assert [field.name for field in fields(RetryPolicySnapshot)] == [
        "max_retries",
        "retry_backoff_ms",
        "exponential_backoff",
        "max_retry_backoff_ms",
        "retry_jitter_ms",
    ]
    assert [field.name for field in fields(DlqRuntimeSnapshot)] == [
        "enabled",
        "topic",
        "payload_mode",
        "message_cache_size_bytes",
        "message_cache_entry_count",
    ]
    assert [field.name for field in fields(AdaptiveConcurrencyRuntimeSnapshot)] == [
        "configured_max_in_flight",
        "effective_max_in_flight",
        "min_in_flight",
        "scale_up_step",
        "scale_down_step",
        "cooldown_ms",
    ]
    assert [field.name for field in fields(AdaptiveBackpressureSnapshot)] == [
        "configured_max_in_flight",
        "effective_max_in_flight",
        "min_in_flight",
        "scale_up_step",
        "scale_down_step",
        "cooldown_ms",
        "lag_scale_up_threshold",
        "low_latency_threshold_ms",
        "high_latency_threshold_ms",
        "last_decision",
        "avg_completion_latency_seconds",
    ]
    assert [field.name for field in fields(PoisonMessageRuntimeSnapshot)] == [
        "enabled",
        "failure_threshold",
        "cooldown_ms",
        "open_circuit_count",
    ]
    assert [field.name for field in fields(PartitionRuntimeSnapshot)] == [
        "tp",
        "current_epoch",
        "last_committed_offset",
        "last_fetched_offset",
        "true_lag",
        "gaps",
        "blocking_offset",
        "blocking_duration_sec",
        "queued_count",
        "in_flight_count",
        "min_in_flight_offset",
    ]


def test_engine_runtime_diagnostics_envelope_is_additive_to_v1_snapshot() -> None:
    diagnostics = EngineRuntimeDiagnostics(
        engine_type="process",
        process=ProcessRuntimeDiagnostics(
            batch_metrics=ProcessBatchMetrics(
                size_flush_count=1,
                timer_flush_count=0,
                close_flush_count=0,
                total_flushed_items=1,
                last_flush_size=1,
                last_flush_wait_seconds=0.0,
                buffered_items=0,
                buffered_age_seconds=0.0,
            )
        ),
    )

    assert diagnostics.engine_type == "process"
    assert diagnostics.process is not None
    assert diagnostics.process.batch_metrics.size_flush_count == 1


def test_pipeline_poll_diagnostics_are_additive_sidecar_not_runtime_snapshot() -> None:
    assert [field.name for field in fields(PipelinePollDiagnostics)] == [
        "records_total",
        "nonempty_polls_total",
        "empty_polls_total",
        "error_polls_total",
        "broker_kind",
        "support_state",
    ]
    assert "poll" in [field.name for field in fields(WorkManagerPipelineDiagnostics)]
    assert "poll" not in [field.name for field in fields(RuntimeSnapshot)]


def test_pipeline_diagnostics_public_sidecar_field_names_remain_stable() -> None:
    assert PipelineDiagnostics is WorkManagerPipelineDiagnostics
    assert PipelineDiagnosticsSnapshot is WorkManagerPipelineDiagnostics
    assert ExportedPipelineDiagnosticsSnapshot is WorkManagerPipelineDiagnostics
    assert [field.name for field in fields(WorkManagerPipelineDiagnostics)] == [
        "stage_counts",
        "blocked_counts",
        "dispatch_capacity",
        "admission",
        "workers",
        "subqueues",
        "stage_support",
        "section_support",
        "scope",
        "settlement",
        "poll",
    ]
    assert [field.name for field in fields(PipelineDispatchCapacityDiagnostics)] == [
        "blocked_items",
        "reason",
        "oldest_age_ms",
    ]
    assert [field.name for field in fields(PipelineAdmissionDiagnostics)] == [
        "blocked_items",
        "reason",
        "oldest_age_ms",
        "support_state",
    ]
    assert [field.name for field in fields(PipelineWorkerDiagnostics)] == [
        "total",
        "executing",
        "admitted",
        "top_k_loads",
        "support_state",
    ]
    assert [field.name for field in fields(PipelineSubqueueDiagnostics)] == [
        "total",
        "queued",
        "queued_items",
        "eligible_subqueues",
        "eligible_items",
        "blocked_subqueues",
        "blocked_items",
        "top_k_depths",
    ]
    assert [field.name for field in fields(PipelineSettlementDiagnostics)] == [
        "completed_unsettled",
        "oldest_age_ms",
        "blocker_reason",
        "support_state",
    ]


def test_pipeline_diagnostics_public_sidecar_uses_bounded_enums() -> None:
    assert [stage.value for stage in PipelineStage] == [
        "acquired",
        "buffered",
        "queued",
        "dispatched",
        "executing",
        "completed_unsettled",
        "failed",
        "dlq",
    ]
    assert [state.value for state in PipelineDiagnosticsSupportState] == [
        "supported",
        "unavailable",
        "not_implemented",
    ]
    assert [section.value for section in PipelineDiagnosticsSection] == [
        "stages",
        "blocked",
        "subqueues",
        "dispatch_capacity",
        "admission",
        "workers",
        "settlement",
        "poll",
    ]
    assert PipelineDiagnosticsScope.WORK_MANAGER_ONLY.value == "work_manager_only"
    assert PipelineDiagnosticsScope.COMBINED.value == "combined"
    assert (
        PipelineDiagnosticsScope.COMBINED_INTERNAL is PipelineDiagnosticsScope.COMBINED
    )
    assert (
        PipelineDiagnosticsScope("combined_internal")
        is PipelineDiagnosticsScope.COMBINED
    )


def test_pipeline_diagnostics_public_api_docstrings_are_stable_not_experimental() -> (
    None
):
    for docstring in (
        PyrallelConsumer.get_pipeline_diagnostics.__doc__,
        BrokerPoller.get_pipeline_diagnostics.__doc__,
        WorkManagerPipelineDiagnostics.__doc__,
    ):
        assert docstring is not None
        lowered = docstring.lower()
        assert "stable" in lowered or "supported" in lowered
        assert "experimental" not in lowered
        assert "internal" not in lowered
