# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_poller.py
# Role: Owns Kafka consumer runtime orchestration, polling, dispatch, completion, commits, and shutdown.
# Extend here for broker-level coordination; split focused helpers when support logic grows.
"""BrokerPoller - polls Kafka and drives the WorkManager."""

import asyncio
import inspect
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any, Dict, List, Optional, Tuple, cast

from confluent_kafka import Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

from ..config import AdaptiveBackpressureConfig, AdaptiveConcurrencyConfig, KafkaConfig
from ..dto import (
    CompletionEvent,
    DLQPayloadMode,
    OrderingMode,
    RuntimeSnapshot,
    SystemMetrics,
)
from ..dto import TopicPartition as DtoTopicPartition
from ..dto import WorkManagerPipelineDiagnostics
from ..logger import LogManager
from .adaptive_backpressure import AdaptiveBackpressureController
from .adaptive_concurrency import AdaptiveConcurrencyController
from .broker_backpressure_support import BrokerBackpressureSupport
from .broker_commit_coordinator_support import (
    BrokerCommitCadenceSupport,
    BrokerCommitCoordinatorSupport,
)
from .broker_commit_settlement_support import BrokerCommitSettlementSupport
from .broker_completion_monitor_support import BrokerCompletionMonitorSupport
from .broker_completion_support import BrokerCompletionSupport
from .broker_dispatch_support import BrokerDispatchSupport
from .broker_lifecycle_support import BrokerLifecycleSupport
from .broker_operation_guard import BrokerOperationGuard
from .broker_poller_config import (
    coerce_adaptive_backpressure_config,
    coerce_adaptive_concurrency_config,
    resolve_commit_debounce_completion_threshold,
    resolve_commit_debounce_interval_seconds,
    resolve_configured_max_in_flight,
    resolve_ordering_mode,
    resolve_shutdown_drain_timeout_seconds,
    resolve_shutdown_policy,
)
from .broker_poller_dlq import BrokerDlqSupport
from .broker_rebalance_bridge import BrokerRebalanceBridge, RevokePreparation
from .broker_rebalance_orchestration_support import BrokerRebalanceOrchestrationSupport
from .broker_rebalance_support import BrokerRebalanceSupport
from .broker_runtime_support import BrokerRuntimeSupport
from .broker_shutdown_support import BrokerShutdownSupport
from .broker_support import BrokerCommitPlanner
from .broker_task_lifecycle_support import BrokerTaskLifecycleSupport
from .commit_coordinator import CommitCandidate, CommitCoordinator, CommitSettlement
from .commit_coordinator_metrics import CommitCoordinatorMetricsSink
from .metadata_encoder import MetadataEncoder
from .offset_tracker import OffsetTracker
from .poison_message import PoisonMessageCircuitBreaker
from .work_manager import WorkManager

logger = LogManager.get_logger(__name__)


