# -*- coding: utf-8 -*-
"""BrokerPoller - polls Kafka and drives the WorkManager."""

import asyncio
import inspect
import random
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from confluent_kafka import Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

from ..config import KafkaConfig
from ..dto import (
    CompletionEvent,
    DLQPayloadMode,
    OrderingMode,
    ProcessBatchMetrics,
    SystemMetrics,
)
from ..dto import TopicPartition as DtoTopicPartition
from ..logger import LogManager
from ..utils.validation import validate_topic_name
from .broker_completion_support import BrokerCompletionSupport
from .broker_dispatch_support import BrokerDispatchSupport
from .broker_rebalance_support import BrokerRebalanceSupport
from .broker_runtime_support import BrokerRuntimeSupport
from .broker_support import BrokerCommitPlanner, DlqCacheSupport
from .broker_task_lifecycle_support import BrokerTaskLifecycleSupport
from .metadata_encoder import MetadataEncoder
from .offset_tracker import OffsetTracker
from .work_manager import WorkManager

logger = LogManager.get_logger(__name__)


class BrokerPoller:
    """Polls Kafka, feeds WorkManager, coordinates commits."""

    MAX_COMPLETED_OFFSETS_FOR_METADATA = 2048

    def __init__(
        self,
        consume_topic: str,
        kafka_config: KafkaConfig,
        execution_engine: BaseExecutionEngine,
        work_manager: Optional[WorkManager] = None,
    ) -> None:
        self._consume_topic = consume_topic
        self._kafka_config = kafka_config
        self._execution_engine = execution_engine

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
        config_ordering_mode = getattr(pc_conf, "ordering_mode", OrderingMode.KEY_HASH)
        if isinstance(config_ordering_mode, str):
            config_ordering_mode = OrderingMode(config_ordering_mode)
        if not isinstance(config_ordering_mode, OrderingMode):
            config_ordering_mode = OrderingMode.KEY_HASH
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
        self._control_lock = asyncio.Lock()
        self._completion_monitor_task: Optional[asyncio.Task[None]] = None
        self._consumer_task: Optional[asyncio.Task[None]] = None
        self._consumer_task_stop_timeout_seconds = 5.0
        self._fatal_error: Optional[Exception] = None

        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._metadata_encoder = MetadataEncoder()
        self._rebalance_support = BrokerRebalanceSupport(
            metadata_encoder=self._metadata_encoder,
            tracker_factory=OffsetTracker,
        )
        self._commit_planner = BrokerCommitPlanner(
            metadata_encoder=self._metadata_encoder,
            max_completed_offsets=self.MAX_COMPLETED_OFFSETS_FOR_METADATA,
        )
        self._dlq_cache_support = DlqCacheSupport()
        self._work_manager = work_manager or WorkManager(
            execution_engine=self._execution_engine,
            ordering_mode=self.ORDERING_MODE,
            blocking_cache_ttl=getattr(pc_conf, "blocking_cache_ttl", 0),
            max_revoke_grace_ms=pc_conf.execution.max_revoke_grace_ms,
        )

        self._diag_log_every = int(getattr(pc_conf, "diag_log_every", 1000) or 1000)
        self._diag_events_since_log = 0
        self._blocking_warn_seconds = float(
            getattr(pc_conf, "blocking_warn_seconds", 5.0) or 5.0
        )
        self._max_blocking_duration_ms = int(
            getattr(pc_conf, "max_blocking_duration_ms", 0) or 0
        )

        self.MAX_IN_FLIGHT_MESSAGES = pc_conf.execution.max_in_flight
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = max(
            1, int(self.MAX_IN_FLIGHT_MESSAGES * 0.7)
        )
        self._is_paused = False

        self._message_cache: (
            "OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]]"
        ) = OrderedDict()
        self._message_cache_size_bytes = 0
        self._idle_consume_timeout_seconds = 0.1

    # ------------------------------------------------------------------
    def _rebalance_state_strategy(self) -> str:
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
        return self._commit_planner.decode_assignment_completed_offsets(
            strategy=self._rebalance_state_strategy(),
            partition=partition,
            committed_partition=committed_partition,
            last_committed=last_committed,
        )

    def _encode_revoke_metadata(self, tracker: OffsetTracker, base_offset: int) -> str:
        return self._commit_planner.encode_revoke_metadata(
            strategy=self._rebalance_state_strategy(),
            tracker=tracker,
            base_offset=base_offset,
        )

    # ------------------------------------------------------------------
    async def _get_consume_timeout_seconds(self) -> float:
        total_in_flight = self._work_manager.get_total_in_flight_count()
        total_queued = await self._get_total_queued_messages()
        if total_in_flight > 0 or total_queued > 0:
            return 0.0
        return self._idle_consume_timeout_seconds

    def _should_cache_message_payloads(self) -> bool:
        dlq_enabled = bool(getattr(self._kafka_config, "dlq_enabled", False))
        payload_mode = getattr(
            self._kafka_config, "dlq_payload_mode", DLQPayloadMode.FULL
        )
        return bool(
            dlq_enabled
            and payload_mode == DLQPayloadMode.FULL
            and self._message_cache_max_bytes != 0
        )

    @staticmethod
    def _estimate_cached_payload_bytes(payload: Any) -> int:
        if payload is None:
            return 0
        if isinstance(payload, memoryview):
            return len(payload)
        if isinstance(payload, (bytes, bytearray)):
            return len(payload)
        if isinstance(payload, str):
            return len(payload.encode("utf-8"))
        return 0

    def _get_cached_message_size(self, key: Any, value: Any) -> int:
        return self._dlq_cache_support.get_cached_message_size(key, value)

    def _pop_cached_message(
        self, cache_key: Tuple[DtoTopicPartition, int]
    ) -> Optional[Tuple[Any, Any]]:
        (
            cached_message,
            self._message_cache_size_bytes,
        ) = self._dlq_cache_support.pop_cached_message(
            self._message_cache,
            self._message_cache_size_bytes,
            cache_key,
        )
        return cached_message

    def _cache_message_for_dlq(
        self, tp: DtoTopicPartition, offset: int, key: Any, value: Any
    ) -> None:
        self._message_cache_size_bytes = self._dlq_cache_support.cache_message_for_dlq(
            message_cache=self._message_cache,
            size_bytes=self._message_cache_size_bytes,
            should_cache=self._should_cache_message_payloads(),
            max_bytes=self._message_cache_max_bytes,
            tp=tp,
            offset=offset,
            key=key,
            value=value,
            logger=logger,
        )

    def _drop_cached_partition_messages(self, tp: DtoTopicPartition) -> None:
        self._message_cache_size_bytes = (
            self._dlq_cache_support.drop_partition_messages(
                message_cache=self._message_cache,
                size_bytes=self._message_cache_size_bytes,
                tp=tp,
            )
        )

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
        if self.producer is None:
            raise RuntimeError("Producer must be initialized for DLQ publishing")

        source_topic = validate_topic_name(self._consume_topic)
        suffix = validate_topic_name(self._kafka_config.DLQ_TOPIC_SUFFIX)
        dlq_topic = source_topic + suffix
        headers_raw = [
            ("x-error-reason", error.encode("utf-8")),
            ("x-retry-attempt", str(attempt).encode("utf-8")),
            ("source-topic", tp.topic.encode("utf-8")),
            ("partition", str(tp.partition).encode("utf-8")),
            ("offset", str(offset).encode("utf-8")),
            ("epoch", str(epoch).encode("utf-8")),
        ]
        headers: List[Tuple[str, Union[str, bytes, None]]] = cast(
            List[Tuple[str, Union[str, bytes, None]]], headers_raw
        )

        exec_config = self._kafka_config.parallel_consumer.execution
        max_retries = exec_config.max_retries
        base_backoff_ms = exec_config.retry_backoff_ms
        max_backoff_ms = exec_config.max_retry_backoff_ms
        jitter_ms = exec_config.retry_jitter_ms
        use_exponential = exec_config.exponential_backoff

        payload_mode = getattr(
            self._kafka_config, "dlq_payload_mode", DLQPayloadMode.FULL
        )

        for retry_attempt in range(max_retries):
            try:
                send_key = None
                send_value = None
                if payload_mode == DLQPayloadMode.FULL:
                    send_key = key
                    send_value = value
                await asyncio.to_thread(
                    self.producer.produce,
                    topic=dlq_topic,
                    key=send_key,
                    value=send_value,
                    headers=headers,  # type: ignore[arg-type]
                )
                await asyncio.to_thread(
                    self.producer.flush,
                    timeout=self._kafka_config.DLQ_FLUSH_TIMEOUT_MS / 1000.0,
                )
                logger.debug(
                    "Published to DLQ: %s@%d -> %s",
                    tp,
                    offset,
                    dlq_topic,
                )
                return True
            except Exception as exc:
                if retry_attempt < max_retries - 1:
                    if use_exponential:
                        backoff = min(
                            base_backoff_ms * (2**retry_attempt), max_backoff_ms
                        )
                    else:
                        backoff = base_backoff_ms

                    jitter = random.uniform(0, jitter_ms)
                    sleep_time_ms = backoff + jitter
                    logger.warning(
                        "DLQ publish failed (attempt %d/%d), retrying in %d ms: %s",
                        retry_attempt + 1,
                        max_retries,
                        int(sleep_time_ms),
                        exc,
                    )
                    await asyncio.sleep(sleep_time_ms / 1000.0)
                else:
                    logger.error(
                        "DLQ publish failed after %d attempts for %s@%d: %s",
                        max_retries,
                        tp,
                        offset,
                        exc,
                        exc_info=True,
                    )
                    return False
        return False

    # ------------------------------------------------------------------
    async def _run_consumer(self) -> None:
        logger.debug("Starting consumer loop")
        if self.consumer is None:
            raise RuntimeError("Kafka consumer must be initialized")

        try:
            while self._running:
                await self._check_backpressure()

                consume_timeout = await self._get_consume_timeout_seconds()
                messages: List[Message] = await asyncio.to_thread(
                    self.consumer.consume,
                    num_messages=self._batch_size,
                    timeout=consume_timeout,
                )

                async with self._control_lock:
                    if messages:
                        await self._make_dispatch_support().dispatch_messages(messages)
                        await self._work_manager.schedule()

                    await self._drain_completion_events_once()

                commits_to_make = (
                    self._make_dispatch_support().build_commit_candidates()
                )
                if commits_to_make:
                    await self._commit_offsets(commits_to_make)

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
            await self._cleanup()
            self._shutdown_event.set()
            self._consumer_task = None

    async def _drain_completion_events_once(self) -> bool:
        completed_events = await self._work_manager.poll_completed_events()
        timeout_events = await self._handle_blocking_timeouts()
        if timeout_events:
            completed_events.extend(timeout_events)
        if not completed_events:
            return False

        await self._process_completed_events(completed_events)
        await self._work_manager.schedule()
        return True

    async def _run_completion_monitor(self) -> None:
        timeout_seconds = self._idle_consume_timeout_seconds
        if self._max_blocking_duration_ms > 0:
            timeout_seconds = min(
                timeout_seconds,
                self._max_blocking_duration_ms / 1000.0,
            )

        try:
            while self._running:
                if self._work_manager.get_total_in_flight_count() <= 0:
                    await asyncio.sleep(timeout_seconds)
                    continue

                has_completion = await self._execution_engine.wait_for_completion(
                    timeout_seconds=timeout_seconds,
                )
                if not has_completion and self._max_blocking_duration_ms <= 0:
                    continue

                async with self._control_lock:
                    await self._drain_completion_events_once()
        except asyncio.CancelledError:
            raise

    def _make_completion_support(self) -> BrokerCompletionSupport:
        async def _publish_to_dlq_proxy(**kwargs: Any) -> bool:
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
        )

    async def _handle_blocking_timeouts(self) -> list[CompletionEvent]:
        return await self._make_completion_support().handle_blocking_timeouts(
            max_blocking_duration_ms=self._max_blocking_duration_ms
        )

    async def _process_completed_events(
        self, completed_events: list[CompletionEvent]
    ) -> None:
        processed_count = (
            await self._make_completion_support().process_completed_events(
                completed_events
            )
        )

        self._diag_events_since_log += processed_count
        if self._diag_events_since_log >= self._diag_log_every:
            self._log_partition_diagnostics()
            self._diag_events_since_log = 0

    # ------------------------------------------------------------------
    async def _commit_offsets(
        self, commits_to_make: List[tuple[DtoTopicPartition, int]]
    ) -> None:
        """Build offset list and commit to Kafka with retry on transient failure.

        On success, advances each tracker's high water mark.
        On failure after retry, logs a warning and continues without crashing.
        """
        if self.consumer is None:
            return
        offsets_to_commit = self._commit_planner.build_offsets_to_commit(
            commits_to_make=commits_to_make,
            trackers=self._offset_trackers,
            strategy=self._rebalance_state_strategy(),
        )

        max_attempts = 2  # 1 initial + 1 retry
        for attempt in range(max_attempts):
            try:
                await asyncio.to_thread(
                    self.consumer.commit,
                    offsets=offsets_to_commit,
                    asynchronous=False,
                )
                for tp, _ in commits_to_make:
                    self._offset_trackers[tp].advance_high_water_mark()
                return
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

    def _get_commit_metadata_offsets(
        self, tracker: OffsetTracker, base_offset: int
    ) -> set[int]:
        return self._commit_planner.get_commit_metadata_offsets(tracker, base_offset)

    # ------------------------------------------------------------------
    def _get_partition_index(self, msg: Message) -> int:
        return hash(cast(bytes, msg.key() or b"")) % self._worker_pool_size

    async def _get_total_queued_messages(self) -> int:
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
        min_inflight = self._execution_engine.get_min_inflight_offset(tp)
        return min_inflight if isinstance(min_inflight, int) else None

    def _log_partition_diagnostics(self) -> None:
        self._make_runtime_support().log_partition_diagnostics()

    async def _check_backpressure(self) -> None:
        if self.consumer is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks")

        total_queued = await self._get_total_queued_messages()
        self._is_paused = self._make_runtime_support().check_backpressure(
            total_queued=total_queued
        )

    # ------------------------------------------------------------------
    def _delivery_report(self, err: Optional[KafkaException], msg: Message) -> None:
        if err is not None:
            logger.error("Delivery failed: %s", err)

    async def _cleanup(self) -> None:
        if self.producer:
            await asyncio.to_thread(self.producer.flush, timeout=5)
        if self.consumer:
            self.consumer.close()
        self._message_cache.clear()
        self._message_cache_size_bytes = 0

    def _raise_if_failed(self) -> None:
        if self._fatal_error is None:
            return

        error = self._fatal_error
        self._fatal_error = None
        raise error

    async def _submit_grouped_messages(
        self,
        grouped_messages: Dict[
            tuple[DtoTopicPartition, Any], list[tuple[int, int, Any]]
        ],
    ) -> None:
        if not grouped_messages:
            return

        submit_message_batch = getattr(self._work_manager, "submit_message_batch", None)
        if inspect.iscoroutinefunction(submit_message_batch):
            await submit_message_batch(grouped_messages)
            return

        for (tp, key), messages in grouped_messages.items():
            for offset, epoch, payload in messages:
                await self._work_manager.submit_message(
                    tp=tp,
                    offset=offset,
                    epoch=epoch,
                    key=key,
                    payload=payload,
                )

    def _make_dispatch_support(self) -> BrokerDispatchSupport:
        return BrokerDispatchSupport(
            ordering_mode=self.ORDERING_MODE,
            offset_trackers=self._offset_trackers,
            cache_message_for_dlq=self._cache_message_for_dlq,
            submit_message=self._work_manager.submit_message,
            submit_grouped_messages=self._submit_grouped_messages,
            get_min_inflight_offset=self._get_min_inflight_offset,
            logger=logger,
        )

    # ------------------------------------------------------------------
    def _on_assign(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        logger.debug(
            "Partitions assigned: %s",
            ", ".join(f"{tp.topic}-{tp.partition}@{tp.offset}" for tp in partitions),
        )

        work_manager_assignments = self._rebalance_support.build_assignments(
            consumer=consumer,
            partitions=partitions,
            strategy=self._rebalance_state_strategy(),
            max_revoke_grace_ms=self._kafka_config.parallel_consumer.execution.max_revoke_grace_ms,
            logger=logger,
        )
        self._offset_trackers.update(work_manager_assignments)
        self._work_manager.on_assign(work_manager_assignments)

    def _on_revoke(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        logger.warning(
            "Partitions revoked: %s",
            ", ".join(f"{tp.topic}-{tp.partition}" for tp in partitions),
        )

        self._rebalance_support.handle_revoke(
            consumer=consumer,
            partitions=partitions,
            work_manager=self._work_manager,
            offset_trackers=self._offset_trackers,
            drop_cached_partition_messages=self._drop_cached_partition_messages,
            strategy=self._rebalance_state_strategy(),
            logger=logger,
        )

    # ------------------------------------------------------------------
    async def start(self) -> None:
        try:
            if self._running:
                return
            self._shutdown_event = asyncio.Event()
            self._fatal_error = None
            producer_conf = cast(
                dict[str, str | int | float | bool],
                self._kafka_config.get_producer_config(),
            )
            admin_conf = cast(
                dict[str, str | int | float | bool],
                {"bootstrap.servers": self._kafka_config.BOOTSTRAP_SERVERS[0]},
            )
            consumer_conf = cast(
                dict[str, str | int | float | bool | None],
                self._kafka_config.get_consumer_config(),
            )
            (
                self.producer,
                self.admin,
                self.consumer,
                self._consumer_task,
                self._completion_monitor_task,
            ) = self._make_task_lifecycle_support().start_runtime(
                consume_topic=self._consume_topic,
                producer_conf=producer_conf,
                admin_conf=admin_conf,
                consumer_conf=consumer_conf,
                on_assign=self._on_assign,
                on_revoke=self._on_revoke,
                consumer_loop_coro_factory=self._run_consumer,
                completion_monitor_coro_factory=self._run_completion_monitor,
                strict_completion_monitor_enabled=getattr(
                    self._kafka_config.parallel_consumer,
                    "strict_completion_monitor_enabled",
                    True,
                ),
            )
            self._running = True
            logger.debug("Kafka consumer subscribed to %s", self._consume_topic)
        except Exception as exc:
            logger.error("Failed to start BrokerPoller: %s", exc, exc_info=True)
            raise

    async def stop(self) -> None:
        if not self._running and self._consumer_task is None:
            if self._shutdown_event.is_set():
                self._raise_if_failed()
            return
        logger.debug("Shutdown signal received")
        self._running = False
        if self._consumer_task is not None:
            consumer_task = self._consumer_task
            await self._make_task_lifecycle_support().stop_runtime(
                consumer_task=consumer_task,
                shutdown_event=self._shutdown_event,
                timeout_seconds=self._consumer_task_stop_timeout_seconds,
                wait_for=asyncio.wait_for,
                gather=asyncio.gather,
            )
            self._consumer_task = None
        self._raise_if_failed()
        logger.debug("BrokerPoller stopped")

    async def wait_closed(self) -> None:
        if not self._running and self._consumer_task is None:
            if self._shutdown_event.is_set():
                self._raise_if_failed()
            return
        await self._make_task_lifecycle_support().wait_closed(
            shutdown_event=self._shutdown_event,
            raise_if_failed=self._raise_if_failed,
        )

    # ------------------------------------------------------------------
    def get_metrics(self) -> SystemMetrics:
        metrics = self._make_runtime_support().build_system_metrics()
        runtime_metrics = self._execution_engine.get_runtime_metrics()
        return SystemMetrics(
            total_in_flight=metrics.total_in_flight,
            is_paused=metrics.is_paused,
            partitions=metrics.partitions,
            process_batch_metrics=(
                runtime_metrics
                if isinstance(runtime_metrics, ProcessBatchMetrics)
                else None
            ),
        )

    def _make_runtime_support(self) -> BrokerRuntimeSupport:
        return BrokerRuntimeSupport(
            work_manager=self._work_manager,
            offset_trackers=self._offset_trackers,
            consumer=self.consumer,
            max_in_flight_messages=self.MAX_IN_FLIGHT_MESSAGES,
            min_in_flight_messages_to_resume=self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME,
            queue_max_messages=self.QUEUE_MAX_MESSAGES,
            queue_resume_threshold=self._queue_resume_threshold,
            is_paused=self._is_paused,
            blocking_warn_seconds=self._blocking_warn_seconds,
            logger=logger,
        )

    def _make_task_lifecycle_support(self) -> BrokerTaskLifecycleSupport:
        def create_task_with_name(
            coro: Any, name: str | None = None
        ) -> asyncio.Task[Any]:
            return asyncio.create_task(coro, name=name)

        return BrokerTaskLifecycleSupport(
            producer_factory=Producer,
            admin_factory=AdminClient,
            consumer_factory=Consumer,
            task_factory=create_task_with_name,
        )
