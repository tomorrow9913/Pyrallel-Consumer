from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    EngineRuntimeDiagnostics,
    EngineWorkerDiagnostics,
    OrderingMode,
    PipelineAdmissionDiagnostics,
    PipelineBlockedReason,
    PipelineCount,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSupportState,
    PipelineDispatchCapacityDiagnostics,
    PipelineStage,
    PipelineSubqueueDiagnostics,
    PipelineWorkerDiagnostics,
    TopicPartition,
    WorkItem,
    WorkManagerPipelineDiagnostics,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


class _NoopEngine(BaseExecutionEngine):
    """Minimal execution engine used to synthesize diagnostics evidence."""

    def __init__(
        self,
        runtime_metrics: Optional[EngineRuntimeDiagnostics] = None,
    ) -> None:
        self.submitted_offsets: list[int] = []
        self._runtime_metrics = runtime_metrics

    async def submit(self, work_item: WorkItem) -> None:
        """Record submitted offsets without executing user work."""
        self.submitted_offsets.append(work_item.offset)

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> list[CompletionEvent]:
        """Return no completions for evidence scenarios."""
        return []

    async def wait_for_completion(
        self, timeout_seconds: Optional[float] = None
    ) -> bool:
        """Report that no asynchronous work completed."""
        return False

    def get_in_flight_count(self) -> int:
        """Return an empty in-flight count for evidence scenarios."""
        return 0

    def get_runtime_metrics(self) -> Optional[EngineRuntimeDiagnostics]:
        """Return configured synthetic engine diagnostics."""
        return self._runtime_metrics

    async def shutdown(self) -> None:
        """Satisfy the execution engine shutdown contract."""
        return None


class _DiagnosticsWorkManager:
    """Small WorkManager facade that returns a prepared diagnostics snapshot."""

    def __init__(self, diagnostics: WorkManagerPipelineDiagnostics) -> None:
        self._diagnostics = diagnostics

    def get_ordering_mode(self) -> OrderingMode:
        """Return a deterministic ordering mode for poller construction."""
        return OrderingMode.KEY_HASH

    def get_total_in_flight_count(self) -> int:
        """Return no submitted work for evidence scenarios."""
        return 0

    def get_virtual_queue_sizes(self) -> dict[TopicPartition, dict[object, int]]:
        """Return no virtual queue sizes for evidence scenarios."""
        return {}

    def get_pipeline_diagnostics(self) -> WorkManagerPipelineDiagnostics:
        """Return the prepared WorkManager-owned diagnostics snapshot."""
        return self._diagnostics


def _json_ready(value: Any) -> Any:
    """Convert dataclasses and enums into JSON-serializable values."""
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return _json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(_json_ready(key)): _json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_ready(item) for item in value]
    return value


def _empty_diagnostics() -> WorkManagerPipelineDiagnostics:
    """Build a diagnostics snapshot with no observed pipeline activity."""
    return WorkManagerPipelineDiagnostics(
        stage_counts={stage: PipelineCount(count=0) for stage in PipelineStage},
        blocked_counts={
            reason: PipelineCount(count=0) for reason in PipelineBlockedReason
        },
        dispatch_capacity=PipelineDispatchCapacityDiagnostics(blocked_items=0),
        admission=PipelineAdmissionDiagnostics(blocked_items=0),
        workers=PipelineWorkerDiagnostics(total=0, executing=0),
        subqueues=PipelineSubqueueDiagnostics(
            total=0,
            queued=0,
            queued_items=0,
            eligible_subqueues=0,
            eligible_items=0,
            blocked_subqueues=0,
            blocked_items=0,
            top_k_depths=[],
        ),
        stage_support={
            stage: PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
            for stage in PipelineStage
        },
        section_support={
            section: PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
            for section in PipelineDiagnosticsSection
        },
    )


def _setup_work_manager(
    *,
    engine: BaseExecutionEngine,
    tp: TopicPartition,
    max_in_flight: int = 100,
) -> WorkManager:
    """Create a WorkManager with one assigned partition for evidence scenarios."""
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=max_in_flight,
        ordering_mode=OrderingMode.KEY_HASH,
    )
    work_manager.on_assign([tp])
    work_manager._offset_trackers[tp] = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    return work_manager