class BrokerPoller:
    """Polls Kafka, feeds WorkManager, coordinates commits."""

    MAX_COMPLETED_OFFSETS_FOR_METADATA = 2048
    COMMIT_FAILURE_REASON_KAFKA_EXCEPTION = "kafka_exception"

    def __init__(
        self,
        consume_topic: str,
        kafka_config: KafkaConfig,
        execution_engine: BaseExecutionEngine,
        work_manager: Optional[WorkManager] = None,
        work_manager_route_batch_size: int | None = None,
    ) -> None:
        """Initialize this component.

        Args:
            consume_topic: Consume topic value used to initialize this component.
            kafka_config: Kafka and parallel-consumer configuration.
            execution_engine: Execution engine used to process scheduled work.
            work_manager: Work manager used for scheduling and accounting.
            work_manager_route_batch_size: Pre-resolved route-batch lease size used
                only when constructing the fallback work manager.

        """
        self._consume_topic = consume_topic
        self._kafka_config = kafka_config
        self._execution_engine = execution_engine
        self._metrics_exporter: Optional[Any] = None
        self._pipeline_completed_offset_skips_total = 0

        pc_conf = self._kafka_config.parallel_consumer
        self._batch_size = getattr(pc_conf, "poll_batch_size", 0) or 0
        self._worker_pool_size = getattr(pc_conf, "worker_pool_size", 0) or 0
        self.QUEUE_MAX_MESSAGES = int(getattr(pc_conf, "queue_max_messages", 0) or 0)
        raw_message_cache_max_bytes = getattr(
            pc_conf, "message_cache_max_bytes", 64 * 1024 * 1024
        )
        self._message_cache_max_bytes = (
            raw_message_cache_max_bytes
            if isinstance(raw_message_cache_max_bytes, int)
            else 64 * 1024 * 1024
        )
        self._queue_resume_threshold = (
            int(self.QUEUE_MAX_MESSAGES * 0.7) if self.QUEUE_MAX_MESSAGES else 0
        )
        config_ordering_mode = resolve_ordering_mode(pc_conf)
        configured_max_in_flight = resolve_configured_max_in_flight(pc_conf.execution)
        get_ordering_mode = getattr(work_manager, "get_ordering_mode", None)
        injected_ordering_mode = (
            get_ordering_mode() if callable(get_ordering_mode) else None
        )
        if isinstance(injected_ordering_mode, OrderingMode):
            self.ORDERING_MODE = injected_ordering_mode
            if injected_ordering_mode != config_ordering_mode:
                logger.warning(
                    "Injected WorkManager ordering_mode %s overrides config ordering_mode %s",
                    injected_ordering_mode.value,
                    config_ordering_mode.value,
                )
        else:
            self.ORDERING_MODE = config_ordering_mode

        self.producer: Optional[Producer] = None
        self.consumer: Optional[Consumer] = None
        self.admin: Optional[AdminClient] = None

        self._running = False
        self._shutdown_event = asyncio.Event()
        self._stop_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._commit_lock = asyncio.Lock()
        self._consumer_operation_guard = BrokerOperationGuard()
        self._completion_monitor_task: Optional[asyncio.Task[None]] = None
        self._consumer_task: Optional[asyncio.Task[None]] = None
        self._event_loop: asyncio.AbstractEventLoop | None = None
        self._defer_consumer_cleanup_for_stop = False
        self._consumer_task_stop_timeout_seconds = (
            pc_conf.execution.consumer_task_stop_timeout_ms / 1000.0
        )
        self._fatal_error: Optional[Exception] = None

        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._metadata_encoder = MetadataEncoder()
        self._rebalance_support = BrokerRebalanceSupport(
            metadata_encoder=self._metadata_encoder,
            tracker_factory=OffsetTracker,
        )
        self._rebalance_bridge = BrokerRebalanceBridge(
            get_event_loop=lambda: self._event_loop,
            timeout_seconds=self._rebalance_bridge_timeout_seconds,
            assign_timeout_seconds=self._assign_bridge_timeout_seconds,
            control_lock=self._control_lock,
            assign_sync=self._assign_sync,
            prepare_revoke_sync=self._prepare_revoke_sync,
            cleanup_revoke_sync=self._cleanup_revoke_sync,
            logger=logger,
        )
        self._commit_planner = BrokerCommitPlanner(
            metadata_encoder=self._metadata_encoder,
            max_completed_offsets=self.MAX_COMPLETED_OFFSETS_FOR_METADATA,
        )
        self._poison_message_config = getattr(pc_conf, "poison_message", None)
        poison_message_circuit = None
        if self._poison_message_config is not None:
            poison_message_circuit = PoisonMessageCircuitBreaker(
                enabled=bool(getattr(self._poison_message_config, "enabled", False)),
                failure_threshold=int(
                    getattr(self._poison_message_config, "failure_threshold", 3)
                ),
                cooldown_ms=int(getattr(self._poison_message_config, "cooldown_ms", 0)),
                forced_failure_attempt=pc_conf.execution.max_retries,
            )
        if work_manager is None:
            if work_manager_route_batch_size is None:
                raise ValueError(
                    "work_manager_route_batch_size is required when BrokerPoller "
                    "constructs a fallback WorkManager"
                )

            work_manager = WorkManager(
                execution_engine=self._execution_engine,
                max_in_flight_messages=configured_max_in_flight,
                ordering_mode=self.ORDERING_MODE,
                blocking_cache_ttl=getattr(pc_conf, "blocking_cache_ttl", 0),
                max_revoke_grace_ms=pc_conf.execution.max_revoke_grace_ms,
                poison_message_circuit=poison_message_circuit,
                route_batch_size=work_manager_route_batch_size,
            )

        self._work_manager = work_manager

        self._diag_log_every = int(getattr(pc_conf, "diag_log_every", 1000) or 1000)
        self._diag_events_since_log = 0
        self._blocking_warn_seconds = float(
            getattr(pc_conf, "blocking_warn_seconds", 5.0) or 5.0
        )
        self._max_blocking_duration_ms = int(
            getattr(pc_conf, "max_blocking_duration_ms", 0) or 0
        )

        self._configured_max_in_flight_messages = configured_max_in_flight
        self.MAX_IN_FLIGHT_MESSAGES = self._configured_max_in_flight_messages
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = max(
            1, int(self.MAX_IN_FLIGHT_MESSAGES * 0.7)
        )
        self._is_paused = False
        adaptive_backpressure_config = self._coerce_adaptive_backpressure_config(
            getattr(pc_conf, "adaptive_backpressure", None)
        )
        self._adaptive_backpressure_controller = AdaptiveBackpressureController(
            configured_max_in_flight=self._configured_max_in_flight_messages,
            config=adaptive_backpressure_config,
        )
        adaptive_concurrency_config = self._coerce_adaptive_concurrency_config(
            pc_conf,
            "adaptive_concurrency",
        )
        self._adaptive_concurrency_controller = AdaptiveConcurrencyController(
            adaptive_concurrency_config,
            configured_max_in_flight=self._configured_max_in_flight_messages,
        )
        self._set_runtime_max_in_flight(
            self.MAX_IN_FLIGHT_MESSAGES,
            log_change=False,
        )

        self._message_cache: (
            "OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]]"
        ) = OrderedDict()
        # BrokerPoller owns pending terminal DLQ failures across transient
        # BrokerCompletionSupport instances; support mutates this ledger while
        # retrying DLQ publication before offsets may be marked complete.
        self._pending_dlq_events: (
            "OrderedDict[Tuple[DtoTopicPartition, int], CompletionEvent]"
        ) = OrderedDict()
        self._message_cache_size_bytes = 0
        self._dlq_support = BrokerDlqSupport(
            consume_topic=self._consume_topic,
            get_kafka_config=lambda: self._kafka_config,
            get_producer=lambda: self.producer,
            get_message_cache=lambda: self._message_cache,
            get_message_cache_max_bytes=lambda: self._message_cache_max_bytes,
            get_message_cache_size_bytes=lambda: self._message_cache_size_bytes,
            set_message_cache_size_bytes=self._set_message_cache_size_bytes,
            logger=logger,
        )
        self._idle_consume_timeout_seconds = 0.1
        self._dirty_commit_partitions: set[DtoTopicPartition] = set()
        self._unsettled_completions_by_partition: dict[DtoTopicPartition, int] = {}
        self._unsettled_completion_timestamps_by_partition: dict[
            DtoTopicPartition, dict[int, float]
        ] = {}
        self._completions_since_last_commit = 0
        self._commit_debounce_completion_threshold = (
            self._resolve_commit_debounce_completion_threshold(pc_conf)
        )
        self._commit_debounce_interval_seconds = (
            self._resolve_commit_debounce_interval_seconds(pc_conf)
        )
        self._last_commit_attempt_monotonic = time.monotonic()
        self._commit_cadence_support = BrokerCommitCadenceSupport(
            get_dirty_commit_partitions=lambda: self._dirty_commit_partitions,
            has_pending_dlq_events=lambda: bool(self._pending_dlq_events),
            get_total_in_flight_count=(
                lambda: self._work_manager.get_total_in_flight_count()
            ),
            get_total_queued_messages=lambda: self._get_total_queued_messages(),
        )
        self._pipeline_poll_records_total = 0
        self._pipeline_poll_nonempty_total = 0
        self._pipeline_poll_empty_total = 0
        self._pipeline_poll_error_total = 0
        commit_coordinator_config = getattr(pc_conf, "commit_coordinator", None)
        self._commit_coordinator_enabled = (
            getattr(commit_coordinator_config, "enabled", False) is True
        )
        self._commit_coordinator: CommitCoordinator | None = None
        self._commit_coordinator_metrics_sink = CommitCoordinatorMetricsSink(
            get_metrics_exporter=self._commit_coordinator_metrics_exporter,
            get_engine_type=self._pipeline_engine_type,
            get_pending_depth=(
                lambda: (
                    self._commit_coordinator.stats.queue_depth
                    if self._commit_coordinator is not None
                    else None
                )
            ),
        )
        self._commit_coordinator_support = BrokerCommitCoordinatorSupport(
            control_lock=self._control_lock,
            offset_trackers=self._offset_trackers,
            dirty_commit_partitions=self._dirty_commit_partitions,
            commit_planner=self._commit_planner,
            operation_guard=self._consumer_operation_guard,
            get_consumer=lambda: self.consumer,
            get_coordinator=lambda: self._commit_coordinator,
            rebalance_state_strategy=self._rebalance_state_strategy,
            clear_committed_dirty_partitions=self._clear_committed_dirty_partitions,
            record_commit_failure=self._record_commit_failure,
            record_pending_partitions=(
                self._commit_coordinator_metrics_sink.record_pending_depth
            ),
            kafka_exception_reason=self.COMMIT_FAILURE_REASON_KAFKA_EXCEPTION,
        )
        if self._commit_coordinator_enabled and commit_coordinator_config is not None:
            self._commit_coordinator = CommitCoordinator(
                config=commit_coordinator_config,
                commit_sync=self._commit_coordinator_support.commit_sync,
                on_commit_success=(
                    self._commit_coordinator_support.settle_committed_offsets
                ),
                on_commit_failure=(
                    self._commit_coordinator_support.retain_failed_commit_offsets
                ),
                record_metrics=self._commit_coordinator_metrics_sink.record_event,
            )

    # ------------------------------------------------------------------
    def set_metrics_exporter(self, metrics_exporter: Optional[Any]) -> None:
        """Install or update metrics exporter for Kafka polling and control-plane orchestration.

        Args:
            metrics_exporter: Optional metrics exporter receiving runtime observations.

        """
        self._metrics_exporter = metrics_exporter

    @staticmethod
    def _resolve_commit_debounce_completion_threshold(pc_conf: Any) -> int:
        """Resolve commit debounce completion threshold for Kafka polling and control-plane orchestration.

        Args:
            pc_conf: Parallel-consumer configuration object.

        Returns:
            Computed integer value.

        """
        return resolve_commit_debounce_completion_threshold(pc_conf)

    @staticmethod
    def _resolve_commit_debounce_interval_seconds(pc_conf: Any) -> float:
        """Resolve commit debounce interval seconds for Kafka polling and control-plane orchestration.

        Args:
            pc_conf: Parallel-consumer configuration object.

        Returns:
            Computed floating-point value.

        """
        return resolve_commit_debounce_interval_seconds(pc_conf)

    def _rebalance_state_strategy(self) -> str:
        """Handle rebalance state strategy within Kafka polling and control-plane orchestration.

        Returns:
            Computed string value.

        """
        return str(
            getattr(
                self._kafka_config.parallel_consumer,
                "rebalance_state_strategy",
                "contiguous_only",
            )
        )

    def _decode_assignment_completed_offsets(
        self,
        partition: KafkaTopicPartition,
        committed_partition: Optional[KafkaTopicPartition],
        last_committed: int,
    ) -> set[int]:
        """Decode assignment completed offsets for Kafka polling and control-plane orchestration.

        Args:
            partition: Kafka topic-partition object being inspected.
            committed_partition: Committed Kafka partition metadata, when available.
            last_committed: Last committed offset used as the decode baseline.

        Returns:
            set[int] result produced by this method.

        """
        return self._commit_planner.decode_assignment_completed_offsets(
            strategy=self._rebalance_state_strategy(),
            partition=partition,
            committed_partition=committed_partition,
            last_committed=last_committed,
        )

    def _encode_revoke_metadata(self, tracker: OffsetTracker, base_offset: int) -> str:
        """Encode revoke metadata for Kafka polling and control-plane orchestration.

        Args:
            tracker: Offset tracker whose state is being read or updated.
            base_offset: Base offset used for relative metadata encoding.

        Returns:
            Computed string value.

        """
        return self._commit_planner.encode_revoke_metadata(
            strategy=self._rebalance_state_strategy(),
            tracker=tracker,
            base_offset=base_offset,
        )

    # ------------------------------------------------------------------
    def _shutdown_policy(self) -> str:
        """Handle shutdown policy within Kafka polling and control-plane orchestration.

        Returns:
            Computed string value.

        """
        return resolve_shutdown_policy(self._kafka_config.parallel_consumer.execution)

    def _shutdown_drain_timeout_seconds(self) -> float:
        """Handle shutdown drain timeout seconds within Kafka polling and control-plane orchestration.

        Returns:
            Computed floating-point value.

        """
        return resolve_shutdown_drain_timeout_seconds(
            self._kafka_config.parallel_consumer.execution
        )

    @staticmethod
    def _coerce_adaptive_backpressure_config(
        raw_config: object,
    ) -> AdaptiveBackpressureConfig:
        """Handle coerce adaptive backpressure config within Kafka polling and control-plane orchestration.

        Args:
            raw_config: Raw adaptive configuration value to coerce.

        Returns:
            AdaptiveBackpressureConfig result produced by this method.

        """
        return coerce_adaptive_backpressure_config(raw_config)

    @staticmethod
    def _coerce_adaptive_concurrency_config(
        raw_parent: object,
        attribute_name: str,
    ) -> AdaptiveConcurrencyConfig:
        """Handle coerce adaptive concurrency config within Kafka polling and control-plane orchestration.

        Args:
            raw_parent: Parent configuration object containing an adaptive config attribute.
            attribute_name: Attribute name to read from the parent configuration.

        Returns:
            AdaptiveConcurrencyConfig result produced by this method.

        """
        return coerce_adaptive_concurrency_config(raw_parent, attribute_name)

    async def _get_consume_timeout_seconds(self) -> float:
        """Return consume timeout seconds for Kafka polling and control-plane orchestration.

        Returns:
            Computed floating-point value.

        """
        total_in_flight = self._work_manager.get_total_in_flight_count()
        total_queued = await self._get_total_queued_messages()
        if total_in_flight > 0 or total_queued > 0:
            return 0.0
        return self._idle_consume_timeout_seconds

    def _should_cache_message_payloads(self) -> bool:
        """Return whether cache message payloads should run in Kafka polling and control-plane orchestration.

        Returns:
            True when the condition is met; otherwise False.

        """
        return self._dlq_support.should_cache_message_payloads()

    @staticmethod
    def _estimate_cached_payload_bytes(payload: Any) -> int:
        """Handle estimate cached payload bytes within Kafka polling and control-plane orchestration.

        Args:
            payload: Serialized or decoded payload handled by this function.

        Returns:
            Computed integer value.

        """
        return BrokerDlqSupport.estimate_cached_payload_bytes(payload)

    def _set_message_cache_size_bytes(self, size_bytes: int) -> None:
        """Update raw DLQ cache size accounting."""
        self._message_cache_size_bytes = size_bytes

    def _set_running_state(self, value: bool) -> None:
        """Update the live broker running flag."""
        self._running = value

    def _set_shutdown_event(self, event: asyncio.Event) -> None:
        """Replace the lifecycle shutdown event."""
        self._shutdown_event = event

    def _set_consumer_task(self, task: Any | None) -> None:
        """Update the live consumer task handle."""
        self._consumer_task = task

    def _set_completion_monitor_task(self, task: Any | None) -> None:
        """Update the live completion monitor task handle."""
        self._completion_monitor_task = task

    def _set_event_loop(self, event_loop: Optional[asyncio.AbstractEventLoop]) -> None:
        """Update the callback bridge event loop."""
        self._event_loop = event_loop

    def _set_fatal_error(self, error: Optional[Exception]) -> None:
        """Update the stored fatal runtime error."""
        self._fatal_error = error

    def _set_runtime_clients(
        self,
        producer: Any | None,
        admin: Any | None,
        consumer: Any | None,
    ) -> None:
        """Install Kafka runtime clients created during start."""
        self.producer = producer
        self.admin = admin
        self.consumer = consumer

    def _set_defer_consumer_cleanup_for_stop(self, value: bool) -> None:
        """Update whether consumer-loop cleanup is deferred to stop."""
        self._defer_consumer_cleanup_for_stop = value

    def _set_completions_since_last_commit(self, value: int) -> None:
        """Update completion count waiting for commit cadence."""
        self._completions_since_last_commit = value

    def _get_cached_message_size(self, key: Any, value: Any) -> int:
        """Return cached message size for Kafka polling and control-plane orchestration.

        Args:
            key: Kafka record key or virtual queue key.
            value: Kafka record value.

        Returns:
            Computed integer value.

        """
        return self._dlq_support.get_cached_message_size(key, value)

    def _pop_cached_message(
        self, cache_key: Tuple[DtoTopicPartition, int]
    ) -> Optional[Tuple[Any, Any]]:
        """Pop cached message from Kafka polling and control-plane orchestration.

        Args:
            cache_key: Cache key identifying a cached message.

        Returns:
            Optional[Tuple[Any, Any]] result produced by this method.

        """
        return self._dlq_support.pop_cached_message(cache_key)

    def _cache_message_for_dlq(
        self, tp: DtoTopicPartition, offset: int, key: Any, value: Any
    ) -> None:
        """Handle cache message for dlq within Kafka polling and control-plane orchestration.

        Args:
            tp: Topic-partition affected by the operation.
            offset: Kafka record offset.
            key: Kafka record key or virtual queue key.
            value: Kafka record value.

        """
        self._dlq_support.cache_message_for_dlq(tp, offset, key, value)

    def _drop_cached_partition_messages(self, tp: DtoTopicPartition) -> None:
        """Drop cached partition messages from Kafka polling and control-plane orchestration.

        Args:
            tp: Topic-partition affected by the operation.

        """
        self._dlq_support.drop_cached_partition_messages(tp)

    # ------------------------------------------------------------------
    async def _publish_to_dlq(
        self,
        tp: DtoTopicPartition,
        offset: int,
        epoch: int,
        key: Any,
        value: Any,
        error: str,
        attempt: int,
    ) -> bool:
        """Convert publish to dlq.

        Args:
            tp: Topic-partition affected by the operation.
            offset: Kafka record offset.
            epoch: Partition ownership epoch associated with the work item.
            key: Kafka record key or virtual queue key.
            value: Kafka record value.
            error: Error reason to attach to the completion or DLQ record.
            attempt: Current retry attempt number.

        Returns:
            True when the condition is met; otherwise False.

        Raises:
            RuntimeError: If the broker runtime has failed.

        """
        return await self._dlq_support.publish_to_dlq(
            tp=tp,
            offset=offset,
            epoch=epoch,
            key=key,
            value=value,
            error=error,
            attempt=attempt,
        )

    # ------------------------------------------------------------------
    async def _run_consumer(self) -> None:
        """Run consumer for Kafka polling and control-plane orchestration.

        Raises:
            RuntimeError: If the broker runtime has failed.

        """
        logger.debug("Starting consumer loop")
        self._event_loop = asyncio.get_running_loop()
        if self.consumer is None:
            raise RuntimeError("Kafka consumer must be initialized")

        try:
            while self._running:
                if self._pending_dlq_events:
                    had_pending_dlq_events = True
                    async with self._control_lock:
                        drained_completion = await self._drain_completion_events_once()
                    if drained_completion:
                        await self._maybe_commit_ready_offsets(
                            had_pending_dlq_events=had_pending_dlq_events
                        )
                    await self._check_backpressure()
                    cadence_messages = await self._consume_messages(
                        num_messages=1,
                        timeout=0,
                    )
                    if cadence_messages:
                        async with self._control_lock:
                            await self._make_dispatch_support().dispatch_messages(
                                cadence_messages
                            )
                            await self._work_manager.schedule()
                    await asyncio.sleep(self._idle_consume_timeout_seconds)
                    continue

                await self._check_backpressure()

                consume_timeout = await self._get_consume_timeout_seconds()
                messages = await self._consume_messages(
                    num_messages=self._batch_size,
                    timeout=consume_timeout,
                )

                async with self._control_lock:
                    if messages:
                        await self._make_dispatch_support().dispatch_messages(messages)
                        await self._work_manager.schedule()

                    await self._drain_completion_events_once()

                await self._maybe_commit_ready_offsets(source="consumer_loop")

                if not messages and consume_timeout > 0:
                    await asyncio.sleep(consume_timeout)

        except Exception as exc:
            self._fatal_error = exc
            logger.error("Consumer loop error: %s", exc, exc_info=True)
        finally:
            self._running = False
            if self._completion_monitor_task is not None:
                self._completion_monitor_task.cancel()
                await asyncio.gather(
                    self._completion_monitor_task, return_exceptions=True
                )
                self._completion_monitor_task = None
            if not self._defer_consumer_cleanup_for_stop:
                await self._cleanup()
            self._shutdown_event.set()
            self._consumer_task = None

    async def _consume_messages(
        self,
        *,
        num_messages: int,
        timeout: float,
    ) -> List[Message]:
        """Poll broker records and record poll/acquire diagnostics."""
        if self.consumer is None:
            raise RuntimeError("Kafka consumer must be initialized")
        consumer = self.consumer

        try:
            messages: List[
                Message
            ] = await self._consumer_operation_guard.run_off_event_loop(
                lambda: consumer.consume(
                    num_messages=num_messages,
                    timeout=timeout,
                )
            )
        except Exception:
            self._record_pipeline_poll_error()
            raise

        self._record_pipeline_poll_batch(messages)
        return messages

    async def _drain_completion_events_once(self) -> bool:
        """Drain completion events once for Kafka polling and control-plane orchestration.

        Returns:
            True when the condition is met; otherwise False.

        """
        await self._drain_execution_control_events_once()
        completed_events = await self._work_manager.poll_completed_events()
        timeout_events = await self._handle_blocking_timeouts()
        if timeout_events:
            completed_events.extend(timeout_events)
        if not completed_events and not self._pending_dlq_events:
            return False

        await self._process_completed_events(completed_events)
        await self._work_manager.schedule()
        return True

    async def _drain_execution_control_events_once(self) -> bool:
        """Drain fatal execution-control events before item completions."""
        control_events = await self._execution_engine.poll_control_events()
        if not control_events:
            return False
        error = control_events[0].error
        self._fatal_error = error
        self._running = False
        raise error

    async def _run_completion_monitor(self) -> None:
        """Run completion monitor for Kafka polling and control-plane orchestration.

        Raises:
            asyncio.CancelledError: Propagated when the monitor task is cancelled.
            Exception: Propagates unexpected monitor failures after storing them.

        """
        await self._make_completion_monitor_support().run()

    async def _maybe_commit_ready_offsets(
        self, *, had_pending_dlq_events: bool = False, source: str = "unknown"
    ) -> None:
        """Handle maybe commit ready offsets within Kafka polling and control-plane orchestration.

        Args:
            had_pending_dlq_events: Whether pending DLQ events existed before the commit attempt.
            source: Diagnostic source label for the commit attempt.

        """
        force = await self._should_force_idle_commit()
        if had_pending_dlq_events or force or self._should_attempt_ready_commit():
            await self._commit_ready_offsets(
                force=force or had_pending_dlq_events,
                source=source,
            )

    async def _commit_ready_offsets(
        self, *, force: bool = False, source: str = "unknown"
    ) -> None:
        """Commit ready offsets for Kafka polling and control-plane orchestration.

        Args:
            force: Whether to force the operation regardless of cadence checks.
            source: Diagnostic source label for the commit attempt.

        """
        self._commit_cadence_support.record_invocation(source)
        if not force and not self._should_attempt_ready_commit():
            self._commit_cadence_support.record_empty_candidate_scan(source)
            return

        async with self._commit_lock:
            if not force and not self._should_attempt_ready_commit():
                self._commit_cadence_support.record_empty_candidate_scan(source)
                return

            async with self._control_lock:
                commits_to_make = (
                    self._make_dispatch_support().build_commit_candidates()
                )
                if not force:
                    commits_to_make = [
                        (tp, offset)
                        for tp, offset in commits_to_make
                        if tp in self._dirty_commit_partitions
                    ]
            if commits_to_make:
                if (
                    self._commit_coordinator_enabled
                    and self._commit_coordinator is not None
                ):
                    candidates = await self._build_commit_coordinator_candidates(
                        commits_to_make
                    )
                    if candidates:
                        enqueued = await self._commit_coordinator.enqueue(
                            candidates,
                            force=force,
                            source=source,
                        )
                        if enqueued:
                            self._record_commit_coordinator_pending_partitions()
                            self._completions_since_last_commit = 0
                            self._last_commit_attempt_monotonic = time.monotonic()
                            return
                    else:
                        self._completions_since_last_commit = 0
                        self._last_commit_attempt_monotonic = time.monotonic()
                        return
                    logger.warning(
                        "Commit coordinator rejected %d candidate(s); falling back to synchronous commit",
                        len(candidates),
                    )
                    self._commit_coordinator.cancel_leases(
                        [candidate.tp for candidate in candidates]
                    )
                    self._record_commit_coordinator_pending_partitions()
                committed = await self._commit_offsets(commits_to_make)
                if committed is not False:
                    self._commit_cadence_support.record_commit_success(
                        source,
                        len(commits_to_make),
                    )
                    self._clear_committed_dirty_partitions(commits_to_make)
            self._completions_since_last_commit = 0
            self._last_commit_attempt_monotonic = time.monotonic()

    async def _build_commit_coordinator_candidates(
        self, commits_to_make: list[tuple[DtoTopicPartition, int]]
    ) -> list[CommitCandidate]:
        """Build enriched commit candidates for the async coordinator."""
        return await self._commit_coordinator_support.build_candidates(commits_to_make)

    def get_commit_cadence_stats(self) -> Dict[str, Any]:
        """Return commit cadence stats for Kafka polling and control-plane orchestration.

        Returns:
            Commit cadence statistics.

        """
        return self._commit_cadence_support.get_stats()

    def _record_pipeline_poll_batch(self, messages: list[Any]) -> None:
        """Record broker poll/acquire totals for pipeline diagnostics."""
        message_count = len(messages)
        self._pipeline_poll_records_total += message_count
        if message_count > 0:
            self._pipeline_poll_nonempty_total += 1
        else:
            self._pipeline_poll_empty_total += 1

    def _record_pipeline_poll_error(self) -> None:
        """Record a broker poll error before records enter WorkManager."""
        self._pipeline_poll_error_total += 1

    def _should_attempt_ready_commit(self) -> bool:
        """Return whether attempt ready commit should run in Kafka polling and control-plane orchestration.

        Returns:
            True when the condition is met; otherwise False.

        """
        return self._commit_cadence_support.should_attempt_ready_commit(
            completions_since_last_commit=self._completions_since_last_commit,
            completion_threshold=self._commit_debounce_completion_threshold,
            interval_seconds=self._commit_debounce_interval_seconds,
            last_attempt_monotonic=self._last_commit_attempt_monotonic,
        )

    async def _should_force_idle_commit(self) -> bool:
        """Return whether force idle commit should run in Kafka polling and control-plane orchestration.

        Returns:
            True when the condition is met; otherwise False.

        """
        return await self._commit_cadence_support.should_force_idle_commit()

    def _clear_committed_dirty_partitions(
        self, commits_to_make: list[tuple[DtoTopicPartition, int]]
    ) -> None:
        """Clear committed dirty partitions for Kafka polling and control-plane orchestration.

        Args:
            commits_to_make: Commit candidates keyed by topic-partition.

        """
        self._make_commit_settlement_support().clear_committed_dirty_partitions(
            commits_to_make
        )

    def _observe_completion_to_commit_latency(
        self,
        tp: DtoTopicPartition,
        tracker: Optional[OffsetTracker],
        safe_offset: int,
    ) -> None:
        """Observe completion-to-commit latency for offsets just settled."""
        self._make_commit_settlement_support().observe_completion_to_commit_latency(
            tp,
            tracker,
            safe_offset,
        )

    def _pipeline_engine_type(self) -> str:
        """Return bounded pipeline engine type for internal metrics."""
        mode = getattr(self._kafka_config.parallel_consumer.execution, "mode", "async")
        value = getattr(mode, "value", mode)
        if value in {"async", "process"}:
            return str(value)
        return "async"

    def _make_completion_support(self) -> BrokerCompletionSupport:
        """Create completion support for Kafka polling and control-plane orchestration.

        Returns:
            Completion support helper bound to this poller.

        """

        async def _publish_to_dlq_proxy(**kwargs: Any) -> bool:
            """Forward DLQ publishing through the poller instance.

            Args:
                **kwargs: Keyword arguments accepted by the poller's DLQ publisher.

            Returns:
                True when the DLQ publish operation succeeds; otherwise False.

            """
            return await self._publish_to_dlq(**kwargs)

        return BrokerCompletionSupport(
            kafka_config=self._kafka_config,
            work_manager=self._work_manager,
            offset_trackers=self._offset_trackers,
            message_cache=self._message_cache,
            should_cache_message_payloads=self._should_cache_message_payloads,
            pop_cached_message=self._pop_cached_message,
            publish_to_dlq=_publish_to_dlq_proxy,
            logger=logger,
            pending_dlq_events=self._pending_dlq_events,
            metrics_exporter=self._metrics_exporter,
        )

    async def _handle_blocking_timeouts(self) -> list[CompletionEvent]:
        """Handle blocking timeouts for Kafka polling and control-plane orchestration.

        Returns:
            list[CompletionEvent] result produced by this method.

        """
        return await self._make_completion_support().handle_blocking_timeouts(
            max_blocking_duration_ms=self._max_blocking_duration_ms
        )

    async def _process_completed_events(
        self, completed_events: list[CompletionEvent]
    ) -> None:
        """Handle process completed events within Kafka polling and control-plane orchestration.

        Args:
            completed_events: Completed events value used by this method.

        """
        managed_partitions = set(self._offset_trackers)
        pending_retry_partitions = {
            tp for tp, _ in self._pending_dlq_events.keys() if tp in managed_partitions
        }
        processing_result = (
            await self._make_completion_support().process_completed_events(
                completed_events
            )
        )
        processed_count = processing_result.processed_count

        if processed_count > 0:
            self._make_commit_settlement_support().record_processed_completions(
                processing_result,
                pending_retry_partitions=pending_retry_partitions,
            )

        self._diag_events_since_log += processed_count
        if self._diag_events_since_log >= self._diag_log_every:
            self._log_partition_diagnostics()
            self._diag_events_since_log = 0

    # ------------------------------------------------------------------
    async def _commit_coordinator_sync(self, candidates: list[CommitCandidate]) -> None:
        """Submit coordinator candidates to Kafka through the support adapter."""
        await self._commit_coordinator_support.commit_sync(candidates)

    async def _settle_committed_offsets(
        self, settlements: list[CommitSettlement]
    ) -> None:
        """Apply successful coordinator settlements to offset trackers."""
        await self._commit_coordinator_support.settle_committed_offsets(settlements)

    async def _retain_failed_commit_offsets(
        self, settlements: list[CommitSettlement], reason: str
    ) -> None:
        """Retain retry intent for failed coordinator settlements."""
        await self._commit_coordinator_support.retain_failed_commit_offsets(
            settlements, reason
        )

    def _record_commit_coordinator_pending_partitions(self) -> None:
        """Record pending coordinator partition depth for metrics."""
        self._commit_coordinator_metrics_sink.record_pending_depth()

    def _record_commit_coordinator_metric(
        self,
        event: str,
        reason: str | None,
        count: int,
        latency: float | None,
    ) -> None:
        """Dispatch a coordinator metric event to the metrics sink."""
        self._commit_coordinator_metrics_sink.record_event(
            event, reason, count, latency
        )

    def _commit_coordinator_metrics_exporter(self) -> Any | None:
        """Return the direct or WorkManager-backed metrics exporter."""
        if self._metrics_exporter is not None:
            return self._metrics_exporter
        return getattr(self._work_manager, "_metrics_exporter", None)

    async def _commit_offsets(
        self, commits_to_make: List[tuple[DtoTopicPartition, int]]
    ) -> bool:
        """Build offset list and commit to Kafka with retry on transient failure.

        On success, advances each tracker's high water mark.
        On failure after retry, logs a warning and continues without crashing.

        Args:
            commits_to_make: Commit candidates keyed by topic-partition.

        Returns:
            True when the condition is met; otherwise False.

        """
        if self.consumer is None:
            return False

        async with self._control_lock:
            tracked_commits: list[tuple[DtoTopicPartition, int]] = []
            tracker_snapshot: dict[DtoTopicPartition, OffsetTracker] = {}
            for tp, safe_offset in commits_to_make:
                tracker = self._offset_trackers.get(tp)
                if tracker is None:
                    logger.debug(
                        "Skipping commit candidate for untracked partition %s", tp
                    )
                    continue
                tracked_commits.append((tp, safe_offset))
                tracker_snapshot[tp] = tracker

        if not tracked_commits:
            return True

        offsets_to_commit = self._commit_planner.build_offsets_to_commit(
            commits_to_make=tracked_commits,
            trackers=tracker_snapshot,
            strategy=self._rebalance_state_strategy(),
        )

        max_attempts = 2  # 1 initial + 1 retry
        for attempt in range(max_attempts):
            try:
                consumer = self.consumer
                if consumer is None:
                    return False
                await self._consumer_operation_guard.run_off_event_loop(
                    lambda: consumer.commit(
                        offsets=offsets_to_commit,
                        asynchronous=False,
                    )
                )
                async with self._control_lock:
                    for tp, safe_offset in tracked_commits:
                        tracker = self._offset_trackers.get(tp)
                        if tracker is None:
                            continue
                        tracker.commit_through(safe_offset)
                return True
            except KafkaException as exc:
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Commit failed (attempt %d/%d), retrying: %s",
                        attempt + 1,
                        max_attempts,
                        exc,
                    )
                else:
                    logger.error(
                        "Commit failed after %d attempts, skipping: %s",
                        max_attempts,
                        exc,
                    )
                    self._record_commit_failure(
                        tracked_commits,
                        self.COMMIT_FAILURE_REASON_KAFKA_EXCEPTION,
                    )
                    return False
        return False

    def _record_commit_failure(
        self,
        tracked_commits: list[tuple[DtoTopicPartition, int]],
        reason: str,
    ) -> None:
        """Record commit failure for Kafka polling and control-plane orchestration.

        Args:
            tracked_commits: Commit candidates being tracked for failure accounting.
            reason: Reason string recorded for diagnostics.

        """
        metrics_exporter = self._metrics_exporter
        if metrics_exporter is None:
            metrics_exporter = getattr(self._work_manager, "_metrics_exporter", None)
        recorder = getattr(metrics_exporter, "record_commit_failure", None)
        if not callable(recorder):
            return

        for tp, _ in tracked_commits:
            try:
                recorder(tp, reason)
            except Exception as exc:
                logger.warning(
                    "Commit failure metric recording failed for %s: %s",
                    tp,
                    exc,
                )

    def _record_commit_failure_for_partition(
        self, tp: DtoTopicPartition, reason: str
    ) -> None:
        """Record commit failure for partition for Kafka polling and control-plane orchestration.

        Args:
            tp: Topic-partition affected by the operation.
            reason: Reason string recorded for diagnostics.

        """
        self._record_commit_failure([(tp, 0)], reason)

    def _get_commit_metadata_offsets(
        self, tracker: OffsetTracker, base_offset: int
    ) -> set[int]:
        """Return commit metadata offsets for Kafka polling and control-plane orchestration.

        Args:
            tracker: Offset tracker whose state is being read or updated.
            base_offset: Base offset used for relative metadata encoding.

        Returns:
            set[int] result produced by this method.

        """
        return self._commit_planner.get_commit_metadata_offsets(tracker, base_offset)

    # ------------------------------------------------------------------
    def _get_partition_index(self, msg: Message) -> int:
        """Return partition index for Kafka polling and control-plane orchestration.

        Args:
            msg: Kafka message associated with the callback or lookup.

        Returns:
            Computed integer value.

        """
        return hash(cast(bytes, msg.key() or b"")) % self._worker_pool_size

    async def _get_total_queued_messages(self) -> int:
        """Return total queued messages for Kafka polling and control-plane orchestration.

        Returns:
            Computed integer value.

        """
        get_total_queued_messages = getattr(
            self._work_manager, "get_total_queued_messages", None
        )
        if callable(get_total_queued_messages):
            total = get_total_queued_messages()
            if isinstance(total, int):
                return total

        total = 0
        for queue_map in self._work_manager.get_virtual_queue_sizes().values():
            for size in queue_map.values():
                total += size
        return total

    def _get_min_inflight_offset(self, tp: DtoTopicPartition) -> Optional[int]:
        """Return min inflight offset for Kafka polling and control-plane orchestration.

        Args:
            tp: Topic-partition affected by the operation.

        Returns:
            Computed integer value, or None when no value is available.

        """
        min_inflight = self._work_manager.get_min_in_flight_offset(tp)
        return min_inflight if isinstance(min_inflight, int) else None

    def _log_partition_diagnostics(self) -> None:
        """Handle log partition diagnostics within Kafka polling and control-plane orchestration."""
        self._make_runtime_support().log_partition_diagnostics()

    def _get_total_true_lag(self) -> int:
        """Return total true lag for Kafka polling and control-plane orchestration.

        Returns:
            Computed integer value.

        """
        total_true_lag = 0
        for tracker in self._offset_trackers.values():
            last_fetched_offset = int(getattr(tracker, "last_fetched_offset", -1))
            last_committed_offset = int(getattr(tracker, "last_committed_offset", -1))
            total_true_lag += max(0, last_fetched_offset - last_committed_offset)
        return total_true_lag

    def _set_runtime_max_in_flight(
        self,
        value: int,
        *,
        log_change: bool = True,
    ) -> None:
        """Install or update runtime max in flight for Kafka polling and control-plane orchestration.

        Args:
            value: Kafka record value.
            log_change: Whether to log an observed runtime limit change.

        """
        self._make_backpressure_support().set_runtime_max_in_flight(
            value,
            log_change=log_change,
        )

    def _maybe_adjust_adaptive_backpressure(self, total_queued: int) -> None:
        """Handle maybe adjust adaptive backpressure within Kafka polling and control-plane orchestration.

        Args:
            total_queued: Total number of queued messages.

        """
        self._make_backpressure_support().maybe_adjust_adaptive_backpressure(
            total_queued
        )

    def _maybe_adjust_adaptive_concurrency(self, total_queued: int) -> None:
        """Handle maybe adjust adaptive concurrency within Kafka polling and control-plane orchestration.

        Args:
            total_queued: Total number of queued messages.

        """
        self._make_backpressure_support().maybe_adjust_adaptive_concurrency(
            total_queued
        )

    async def _check_backpressure(self) -> None:
        """Handle check backpressure within Kafka polling and control-plane orchestration.

        Raises:
            RuntimeError: If the broker runtime has failed.

        """
        await self._make_backpressure_support().check_backpressure()

    # ------------------------------------------------------------------
    def _delivery_report(self, err: Optional[KafkaException], msg: Message) -> None:
        """Handle delivery report within Kafka polling and control-plane orchestration.

        Args:
            err: Kafka delivery error, if any.
            msg: Kafka message associated with the callback or lookup.

        """
        if err is not None:
            logger.error("Delivery failed: %s", err)

    async def _cleanup(self) -> None:
        """Handle cleanup within Kafka polling and control-plane orchestration."""
        await self._make_lifecycle_support().cleanup_runtime()

    def _raise_if_failed(self) -> None:
        """Handle raise if failed within Kafka polling and control-plane orchestration.

        Raises:
            error: If this exception is raised by the operation.

        """
        if self._fatal_error is None:
            return

        error = self._fatal_error
        self._fatal_error = None
        raise error

    async def _submit_grouped_messages(
        self,
        grouped_messages: Dict[
            tuple[DtoTopicPartition, Any], list[tuple[int, int, Any, Any]]
        ],
    ) -> None:
        """Submit grouped messages for Kafka polling and control-plane orchestration.

        Args:
            grouped_messages: Messages grouped by topic-partition and ordering key.

        """
        if not grouped_messages:
            return

        submit_message_batch = getattr(self._work_manager, "submit_message_batch", None)
        if inspect.iscoroutinefunction(submit_message_batch):
            await submit_message_batch(grouped_messages)
            return

        for (tp, key), messages in grouped_messages.items():
            for offset, epoch, payload, _poison_key in messages:
                await self._work_manager.submit_message(
                    tp=tp,
                    offset=offset,
                    epoch=epoch,
                    key=key,
                    payload=payload,
                )

    def _make_dispatch_support(self) -> BrokerDispatchSupport:
        """Create dispatch support for Kafka polling and control-plane orchestration.

        Returns:
            Dispatch support helper bound to this poller.

        """
        return BrokerDispatchSupport(
            ordering_mode=self.ORDERING_MODE,
            offset_trackers=self._offset_trackers,
            cache_message_for_dlq=self._cache_message_for_dlq,
            submit_message=self._work_manager.submit_message,
            submit_grouped_messages=self._submit_grouped_messages,
            get_min_inflight_offset=self._get_min_inflight_offset,
            record_completed_offset_skip=self._record_completed_offset_skip,
            logger=logger,
        )

    def _record_completed_offset_skip(
        self, _tp: DtoTopicPartition, _offset: int
    ) -> None:
        """Record a restored completed-offset dispatch skip."""
        self._pipeline_completed_offset_skips_total += 1

    # ------------------------------------------------------------------
    def _on_assign(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        """Handle on assign within Kafka polling and control-plane orchestration.

        Args:
            consumer: Kafka consumer instance.
            partitions: Kafka topic partitions passed by the rebalance callback.

        """
        self._make_rebalance_orchestration_support().handle_assign_callback(
            consumer=consumer,
            partitions=partitions,
            assign_from_callback=self._assign_from_callback,
        )

    def _assign_from_callback(
        self, consumer: Consumer, partitions: list[KafkaTopicPartition]
    ) -> bool:
        """Build assignment state off-loop and install it on the event loop."""
        return self._make_rebalance_orchestration_support().assign_from_callback(
            consumer=consumer,
            partitions=partitions,
        )

    def _assign_sync(
        self, work_manager_assignments: dict[DtoTopicPartition, OffsetTracker]
    ) -> None:
        """Install assignment trackers and notify WorkManager."""
        self._make_rebalance_orchestration_support().assign_sync(
            work_manager_assignments
        )

    def _on_revoke(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        """Handle on revoke within Kafka polling and control-plane orchestration.

        Args:
            consumer: Kafka consumer instance.
            partitions: Kafka topic partitions passed by the rebalance callback.

        """
        self._make_rebalance_orchestration_support().handle_revoke_callback(
            consumer=consumer,
            partitions=partitions,
            prepare_revoke_from_callback=self._prepare_revoke_from_callback,
            cleanup_revoke_from_callback=self._cleanup_revoke_from_callback,
        )

    def _prepare_revoke_from_callback(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation | None:
        """Bridge revoke preparation onto the event loop."""
        return (
            self._make_rebalance_orchestration_support().prepare_revoke_from_callback(
                partitions
            )
        )

    def _prepare_revoke_sync(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation:
        """Prepare revoke payloads and state transitions under control lock."""
        return self._make_rebalance_orchestration_support().prepare_revoke_sync(
            partitions
        )

    def _commit_prepared_revoke_offsets(
        self,
        consumer: Consumer,
        offsets_to_commit: list[KafkaTopicPartition],
    ) -> list[DtoTopicPartition]:
        """Commit prepared revoke offsets under the broker operation guard."""
        return (
            self._make_rebalance_orchestration_support().commit_prepared_revoke_offsets(
                consumer=consumer,
                offsets_to_commit=offsets_to_commit,
            )
        )

    def _cleanup_revoke_from_callback(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> bool:
        """Bridge revoke cleanup onto the event loop."""
        return (
            self._make_rebalance_orchestration_support().cleanup_revoke_from_callback(
                revoked_tps, failed_tps
            )
        )

    def _cleanup_revoke_sync(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> None:
        """Remove revoked partition state after broker revoke commit finishes."""
        self._make_rebalance_orchestration_support().cleanup_revoke_sync(
            revoked_tps,
            failed_tps,
        )

    def _rebalance_bridge_timeout_seconds(self) -> float:
        """Return bounded rebalance callback bridge timeout in seconds."""
        timeout_ms = getattr(
            self._kafka_config.parallel_consumer.execution,
            "max_revoke_grace_ms",
            0,
        )
        return max(0.0, float(timeout_ms) / 1000.0)

    def _assign_bridge_timeout_seconds(self) -> float:
        """Return assign bridge timeout covering committed-offset lookup budget."""
        committed_lookup_timeout = (
            self._rebalance_support.committed_lookup_timeout_seconds
        )
        return max(self._rebalance_bridge_timeout_seconds(), committed_lookup_timeout)

    def _record_commit_failure_for_rebalance_bridge(
        self, partitions: list[KafkaTopicPartition]
    ) -> None:
        """Record replay-safe failures when rebalance bridge phases fail."""
        self._make_rebalance_orchestration_support().record_commit_failure_for_rebalance_bridge(
            partitions
        )

    # ------------------------------------------------------------------
    async def start(self) -> None:
        """Handle start within Kafka polling and control-plane orchestration.

        Raises:
            Exception: Propagates startup failures after logging them.

        """
        await self._make_lifecycle_support().start()

    async def stop(self) -> None:
        """Handle stop within Kafka polling and control-plane orchestration."""
        await self._make_lifecycle_support().stop(cleanup=self._cleanup)

    async def _drain_shutdown_work(self, *, timeout_seconds: float) -> bool:
        """Drain shutdown work for Kafka polling and control-plane orchestration.

        Args:
            timeout_seconds: Maximum time to wait, in seconds; None waits indefinitely.

        Returns:
            True when the condition is met; otherwise False.

        """
        return await self._make_shutdown_support().drain(
            timeout_seconds=timeout_seconds
        )

    async def _drain_commit_coordinator_for_shutdown(self, deadline: float) -> bool:
        """Drain coordinator work or run sync fallback before shutdown close."""
        coordinator_timeout = getattr(
            self._kafka_config.parallel_consumer.commit_coordinator,
            "stop_drain_timeout_ms",
            0,
        )
        return (
            await self._commit_coordinator_support.drain_or_sync_fallback_for_shutdown(
                deadline=deadline,
                timeout_ms=int(coordinator_timeout),
                sync_fallback=self._commit_offsets,
            )
        )

    async def wait_closed(self) -> None:
        """Wait for closed in Kafka polling and control-plane orchestration."""
        await self._make_lifecycle_support().wait_closed()

    # ------------------------------------------------------------------
    def get_metrics(self) -> SystemMetrics:
        """Return metrics for Kafka polling and control-plane orchestration.

        Returns:
            Current system metrics snapshot.

        """
        metrics = self._make_runtime_support().build_system_metrics()
        runtime_metrics = self._execution_engine.get_runtime_metrics()
        return SystemMetrics(
            total_in_flight=metrics.total_in_flight,
            is_paused=metrics.is_paused,
            partitions=metrics.partitions,
            completed_offset_skips_total=self._pipeline_completed_offset_skips_total,
            adaptive_backpressure=metrics.adaptive_backpressure,
            adaptive_concurrency=metrics.adaptive_concurrency,
            process_batch_metrics=BrokerRuntimeSupport._project_process_batch_metrics(
                runtime_metrics
            ),
        )

    def get_runtime_snapshot(self) -> RuntimeSnapshot:
        """Return runtime snapshot for Kafka polling and control-plane orchestration.

        Returns:
            Current runtime snapshot.

        """
        return self._make_runtime_support().build_runtime_snapshot()

    def get_pipeline_diagnostics(self) -> WorkManagerPipelineDiagnostics:
        """Return the stable pipeline diagnostics sidecar snapshot."""
        diagnostics = self._work_manager.get_pipeline_diagnostics()
        runtime_metrics = self._execution_engine.get_runtime_metrics()
        return self._make_runtime_support().compose_pipeline_diagnostics(
            diagnostics,
            runtime_metrics,
        )

    def _make_runtime_support(self) -> BrokerRuntimeSupport:
        """Create runtime support for Kafka polling and control-plane orchestration.

        Returns:
            Runtime support helper bound to this poller.

        """
        adaptive_backpressure_snapshot = None
        if self._adaptive_backpressure_controller.enabled:
            get_latency = getattr(
                self._work_manager, "get_average_completion_latency_seconds", None
            )
            raw_completion_latency = get_latency() if callable(get_latency) else None
            avg_completion_latency = (
                float(raw_completion_latency)
                if isinstance(raw_completion_latency, (int, float))
                else None
            )
            adaptive_backpressure_snapshot = (
                self._adaptive_backpressure_controller.build_runtime_snapshot(
                    avg_completion_latency_seconds=avg_completion_latency
                )
            )
            # Export the true live runtime cap for the backpressure snapshot too.
            # Adaptive concurrency can change MAX_IN_FLIGHT_MESSAGES after the
            # backpressure evaluator runs, in either direction.
            if adaptive_backpressure_snapshot is not None:
                normalized_backpressure_cap = max(1, int(self.MAX_IN_FLIGHT_MESSAGES))
                if (
                    normalized_backpressure_cap
                    != adaptive_backpressure_snapshot.effective_max_in_flight
                ):
                    adaptive_backpressure_snapshot = replace(
                        adaptive_backpressure_snapshot,
                        effective_max_in_flight=normalized_backpressure_cap,
                    )
        adaptive_concurrency_snapshot = None
        if self._adaptive_concurrency_controller.enabled:
            adaptive_concurrency_snapshot = (
                self._adaptive_concurrency_controller.build_runtime_snapshot(
                    effective_max_in_flight=self.MAX_IN_FLIGHT_MESSAGES
                )
            )
        return BrokerRuntimeSupport(
            work_manager=self._work_manager,
            offset_trackers=self._offset_trackers,
            consumer=self.consumer,
            execution_engine=self._execution_engine,
            execution_config=self._kafka_config.parallel_consumer.execution,
            consume_topic=self._consume_topic,
            ordering_mode=self.ORDERING_MODE,
            dlq_enabled=bool(getattr(self._kafka_config, "dlq_enabled", False)),
            dlq_topic_suffix=str(getattr(self._kafka_config, "DLQ_TOPIC_SUFFIX", "")),
            dlq_payload_mode=getattr(
                self._kafka_config, "dlq_payload_mode", DLQPayloadMode.FULL
            ),
            message_cache_size_bytes=self._message_cache_size_bytes,
            message_cache_entry_count=len(self._message_cache),
            max_in_flight_messages=self.MAX_IN_FLIGHT_MESSAGES,
            min_in_flight_messages_to_resume=self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME,
            queue_max_messages=self.QUEUE_MAX_MESSAGES,
            queue_resume_threshold=self._queue_resume_threshold,
            is_paused=self._is_paused,
            blocking_warn_seconds=self._blocking_warn_seconds,
            logger=logger,
            configured_max_in_flight_messages=self._configured_max_in_flight_messages,
            adaptive_backpressure=adaptive_backpressure_snapshot,
            adaptive_concurrency=adaptive_concurrency_snapshot,
            poison_message_config=self._poison_message_config,
            poison_message_open_circuit_count=(
                self._work_manager.get_poison_message_open_circuit_count()
                if hasattr(self._work_manager, "get_poison_message_open_circuit_count")
                else 0
            ),
            dirty_commit_partitions=self._dirty_commit_partitions,
            unsettled_completions_by_partition=(
                self._unsettled_completions_by_partition
            ),
            pending_dlq_events=self._pending_dlq_events,
            pipeline_poll_records_total=self._pipeline_poll_records_total,
            pipeline_poll_nonempty_total=self._pipeline_poll_nonempty_total,
            pipeline_poll_empty_total=self._pipeline_poll_empty_total,
            pipeline_poll_error_total=self._pipeline_poll_error_total,
            pipeline_completed_offset_skips_total=(
                self._pipeline_completed_offset_skips_total
            ),
        )

    def _make_lifecycle_support(self) -> BrokerLifecycleSupport:
        """Create lifecycle support for start, stop, wait, and cleanup orchestration."""
        return BrokerLifecycleSupport(
            stop_lock=self._stop_lock,
            get_running=lambda: self._running,
            set_running=self._set_running_state,
            get_shutdown_event=lambda: self._shutdown_event,
            set_shutdown_event=self._set_shutdown_event,
            get_consumer_task=lambda: self._consumer_task,
            set_consumer_task=self._set_consumer_task,
            get_completion_monitor_task=lambda: self._completion_monitor_task,
            set_completion_monitor_task=self._set_completion_monitor_task,
            set_event_loop=self._set_event_loop,
            set_fatal_error=self._set_fatal_error,
            get_kafka_config=lambda: self._kafka_config,
            get_consume_topic=lambda: self._consume_topic,
            set_runtime_clients=self._set_runtime_clients,
            get_producer=lambda: self.producer,
            get_consumer=lambda: self.consumer,
            get_pending_dlq_events=lambda: self._pending_dlq_events,
            get_message_cache=lambda: self._message_cache,
            set_message_cache_size_bytes=self._set_message_cache_size_bytes,
            get_task_lifecycle_support=self._make_task_lifecycle_support,
            get_shutdown_policy=self._shutdown_policy,
            get_shutdown_drain_timeout_seconds=self._shutdown_drain_timeout_seconds,
            get_consumer_task_stop_timeout_seconds=(
                lambda: self._consumer_task_stop_timeout_seconds
            ),
            set_defer_consumer_cleanup_for_stop=(
                self._set_defer_consumer_cleanup_for_stop
            ),
            raise_if_failed=self._raise_if_failed,
            drain_shutdown_work=self._drain_shutdown_work,
            drain_commit_coordinator_for_shutdown=(
                self._drain_commit_coordinator_for_shutdown
            ),
            consumer_operation_guard=self._consumer_operation_guard,
            on_assign=self._on_assign,
            on_revoke=self._on_revoke,
            run_consumer=self._run_consumer,
            run_completion_monitor=self._run_completion_monitor,
            logger=logger,
        )

    def _make_completion_monitor_support(self) -> BrokerCompletionMonitorSupport:
        """Create completion monitor support for completion queue cadence."""
        return BrokerCompletionMonitorSupport(
            control_lock=self._control_lock,
            get_running=lambda: self._running,
            set_running=self._set_running_state,
            get_total_in_flight_count=self._work_manager.get_total_in_flight_count,
            has_pending_dlq_events=lambda: bool(self._pending_dlq_events),
            get_idle_consume_timeout_seconds=lambda: self._idle_consume_timeout_seconds,
            get_max_blocking_duration_ms=lambda: self._max_blocking_duration_ms,
            wait_for_completion=self._execution_engine.wait_for_completion,
            drain_completion_events_once=self._drain_completion_events_once,
            maybe_commit_ready_offsets=self._maybe_commit_ready_offsets,
            set_fatal_error=self._set_fatal_error,
            logger=logger,
            sleep=asyncio.sleep,
        )

    def _make_commit_settlement_support(self) -> BrokerCommitSettlementSupport:
        """Create commit settlement support for completed-offset bookkeeping."""
        return BrokerCommitSettlementSupport(
            offset_trackers=self._offset_trackers,
            dirty_commit_partitions=self._dirty_commit_partitions,
            unsettled_completions_by_partition=(
                self._unsettled_completions_by_partition
            ),
            unsettled_completion_timestamps_by_partition=(
                self._unsettled_completion_timestamps_by_partition
            ),
            get_completions_since_last_commit=(
                lambda: self._completions_since_last_commit
            ),
            set_completions_since_last_commit=(self._set_completions_since_last_commit),
            get_metrics_exporter=self._commit_coordinator_metrics_exporter,
            get_pipeline_engine_type=self._pipeline_engine_type,
            now=time.monotonic,
        )

    def _make_task_lifecycle_support(self) -> BrokerTaskLifecycleSupport:
        """Create task lifecycle support for Kafka polling and control-plane orchestration.

        Returns:
            Task lifecycle support helper bound to this poller.

        """

        def create_task_with_name(
            coro: Any, name: str | None = None
        ) -> asyncio.Task[Any]:
            """Create an asyncio task with an optional task name.

            Args:
                coro: Awaitable object to schedule as an asyncio task.
                name: Optional name assigned to the created task.

            Returns:
                Created asyncio task.

            """
            return asyncio.create_task(coro, name=name)

        return BrokerTaskLifecycleSupport(
            producer_factory=Producer,
            admin_factory=AdminClient,
            consumer_factory=Consumer,
            task_factory=create_task_with_name,
        )

    def _make_shutdown_support(self) -> BrokerShutdownSupport:
        """Create shutdown drain support for Kafka polling orchestration."""
        return BrokerShutdownSupport(
            control_lock=self._control_lock,
            schedule_work=self._work_manager.schedule,
            drain_completion_events_once=self._drain_completion_events_once,
            commit_ready_offsets=self._commit_ready_offsets,
            get_total_in_flight_count=self._work_manager.get_total_in_flight_count,
            get_total_queued_messages=self._get_total_queued_messages,
            get_pending_dlq_count=lambda: len(self._pending_dlq_events),
            drain_commit_coordinator=self._drain_commit_coordinator_for_shutdown,
            wait_for_completion=self._execution_engine.wait_for_completion,
            idle_consume_timeout_seconds=self._idle_consume_timeout_seconds,
            logger=logger,
            sleep=asyncio.sleep,
        )

    def _make_rebalance_orchestration_support(
        self,
    ) -> BrokerRebalanceOrchestrationSupport:
        """Create rebalance orchestration support for assign/revoke callbacks."""
        return BrokerRebalanceOrchestrationSupport(
            rebalance_support=self._rebalance_support,
            rebalance_bridge=self._rebalance_bridge,
            get_rebalance_state_strategy=self._rebalance_state_strategy,
            get_max_revoke_grace_ms=(
                lambda: (
                    self._kafka_config.parallel_consumer.execution.max_revoke_grace_ms
                )
            ),
            get_commit_coordinator=lambda: self._commit_coordinator,
            get_work_manager=lambda: self._work_manager,
            get_offset_trackers=lambda: self._offset_trackers,
            get_dirty_commit_partitions=lambda: self._dirty_commit_partitions,
            get_unsettled_completions_by_partition=(
                lambda: self._unsettled_completions_by_partition
            ),
            get_unsettled_completion_timestamps_by_partition=(
                lambda: self._unsettled_completion_timestamps_by_partition
            ),
            get_pending_dlq_events=lambda: self._pending_dlq_events,
            drop_cached_partition_messages=self._drop_cached_partition_messages,
            encode_revoke_metadata=self._encode_revoke_metadata,
            record_commit_failure_for_partition=(
                self._record_commit_failure_for_partition
            ),
            consumer_operation_guard=self._consumer_operation_guard,
            logger=logger,
        )

    def _make_backpressure_support(self) -> BrokerBackpressureSupport:
        """Create backpressure support for adaptive limit orchestration."""
        return BrokerBackpressureSupport(
            configured_max_in_flight=self._configured_max_in_flight_messages,
            adaptive_backpressure_controller=self._adaptive_backpressure_controller,
            adaptive_concurrency_controller=self._adaptive_concurrency_controller,
            work_manager=self._work_manager,
            get_consumer=lambda: self.consumer,
            get_total_queued_messages=self._get_total_queued_messages,
            get_total_true_lag=self._get_total_true_lag,
            get_current_limit=lambda: self.MAX_IN_FLIGHT_MESSAGES,
            set_current_limit=self._set_max_in_flight_message_limit,
            set_resume_limit=self._set_min_in_flight_resume_limit,
            get_queue_max_messages=lambda: self.QUEUE_MAX_MESSAGES,
            get_is_paused=lambda: self._is_paused,
            set_is_paused=self._set_paused_state,
            check_runtime_backpressure=(
                lambda total_queued: self._make_runtime_support().check_backpressure(
                    total_queued=total_queued
                )
            ),
            logger=logger,
        )

    def _set_max_in_flight_message_limit(self, value: int) -> None:
        """Update the live max-in-flight limit."""
        self.MAX_IN_FLIGHT_MESSAGES = value

    def _set_min_in_flight_resume_limit(self, value: int) -> None:
        """Update the live in-flight resume threshold."""
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = value

    def _set_paused_state(self, value: bool) -> None:
        """Update the live paused state."""
        self._is_paused = value
