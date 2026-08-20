# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_runtime_support.py
# Role: Projects WorkManager, Kafka, and execution-engine state into runtime metrics snapshots.
# Extend here for diagnostic projection; keep adaptive policy decisions in adaptive modules.
from __future__ import annotations

from dataclasses import replace
from typing import Any

from pyrallel_consumer.dto import (
    AdaptiveBackpressureSnapshot,
    AdaptiveConcurrencyRuntimeSnapshot,
    DLQPayloadMode,
    DlqRuntimeSnapshot,
    EngineRuntimeDiagnostics,
    OrderingMode,
    PartitionMetrics,
    PartitionRuntimeSnapshot,
    PipelineCount,
    PipelineDiagnosticsScope,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSupportState,
    PipelinePollDiagnostics,
    PipelineSettlementBlockerReason,
    PipelineSettlementDiagnostics,
    PipelineStage,
    PipelineWorkerDiagnostics,
    PoisonMessageRuntimeSnapshot,
    ProcessBatchMetrics,
    ProcessRuntimeDiagnostics,
    QueueRuntimeSnapshot,
    RetryPolicySnapshot,
    RuntimeSnapshot,
    SystemMetrics,
    WorkManagerPipelineDiagnostics,
)


class BrokerRuntimeSupport:
    """Group helper operations for runtime metric projection."""

    def __init__(
        self,
        *,
        work_manager: Any,
        offset_trackers: dict[Any, Any],
        consumer: Any,
        execution_engine: Any = None,
        execution_config: Any = None,
        consume_topic: str = "",
        ordering_mode: OrderingMode = OrderingMode.KEY_HASH,
        dlq_enabled: bool = False,
        dlq_topic_suffix: str = "",
        dlq_payload_mode: DLQPayloadMode = DLQPayloadMode.FULL,
        message_cache_size_bytes: int = 0,
        message_cache_entry_count: int = 0,
        max_in_flight_messages: int,
        min_in_flight_messages_to_resume: int,
        queue_max_messages: int,
        queue_resume_threshold: int,
        is_paused: bool,
        blocking_warn_seconds: float,
        logger: Any,
        configured_max_in_flight_messages: int | None = None,
        adaptive_backpressure: AdaptiveBackpressureSnapshot | None = None,
        adaptive_concurrency: AdaptiveConcurrencyRuntimeSnapshot | None = None,
        poison_message_config: Any = None,
        poison_message_open_circuit_count: int = 0,
        dirty_commit_partitions: set[Any] | None = None,
        unsettled_completions_by_partition: dict[Any, int] | None = None,
        pending_dlq_events: dict[Any, Any] | None = None,
        pipeline_poll_records_total: int = 0,
        pipeline_poll_nonempty_total: int = 0,
        pipeline_poll_empty_total: int = 0,
        pipeline_poll_error_total: int = 0,
        pipeline_completed_offset_skips_total: int = 0,
    ) -> None:
        """Initialize this component.

        Args:
            work_manager: Work manager used for scheduling and accounting.
            offset_trackers: Offset trackers value used to initialize this component.
            consumer: Consumer value used to initialize this component.
            execution_engine: Execution engine used to process scheduled work.
            execution_config: Execution config value used to initialize this component.
            consume_topic: Consume topic value used to initialize this component.
            ordering_mode: Ordering mode used by the scheduler.
            dlq_enabled: Dlq enabled value used to initialize this component.
            dlq_topic_suffix: Dlq topic suffix value used to initialize this component.
            dlq_payload_mode: Dlq payload mode value used to initialize this component.
            message_cache_size_bytes: Message cache size bytes value used to initialize this component.
            message_cache_entry_count: Message cache entry count value used to initialize this component.
            max_in_flight_messages: Maximum number of in-flight work items.
            min_in_flight_messages_to_resume: Min in flight messages to resume value used to initialize this component.
            queue_max_messages: Queue max messages value used to initialize this component.
            queue_resume_threshold: Queue resume threshold value used to initialize this component.
            is_paused: Is paused value used to initialize this component.
            blocking_warn_seconds: Blocking warn seconds value used to initialize this component.
            logger: Logger used for diagnostics.
            configured_max_in_flight_messages: Configured max in flight messages value used to initialize this component.
            adaptive_backpressure: Adaptive backpressure value used to initialize this component.
            adaptive_concurrency: Adaptive concurrency value used to initialize this component.
            poison_message_config: Poison message config value used to initialize this component.
            poison_message_open_circuit_count: Poison message open circuit count value used to initialize this component.
            dirty_commit_partitions: Partitions waiting for commit settlement.
            unsettled_completions_by_partition: Completion counts pending commit by partition.
            pending_dlq_events: Terminal failures waiting for DLQ publication.
            pipeline_poll_records_total: Total records observed by broker polling.
            pipeline_poll_nonempty_total: Total non-empty broker polls.
            pipeline_poll_empty_total: Total empty broker polls.
            pipeline_poll_error_total: Total broker poll errors.
            pipeline_completed_offset_skips_total: Completed offsets skipped as stale.

        """
        self._work_manager = work_manager
        self._offset_trackers = offset_trackers
        self._consumer = consumer
        self._execution_engine = execution_engine
        self._execution_config = execution_config
        self._consume_topic = consume_topic
        self._ordering_mode = ordering_mode
        self._dlq_enabled = dlq_enabled
        self._dlq_topic_suffix = dlq_topic_suffix
        self._dlq_payload_mode = dlq_payload_mode
        self._message_cache_size_bytes = message_cache_size_bytes
        self._message_cache_entry_count = message_cache_entry_count
        self._max_in_flight_messages = max_in_flight_messages
        self._min_in_flight_messages_to_resume = min_in_flight_messages_to_resume
        self._queue_max_messages = queue_max_messages
        self._queue_resume_threshold = queue_resume_threshold
        self._is_paused = is_paused
        self._blocking_warn_seconds = blocking_warn_seconds
        self._logger = logger
        self._configured_max_in_flight_messages = configured_max_in_flight_messages
        self._adaptive_backpressure = adaptive_backpressure
        self._adaptive_concurrency = adaptive_concurrency
        self._poison_message_config = poison_message_config
        self._poison_message_open_circuit_count = poison_message_open_circuit_count
        self._dirty_commit_partitions = dirty_commit_partitions or set()
        self._unsettled_completions_by_partition = (
            unsettled_completions_by_partition or {}
        )
        self._pending_dlq_events = pending_dlq_events or {}
        self._pipeline_poll_records_total = pipeline_poll_records_total
        self._pipeline_poll_nonempty_total = pipeline_poll_nonempty_total
        self._pipeline_poll_empty_total = pipeline_poll_empty_total
        self._pipeline_poll_error_total = pipeline_poll_error_total
        self._pipeline_completed_offset_skips_total = (
            pipeline_completed_offset_skips_total
        )

    @staticmethod
    def _project_process_batch_metrics(
        metrics: EngineRuntimeDiagnostics | ProcessBatchMetrics | None,
    ) -> ProcessBatchMetrics | None:
        """Handle project process batch metrics within runtime metric projection.

        Args:
            metrics: Metrics value used by this function.

        Returns:
            ProcessBatchMetrics | None result produced by this function.

        """
        if metrics is None:
            return None
        if isinstance(metrics, ProcessBatchMetrics):
            return metrics
        if isinstance(metrics, EngineRuntimeDiagnostics) and isinstance(
            metrics.process, ProcessRuntimeDiagnostics
        ):
            return metrics.process.batch_metrics
        return None

    def log_partition_diagnostics(self) -> None:
        """Handle log partition diagnostics within runtime metric projection."""
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        gaps = self._work_manager.get_gaps()
        blocking = self._work_manager.get_blocking_offsets()
        parts: list[str] = []
        for tp, tracker in self._offset_trackers.items():
            queued = sum(queue_sizes.get(tp, {}).values())
            gap_count = len(gaps.get(tp, []))
            blocking_offset = blocking.get(tp)
            blocking_age = None
            if blocking_offset is not None:
                durations = tracker.get_blocking_offset_durations()
                blocking_age = durations.get(blocking_offset.start)
            age_str = (
                "%.2fs" % blocking_age
                if isinstance(blocking_age, (int, float))
                else "n/a"
            )
            parts.append(
                "%s-%d queued=%d gaps=%d blocking=%s age=%s"
                % (
                    tp.topic,
                    tp.partition,
                    queued,
                    gap_count,
                    blocking_offset.start if blocking_offset else "none",
                    age_str,
                )
            )
            if (
                blocking_offset is not None
                and isinstance(blocking_age, (int, float))
                and blocking_age >= self._blocking_warn_seconds
            ):
                self._logger.warning(
                    "Prolonged blocking offset %s@%d age=%.2fs gaps=%d queued=%d",
                    tp,
                    blocking_offset.start,
                    blocking_age,
                    gap_count,
                    queued,
                )
        self._logger.debug("Partition diag: %s", "; ".join(parts))

    def check_backpressure(self, *, total_queued: int) -> bool:
        """Handle check backpressure within runtime metric projection.

        Args:
            total_queued: Total number of queued messages.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        Raises:
            RuntimeError: If the runtime is in a failed or unsupported state.

        """
        if self._consumer is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks")

        total_in_flight = self._work_manager.get_total_in_flight_count()
        current_load = total_in_flight + total_queued
        partitions = self._consumer.assignment()
        if not partitions:
            return self._is_paused

        queue_full = (
            self._queue_max_messages > 0 and total_queued >= self._queue_max_messages
        )

        if not self._is_paused and (
            current_load > self._max_in_flight_messages or queue_full
        ):
            self._logger.warning(
                "Backpressure: pausing consumer (load=%d limit=%d)",
                current_load,
                self._max_in_flight_messages,
            )
            self._consumer.pause(partitions)
            return True

        if self._is_paused and (
            current_load < self._min_in_flight_messages_to_resume
            and (
                self._queue_resume_threshold == 0
                or total_queued <= self._queue_resume_threshold
            )
        ):
            self._logger.debug(
                "Backpressure released: resuming consumer (load=%d resume=%d)",
                current_load,
                self._min_in_flight_messages_to_resume,
            )
            self._consumer.resume(partitions)
            return False

        return self._is_paused

    def build_system_metrics(self) -> SystemMetrics:
        """Build system metrics for runtime metric projection.

        Returns:
            Current system metrics snapshot.

        """
        partition_metrics_list: list[PartitionMetrics] = []
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        for tp, tracker in self._offset_trackers.items():
            true_lag = max(
                0, tracker.last_fetched_offset - tracker.last_committed_offset
            )
            gaps = tracker.get_gaps()
            gap_count = len(gaps)
            blocking_offset = gaps[0].start if gaps else None
            durations = tracker.get_blocking_offset_durations()
            blocking_duration = (
                durations.get(blocking_offset) if blocking_offset is not None else None
            )
            queued_count = sum(queue_sizes.get(tp, {}).values())
            partition_metrics_list.append(
                PartitionMetrics(
                    tp=tp,
                    true_lag=true_lag,
                    gap_count=gap_count,
                    blocking_offset=blocking_offset,
                    blocking_duration_sec=blocking_duration,
                    queued_count=queued_count,
                )
            )

        return SystemMetrics(
            total_in_flight=self._work_manager.get_total_in_flight_count(),
            is_paused=self._is_paused,
            partitions=partition_metrics_list,
            adaptive_backpressure=self._adaptive_backpressure,
            adaptive_concurrency=self._adaptive_concurrency,
        )

    def build_runtime_snapshot(self) -> RuntimeSnapshot:
        """Build runtime snapshot for runtime metric projection.

        Returns:
            Current runtime snapshot.

        """
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        in_flight_counts = self._work_manager.get_in_flight_counts()
        partition_snapshots: list[PartitionRuntimeSnapshot] = []

        for tp in sorted(
            self._offset_trackers,
            key=lambda current_tp: (current_tp.topic, current_tp.partition),
        ):
            tracker = self._offset_trackers[tp]
            gaps = tracker.get_gaps()
            blocking_offset = gaps[0].start if gaps else None
            durations = tracker.get_blocking_offset_durations()
            partition_snapshots.append(
                PartitionRuntimeSnapshot(
                    tp=tp,
                    current_epoch=tracker.get_current_epoch(),
                    last_committed_offset=tracker.last_committed_offset,
                    last_fetched_offset=tracker.last_fetched_offset,
                    true_lag=max(
                        0, tracker.last_fetched_offset - tracker.last_committed_offset
                    ),
                    gaps=gaps,
                    blocking_offset=blocking_offset,
                    blocking_duration_sec=(
                        durations.get(blocking_offset)
                        if blocking_offset is not None
                        else None
                    ),
                    queued_count=sum(queue_sizes.get(tp, {}).values()),
                    in_flight_count=in_flight_counts.get(tp, 0),
                    min_in_flight_offset=self._work_manager.get_min_in_flight_offset(
                        tp
                    ),
                )
            )

        runtime_diagnostics = (
            self._execution_engine.get_runtime_metrics()
            if self._execution_engine is not None
            else None
        )
        poison_message_snapshot = None
        if self._poison_message_config is not None:
            poison_message_snapshot = PoisonMessageRuntimeSnapshot(
                enabled=bool(getattr(self._poison_message_config, "enabled", False)),
                failure_threshold=int(
                    getattr(self._poison_message_config, "failure_threshold", 0)
                ),
                cooldown_ms=int(getattr(self._poison_message_config, "cooldown_ms", 0)),
                open_circuit_count=self._poison_message_open_circuit_count,
            )
        return RuntimeSnapshot(
            queue=QueueRuntimeSnapshot(
                total_in_flight=self._work_manager.get_total_in_flight_count(),
                total_queued=self._work_manager.get_total_queued_messages(),
                max_in_flight=self._max_in_flight_messages,
                is_paused=self._is_paused,
                is_rebalancing=self._work_manager.is_rebalancing(),
                ordering_mode=self._ordering_mode,
                configured_max_in_flight=self._configured_max_in_flight_messages,
            ),
            retry=RetryPolicySnapshot(
                max_retries=getattr(self._execution_config, "max_retries", 0),
                retry_backoff_ms=getattr(self._execution_config, "retry_backoff_ms", 0),
                exponential_backoff=bool(
                    getattr(self._execution_config, "exponential_backoff", False)
                ),
                max_retry_backoff_ms=getattr(
                    self._execution_config, "max_retry_backoff_ms", 0
                ),
                retry_jitter_ms=getattr(self._execution_config, "retry_jitter_ms", 0),
            ),
            dlq=DlqRuntimeSnapshot(
                enabled=self._dlq_enabled,
                topic=self._consume_topic + self._dlq_topic_suffix,
                payload_mode=self._dlq_payload_mode,
                message_cache_size_bytes=self._message_cache_size_bytes,
                message_cache_entry_count=self._message_cache_entry_count,
            ),
            partitions=partition_snapshots,
            adaptive_backpressure=self._adaptive_backpressure,
            adaptive_concurrency=self._adaptive_concurrency,
            process_batch_metrics=self._project_process_batch_metrics(
                runtime_diagnostics
            ),
            poison_message=poison_message_snapshot,
        )

    def compose_pipeline_diagnostics(
        self,
        diagnostics: WorkManagerPipelineDiagnostics,
        runtime_metrics: EngineRuntimeDiagnostics | None,
    ) -> WorkManagerPipelineDiagnostics:
        """Compose WorkManager sidecar data with broker and engine diagnostics."""
        completed_unsettled = sum(
            self._unsettled_completions_by_partition.get(tp, 0)
            for tp in self._dirty_commit_partitions
        )
        pending_dlq_count = len(self._pending_dlq_events)
        settlement_blocker_reason = None
        if pending_dlq_count > 0:
            settlement_blocker_reason = (
                PipelineSettlementBlockerReason.DLQ_PUBLISH_PENDING
            )
        elif completed_unsettled > 0:
            settlement_blocker_reason = PipelineSettlementBlockerReason.COMMIT_PENDING

        stage_counts = dict(diagnostics.stage_counts)
        stage_counts[PipelineStage.COMPLETED_UNSETTLED] = PipelineCount(
            count=completed_unsettled,
            oldest_age_ms=None,
        )
        stage_counts[PipelineStage.DLQ] = PipelineCount(
            count=pending_dlq_count,
            oldest_age_ms=None,
        )
        stage_support = dict(diagnostics.stage_support)
        stage_support[PipelineStage.COMPLETED_UNSETTLED] = (
            PipelineDiagnosticsSupportState.SUPPORTED
        )
        stage_support[PipelineStage.DLQ] = PipelineDiagnosticsSupportState.SUPPORTED
        section_support = dict(diagnostics.section_support)
        section_support[PipelineDiagnosticsSection.SETTLEMENT] = (
            PipelineDiagnosticsSupportState.SUPPORTED
        )
        section_support[PipelineDiagnosticsSection.POLL] = (
            PipelineDiagnosticsSupportState.SUPPORTED
        )

        workers = diagnostics.workers
        if runtime_metrics is not None and runtime_metrics.workers is not None:
            section_support[PipelineDiagnosticsSection.WORKERS] = (
                PipelineDiagnosticsSupportState.SUPPORTED
            )
            workers = PipelineWorkerDiagnostics(
                total=runtime_metrics.workers.total,
                executing=runtime_metrics.workers.executing,
                admitted=runtime_metrics.workers.admitted,
                top_k_loads=list(runtime_metrics.workers.top_k_loads),
                support_state=PipelineDiagnosticsSupportState.SUPPORTED,
            )

        settlement = PipelineSettlementDiagnostics(
            completed_unsettled=completed_unsettled,
            oldest_age_ms=None,
            blocker_reason=settlement_blocker_reason,
            support_state=PipelineDiagnosticsSupportState.SUPPORTED,
        )
        return replace(
            diagnostics,
            stage_counts=stage_counts,
            stage_support=stage_support,
            settlement=settlement,
            workers=workers,
            poll=PipelinePollDiagnostics(
                records_total=self._pipeline_poll_records_total,
                nonempty_polls_total=self._pipeline_poll_nonempty_total,
                empty_polls_total=self._pipeline_poll_empty_total,
                error_polls_total=self._pipeline_poll_error_total,
                completed_offset_skips_total=self._pipeline_completed_offset_skips_total,
                broker_kind="kafka",
                support_state=PipelineDiagnosticsSupportState.SUPPORTED,
            ),
            section_support=section_support,
            scope=PipelineDiagnosticsScope.COMBINED,
        )