def _make_poller(
    *,
    diagnostics: Optional[WorkManagerPipelineDiagnostics] = None,
    runtime_metrics: Optional[EngineRuntimeDiagnostics] = None,
) -> BrokerPoller:
    """Create a BrokerPoller around synthetic WorkManager and engine diagnostics."""
    config = KafkaConfig(_env_file=None)
    engine = _NoopEngine(runtime_metrics=runtime_metrics)
    work_manager = _DiagnosticsWorkManager(diagnostics or _empty_diagnostics())
    return BrokerPoller(
        consume_topic="evidence-topic",
        kafka_config=config,
        execution_engine=engine,
        work_manager=work_manager,  # type: ignore[arg-type]
    )


def _summarize_snapshot(diagnostics: WorkManagerPipelineDiagnostics) -> dict[str, Any]:
    """Flatten a diagnostics snapshot into stable evidence JSON fields."""
    return {
        "scope": diagnostics.scope.value,
        "stages": {
            stage.value: diagnostics.stage_counts[stage].count
            for stage in PipelineStage
        },
        "blocked": {
            reason.value: diagnostics.blocked_counts[reason].count
            for reason in PipelineBlockedReason
        },
        "dispatch_capacity": _json_ready(diagnostics.dispatch_capacity),
        "admission": _json_ready(diagnostics.admission),
        "workers": _json_ready(diagnostics.workers),
        "settlement": _json_ready(diagnostics.settlement),
        "subqueues": _json_ready(diagnostics.subqueues),
        "section_support": {
            section.value: diagnostics.section_support.get(
                section,
                PipelineDiagnosticsSupportState.NOT_IMPLEMENTED,
            ).value
            for section in PipelineDiagnosticsSection
        },
        "stage_support": {
            stage.value: diagnostics.stage_support.get(
                stage,
                PipelineDiagnosticsSupportState.NOT_IMPLEMENTED,
            ).value
            for stage in PipelineStage
        },
    }


async def _normal_snapshot(tp: TopicPartition) -> WorkManagerPipelineDiagnostics:
    """Return an idle WorkManager diagnostics snapshot."""
    work_manager = _setup_work_manager(engine=_NoopEngine(), tp=tp)
    return work_manager.get_pipeline_diagnostics()


async def _ordering_lock_snapshot(tp: TopicPartition) -> WorkManagerPipelineDiagnostics:
    """Return a snapshot with one key-ordered item blocked behind another."""
    engine = _NoopEngine()
    work_manager = _setup_work_manager(engine=engine, tp=tp)
    await work_manager.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await work_manager.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await work_manager.schedule()
    return work_manager.get_pipeline_diagnostics()


async def _max_in_flight_snapshot(tp: TopicPartition) -> WorkManagerPipelineDiagnostics:
    """Return a snapshot showing max-in-flight dispatch pressure."""
    engine = _NoopEngine()
    work_manager = _setup_work_manager(engine=engine, tp=tp, max_in_flight=1)
    await work_manager.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await work_manager.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await work_manager.schedule()
    return work_manager.get_pipeline_diagnostics()


def _worker_occupancy_snapshot() -> WorkManagerPipelineDiagnostics:
    """Return a composed snapshot with process worker occupancy diagnostics."""
    diagnostics = _empty_diagnostics()
    runtime_metrics = EngineRuntimeDiagnostics(
        engine_type="process",
        workers=EngineWorkerDiagnostics(
            total=2,
            executing=1,
            admitted=1,
            top_k_loads=[3, 1],
        ),
    )
    return _make_poller(
        diagnostics=diagnostics,
        runtime_metrics=runtime_metrics,
    ).get_pipeline_diagnostics()


def _commit_pending_snapshot(tp: TopicPartition) -> WorkManagerPipelineDiagnostics:
    """Return a snapshot with one completed item waiting for commit."""
    poller = _make_poller()
    poller._dirty_commit_partitions.add(tp)
    poller._unsettled_completions_by_partition[tp] = 1
    poller._completions_since_last_commit = 0
    return poller.get_pipeline_diagnostics()


