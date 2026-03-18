# -*- coding: utf-8 -*-
"""BrokerPoller - polls Kafka and drives the WorkManager."""

import asyncio
import random
from itertools import islice
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from confluent_kafka import OFFSET_INVALID, Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

from ..config import KafkaConfig
from ..dto import (
    CompletionEvent,
    CompletionStatus,
    DLQPayloadMode,
    OrderingMode,
    PartitionMetrics,
    SystemMetrics,
)
from ..dto import TopicPartition as DtoTopicPartition
from ..logger import LogManager
from ..utils.validation import validate_topic_name
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

        self._message_cache: Dict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]] = {}
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
        if self._rebalance_state_strategy() != "metadata_snapshot":
            return set()

        metadata_source = (
            committed_partition if committed_partition is not None else partition
        )
        raw_metadata = getattr(metadata_source, "metadata", None)
        if not isinstance(raw_metadata, str) or not raw_metadata:
            return set()

        decoded = self._metadata_encoder.decode_metadata(raw_metadata)
        return {offset for offset in decoded if offset > last_committed}

    def _encode_revoke_metadata(self, tracker: OffsetTracker, base_offset: int) -> str:
        if self._rebalance_state_strategy() != "metadata_snapshot":
            return ""
        metadata_offsets = self._get_commit_metadata_offsets(tracker, base_offset)
        metadata = self._metadata_encoder.encode_metadata(metadata_offsets, base_offset)
        if isinstance(metadata, str):
            return metadata
        if isinstance(metadata, (bytes, bytearray)):
            return bytes(metadata).decode("utf-8", errors="ignore")
        return ""

    # ------------------------------------------------------------------
    async def _get_consume_timeout_seconds(self) -> float:
        total_in_flight = self._work_manager.get_total_in_flight_count()
        total_queued = await self._get_total_queued_messages()
        if total_in_flight > 0 or total_queued > 0:
            return 0.0
        return self._idle_consume_timeout_seconds

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
                        for msg in messages:
                            if msg.error():
                                logger.warning(
                                    "Consumed message with error: %s", msg.error()
                                )
                                continue

                            topic = msg.topic()
                            partition = msg.partition()
                            if topic is None or partition is None:
                                logger.warning(
                                    "Received message with None topic or partition"
                                )
                                continue

                            tp = DtoTopicPartition(topic=topic, partition=partition)
                            offset_val = msg.offset()
                            if offset_val is None:
                                continue
                            tracker = self._offset_trackers.get(tp)
                            if tracker is None:
                                logger.warning("Untracked partition %s - skipping", tp)
                                continue

                            cache_key = (tp, offset_val)
                            self._message_cache[cache_key] = (msg.key(), msg.value())

                            submit_key: Any
                            if self.ORDERING_MODE == OrderingMode.PARTITION:
                                submit_key = tp.partition
                            else:
                                submit_key = msg.key()

                            await self._work_manager.submit_message(
                                tp=tp,
                                offset=offset_val,
                                epoch=tracker.get_current_epoch(),
                                key=submit_key,
                                payload=msg.value(),
                            )

                        await self._work_manager.schedule()

                    await self._drain_completion_events_once()

                commits_to_make: List[tuple[DtoTopicPartition, int]] = []
                for tp, tracker in self._offset_trackers.items():
                    potential_hwm = tracker.last_committed_offset
                    for offset in tracker.completed_offsets:
                        if offset == potential_hwm + 1:
                            potential_hwm = offset
                        else:
                            break
                    min_inflight = self._get_min_inflight_offset(tp)
                    if min_inflight is not None and min_inflight <= potential_hwm:
                        potential_hwm = min_inflight - 1
                    if potential_hwm > tracker.last_committed_offset:
                        commits_to_make.append((tp, potential_hwm))

                if commits_to_make:
                    await self._commit_offsets(commits_to_make)

                if not messages and consume_timeout > 0:
                    await asyncio.sleep(consume_timeout)

        except Exception as exc:
            self._fatal_error = exc
            logger.error("Consumer loop error: %s", exc, exc_info=True)
        finally:
            self._running = False
            self._consumer_task = None
            if self._completion_monitor_task is not None:
                self._completion_monitor_task.cancel()
                await asyncio.gather(
                    self._completion_monitor_task, return_exceptions=True
                )
                self._completion_monitor_task = None
            await self._cleanup()
            self._shutdown_event.set()

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

    async def _handle_blocking_timeouts(self) -> list[CompletionEvent]:
        if self._max_blocking_duration_ms <= 0:
            return []

        threshold_sec = self._max_blocking_duration_ms / 1000.0
        forced = False
        execution_config = self._kafka_config.parallel_consumer.execution

        for tp, tracker in self._offset_trackers.items():
            durations = tracker.get_blocking_offset_durations()
            if not durations:
                continue

            for offset, duration in durations.items():
                if duration < threshold_sec:
                    continue

                error_text = "Blocking offset %d exceeded %.3fs" % (offset, duration)
                success = await self._work_manager.force_fail(
                    tp=tp,
                    offset=offset,
                    epoch=tracker.get_current_epoch(),
                    error=error_text,
                    attempt=execution_config.max_retries,
                )
                if success:
                    forced = True

        if not forced:
            return []

        return await self._work_manager.poll_completed_events()

    async def _process_completed_events(
        self, completed_events: list[CompletionEvent]
    ) -> None:
        for event in completed_events:
            tracker = self._offset_trackers.get(event.tp)
            if tracker is None:
                logger.warning("Completion for untracked partition %s", event.tp)
                continue
            if event.epoch != tracker.get_current_epoch():
                logger.warning(
                    "Discarding zombie completion for %s@%d (epoch %d vs %d)",
                    event.tp,
                    event.offset,
                    event.epoch,
                    tracker.get_current_epoch(),
                )
                continue

            dlq_success: bool = True

            if event.status == CompletionStatus.FAILURE:
                logger.error(
                    "Message processing failed for %s@%d: %s",
                    event.tp,
                    event.offset,
                    event.error,
                )

                max_retries = self._kafka_config.parallel_consumer.execution.max_retries
                if (
                    self._kafka_config.dlq_enabled and event.attempt >= max_retries  # type: ignore[attr-defined]
                ):
                    cache_key = (event.tp, event.offset)
                    cached_msg = self._message_cache.get(cache_key)
                    if cached_msg is None:
                        logger.warning(
                            "No cached message for %s@%d, skipping DLQ publish and commit",
                            event.tp,
                            event.offset,
                        )
                        continue

                    msg_key, msg_value = cached_msg
                    error_text = event.error or "Unknown error"
                    dlq_success = await self._publish_to_dlq(
                        tp=event.tp,
                        offset=event.offset,
                        epoch=event.epoch,
                        key=msg_key,
                        value=msg_value,
                        error=error_text,
                        attempt=event.attempt,  # type: ignore[attr-defined]
                    )

                if not dlq_success:
                    logger.error(
                        "DLQ publish failed for %s@%d, skipping commit and retaining cache for retry",
                        event.tp,
                        event.offset,
                    )
                    continue

            tracker.mark_complete(event.offset)
            cache_key_to_remove = (event.tp, event.offset)
            self._message_cache.pop(cache_key_to_remove, None)

        self._diag_events_since_log += len(completed_events)
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
        offsets_to_commit = []
        for tp, safe_offset in commits_to_make:
            tracker = self._offset_trackers[tp]
            base_offset = safe_offset + 1
            metadata_offsets = self._get_commit_metadata_offsets(tracker, base_offset)
            metadata = self._metadata_encoder.encode_metadata(
                metadata_offsets, base_offset
            )
            if isinstance(metadata, (bytes, bytearray)):
                metadata_text = bytes(metadata).decode("utf-8", errors="ignore")
            elif isinstance(metadata, str):
                metadata_text = metadata
            else:
                metadata_text = ""

            kafka_tp = cast(
                KafkaTopicPartition,
                cast(Any, KafkaTopicPartition)(
                    tp.topic,
                    tp.partition,
                    safe_offset + 1,
                    metadata=metadata_text,
                ),
            )
            offsets_to_commit.append(kafka_tp)

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
        if hasattr(tracker.completed_offsets, "irange"):
            return set(
                islice(
                    tracker.completed_offsets.irange(minimum=base_offset),
                    self.MAX_COMPLETED_OFFSETS_FOR_METADATA,
                )
            )

        return {offset for offset in tracker.completed_offsets if offset >= base_offset}

    # ------------------------------------------------------------------
    def _get_partition_index(self, msg: Message) -> int:
        return hash(cast(bytes, msg.key() or b"")) % self._worker_pool_size

    async def _get_total_queued_messages(self) -> int:
        total = 0
        for queue_map in self._work_manager.get_virtual_queue_sizes().values():
            for size in queue_map.values():
                total += size
        return total

    def _get_min_inflight_offset(self, tp: DtoTopicPartition) -> Optional[int]:
        min_inflight = self._execution_engine.get_min_inflight_offset(tp)
        return min_inflight if isinstance(min_inflight, int) else None

    def _log_partition_diagnostics(self) -> None:
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        gaps = self._work_manager.get_gaps()
        blocking = self._work_manager.get_blocking_offsets()
        parts: list[str] = []
        for tp, tracker in self._offset_trackers.items():
            safe_topic = validate_topic_name(tp.topic)
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
                    safe_topic,
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
                logger.warning(
                    "Prolonged blocking offset %s@%d age=%.2fs gaps=%d queued=%d",
                    tp,
                    blocking_offset.start,
                    blocking_age,
                    gap_count,
                    queued,
                )
        logger.debug("Partition diag: %s", "; ".join(parts))

    async def _check_backpressure(self) -> None:
        if self.consumer is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks")

        total_in_flight = self._work_manager.get_total_in_flight_count()
        total_queued = await self._get_total_queued_messages()
        current_load = total_in_flight + total_queued

        partitions = self.consumer.assignment()
        if not partitions:
            return

        queue_full = (
            self.QUEUE_MAX_MESSAGES > 0 and total_queued >= self.QUEUE_MAX_MESSAGES
        )

        if not self._is_paused and (
            current_load > self.MAX_IN_FLIGHT_MESSAGES or queue_full
        ):
            logger.warning(
                "Backpressure: pausing consumer (load=%d limit=%d)",
                current_load,
                self.MAX_IN_FLIGHT_MESSAGES,
            )
            self.consumer.pause(partitions)
            self._is_paused = True
        elif self._is_paused and (
            current_load < self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME
            and (
                self._queue_resume_threshold == 0
                or total_queued <= self._queue_resume_threshold
            )
        ):
            logger.debug(
                "Backpressure released: resuming consumer (load=%d resume=%d)",
                current_load,
                self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME,
            )
            self.consumer.resume(partitions)
            self._is_paused = False

    # ------------------------------------------------------------------
    def _delivery_report(self, err: Optional[KafkaException], msg: Message) -> None:
        if err is not None:
            logger.error("Delivery failed: %s", err)

    async def _cleanup(self) -> None:
        if self.producer:
            await asyncio.to_thread(self.producer.flush, timeout=5)
        if self.consumer:
            self.consumer.close()

    def _raise_if_failed(self) -> None:
        if self._fatal_error is None:
            return

        error = self._fatal_error
        self._fatal_error = None
        raise RuntimeError(str(error)) from error

    # ------------------------------------------------------------------
    def _on_assign(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        logger.debug(
            "Partitions assigned: %s",
            ", ".join(f"{tp.topic}-{tp.partition}@{tp.offset}" for tp in partitions),
        )

        committed_offsets: Dict[Tuple[str, int], KafkaTopicPartition] = {}
        try:
            committed_partitions = consumer.committed(partitions)
            for committed_tp in committed_partitions:
                if committed_tp.offset is None:
                    continue
                committed_offsets[
                    (committed_tp.topic, committed_tp.partition)
                ] = committed_tp
        except KafkaException as exc:
            logger.warning(
                "Failed to fetch committed offsets on assignment, falling back to assignment offsets: %s",
                exc,
            )

        work_manager_assignments: Dict[DtoTopicPartition, OffsetTracker] = {}

        for partition in partitions:
            tp_dto = DtoTopicPartition(partition.topic, partition.partition)
            committed_partition = committed_offsets.get(
                (partition.topic, partition.partition)
            )
            committed_offset = (
                committed_partition.offset if committed_partition is not None else None
            )
            tracker_starting_offset = (
                committed_offset
                if committed_offset is not None and committed_offset > OFFSET_INVALID
                else partition.offset
            )
            last_committed = (
                committed_offset - 1
                if committed_offset is not None and committed_offset >= 0
                else (
                    partition.offset - 1
                    if partition.offset and partition.offset > 0
                    else -1
                )
            )
            initial_completed_offsets = self._decode_assignment_completed_offsets(
                partition,
                committed_partition,
                last_committed,
            )
            tracker = OffsetTracker(
                topic_partition=tp_dto,
                starting_offset=tracker_starting_offset,
                max_revoke_grace_ms=self._kafka_config.parallel_consumer.execution.max_revoke_grace_ms,
                initial_completed_offsets=initial_completed_offsets,
            )
            tracker.last_committed_offset = last_committed
            tracker.last_fetched_offset = max(
                [last_committed, *initial_completed_offsets]
                if initial_completed_offsets
                else [last_committed]
            )
            tracker.increment_epoch()
            self._offset_trackers[tp_dto] = tracker
            work_manager_assignments[tp_dto] = tracker

        self._work_manager.on_assign(work_manager_assignments)

    def _on_revoke(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        logger.warning(
            "Partitions revoked: %s",
            ", ".join(f"{tp.topic}-{tp.partition}" for tp in partitions),
        )

        tp_dtos = [
            DtoTopicPartition(topic=tp.topic, partition=tp.partition)
            for tp in partitions
        ]
        self._work_manager.on_revoke(tp_dtos)

        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(tp_kafka.topic, tp_kafka.partition)
            tracker = self._offset_trackers.get(tp_dto)
            if tracker is None:
                continue

            tracker.advance_high_water_mark()
            safe_offset = tracker.last_committed_offset
            if safe_offset >= 0:
                metadata = self._encode_revoke_metadata(tracker, safe_offset + 1)
                tp_to_commit = KafkaTopicPartition(
                    tp_dto.topic,
                    tp_dto.partition,
                    safe_offset + 1,
                    metadata=metadata,
                )
                try:
                    consumer.commit(offsets=[tp_to_commit], asynchronous=False)
                except KafkaException as exc:
                    logger.warning(
                        "Revoke commit failed for %s-%d at offset %d: %s",
                        tp_dto.topic,
                        tp_dto.partition,
                        safe_offset + 1,
                        exc,
                    )

            del self._offset_trackers[tp_dto]

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
            self.producer = Producer(producer_conf)  # type: ignore[arg-type]

            admin_conf = cast(
                dict[str, str | int | float | bool],
                {"bootstrap.servers": self._kafka_config.BOOTSTRAP_SERVERS[0]},
            )
            self.admin = AdminClient(admin_conf)  # type: ignore[arg-type]

            consumer_conf = cast(
                dict[str, str | int | float | bool | None],
                self._kafka_config.get_consumer_config(),
            )
            self.consumer = Consumer(consumer_conf)  # type: ignore[arg-type]
            self.consumer.subscribe(
                [self._consume_topic],
                on_assign=self._on_assign,
                on_revoke=self._on_revoke,
            )
            self._running = True
            if getattr(
                self._kafka_config.parallel_consumer,
                "strict_completion_monitor_enabled",
                True,
            ):
                self._completion_monitor_task = asyncio.create_task(
                    self._run_completion_monitor()
                )
            self._consumer_task = asyncio.create_task(
                self._run_consumer(),
                name="broker-poller-loop",
            )
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
            try:
                await asyncio.wait_for(
                    self._consumer_task,
                    timeout=self._consumer_task_stop_timeout_seconds,
                )
            except asyncio.TimeoutError:
                self._consumer_task.cancel()
                await asyncio.gather(self._consumer_task, return_exceptions=True)
            finally:
                self._consumer_task = None
        await self._shutdown_event.wait()
        self._raise_if_failed()
        logger.debug("BrokerPoller stopped")

    # ------------------------------------------------------------------
    def get_metrics(self) -> SystemMetrics:
        partition_metrics_list: List[PartitionMetrics] = []
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
                durations.get(blocking_offset) if blocking_offset else None
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
        )