def _dlq_publish_pending_snapshot(tp: TopicPartition) -> WorkManagerPipelineDiagnostics:
    """Return a snapshot with one terminal failure waiting for DLQ publish."""
    poller = _make_poller()
    poller._pending_dlq_events[(tp, 12)] = CompletionEvent(
        id="evidence-12",
        tp=tp,
        offset=12,
        epoch=0,
        status=CompletionStatus.FAILURE,
        error="synthetic evidence failure",
        attempt=3,
    )
    return poller.get_pipeline_diagnostics()


async def build_evidence() -> dict[str, Any]:
    """Build the pipeline diagnostics evidence artifact content."""
    tp = TopicPartition("evidence-topic", 0)
    scenario_builders = {
        "normal": await _normal_snapshot(tp),
        "ordering_lock": await _ordering_lock_snapshot(tp),
        "max_in_flight_dispatch_capacity": await _max_in_flight_snapshot(tp),
        "process_worker_occupancy": _worker_occupancy_snapshot(),
        "settlement_commit_pending": _commit_pending_snapshot(tp),
        "dlq_publish_pending": _dlq_publish_pending_snapshot(tp),
    }
    scenarios = {
        name: _summarize_snapshot(diagnostics)
        for name, diagnostics in scenario_builders.items()
    }
    return {
        "artifact_type": "pipeline_diagnostics_evidence",
        "generated_at_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "runtime_snapshot_v1_changed": False,
        "prometheus_metrics_added": True,
        "scenarios": scenarios,
        "bounded_prometheus_candidates": {
            "stage_counts": {
                "metric": "pyrallel_pipeline_stage_messages",
                "labels": ["stage", "engine_type"],
                "source": "stage_counts for supported stages only",
            },
            "blocked_reasons": {
                "metric": "pyrallel_pipeline_blocked_messages",
                "labels": ["reason", "engine_type"],
                "source": "blocked_counts bounded enum reasons",
            },
            "dispatch_capacity": {
                "metric": "pyrallel_pipeline_dispatch_capacity_blocked_messages",
                "labels": ["reason", "engine_type"],
                "source": "dispatch_capacity when reason is not null and section is supported",
            },
            "workers_support": {
                "metric": "pyrallel_pipeline_section_support_state",
                "labels": ["section", "state", "engine_type"],
                "source": "section_support, including workers",
            },
            "worker_capacity": {
                "metric": "pyrallel_pipeline_worker_capacity_units",
                "labels": ["state", "engine_type"],
                "source": "aggregate workers.total/executing/admitted when workers are supported",
            },
            "settlement_support": {
                "metric": "pyrallel_pipeline_section_support_state",
                "labels": ["section", "state", "engine_type"],
                "source": "section_support, including settlement",
            },
        },
        "snapshot_debug_only_fields": [
            "subqueues.top_k_depths",
            "workers.top_k_loads",
            "settlement.oldest_age_ms",
            "stage_counts[].oldest_age_ms",
            "blocked_counts[].oldest_age_ms",
            "raw TopicPartition/topic/partition/offset/key/error context",
        ],
        "forbidden_prometheus_labels": [
            "topic",
            "partition",
            "key",
            "route",
            "worker_id",
            "subqueue_id",
            "offset",
            "exception_text",
        ],
    }


def write_evidence(path: Path) -> dict[str, Any]:
    """Write the pipeline diagnostics evidence artifact to disk."""
    evidence = asyncio.run(build_evidence())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(_json_ready(evidence), indent=2, sort_keys=True), encoding="utf-8"
    )
    return evidence


def main() -> None:
    """Parse CLI arguments and regenerate the evidence artifact."""
    parser = argparse.ArgumentParser(
        description="Generate #131 pipeline diagnostics sidecar evidence JSON."
    )
    parser.add_argument(
        "--output",
        default="benchmarks/results/pipeline_diagnostics_evidence.json",
        help="Path for the evidence JSON artifact.",
    )
    args = parser.parse_args()
    evidence = write_evidence(Path(args.output))
    print(
        json.dumps(
            _json_ready(
                {"output": args.output, "scenarios": list(evidence["scenarios"])}
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
