# -*- coding: utf-8 -*-
"""BrokerPoller - polls Kafka and drives the WorkManager."""

import asyncio
import random
from typing import Any, Dict, List, Optional, Tuple, Union, cast

from confluent_kafka import Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

from ..config import KafkaConfig
from ..dto import CompletionStatus, OrderingMode, PartitionMetrics, SystemMetrics
from ..dto import TopicPartition as DtoTopicPartition
from ..logger import LogManager
from .metadata_encoder import MetadataEncoder
from .offset_tracker import OffsetTracker
from .work_manager import WorkManager

logger = LogManager.get_logger(__name__)


class BrokerPoller:
    """Polls Kafka, feeds WorkManager, coordinates commits."""

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

        self._batch_size = self._kafka_config.parallel_consumer.poll_batch_size
        self._worker_pool_size = self._kafka_config.parallel_consumer.worker_pool_size
        self.ORDERING_MODE = OrderingMode.KEY_HASH

        self.producer: Optional[Producer] = None
        self.consumer: Optional[Consumer] = None
        self.admin: Optional[AdminClient] = None

        self._running = False
        self._shutdown_event = asyncio.Event()

        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._metadata_encoder = MetadataEncoder()
        self._work_manager = work_manager or WorkManager(
            execution_engine=self._execution_engine,
            ordering_mode=self.ORDERING_MODE,
            blocking_cache_ttl=self._kafka_config.parallel_consumer.blocking_cache_ttl,
        )

        self._diag_log_every = self._kafka_config.parallel_consumer.diag_log_every
        self._diag_events_since_log = 0
        self._blocking_warn_seconds = (
            self._kafka_config.parallel_consumer.blocking_warn_seconds
        )

        self.MAX_IN_FLIGHT_MESSAGES = (
            self._kafka_config.parallel_consumer.execution.max_in_flight
        )
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = self.MAX_IN_FLIGHT_MESSAGES // 2
        self._is_paused = False

        self._message_cache: Dict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]] = {}

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

        dlq_topic = self._consume_topic + self._kafka_config.DLQ_TOPIC_SUFFIX
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

        for retry_attempt in range(max_retries):
            try:
                await asyncio.to_thread(
                    self.producer.produce,
                    topic=dlq_topic,
                    key=key,
                    value=value,
                    headers=headers,  # type: ignore[arg-type]
                )
                await asyncio.to_thread(
                    self.producer.flush,
                    timeout=self._kafka_config.DLQ_FLUSH_TIMEOUT_MS / 1000.0,
                )
                logger.info(
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
        logger.info("Starting consumer loop")
        if self.consumer is None:
            raise RuntimeError("Kafka consumer must be initialized")

        try:
            while self._running:
                await self._check_backpressure()

                messages: List[Message] = await asyncio.to_thread(
                    self.consumer.consume,
                    num_messages=self._batch_size,
                    timeout=0.1,
                )

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

                        await self._work_manager.submit_message(
                            tp=tp,
                            offset=offset_val,
                            epoch=tracker.get_current_epoch(),
                            key=msg.key(),
                            payload=msg.value(),
                        )
                        tracker.update_last_fetched_offset(offset_val)

                    await self._work_manager.schedule()

                completed_events = await self._work_manager.poll_completed_events()
                if completed_events:
                    for event in completed_events:
                        tracker = self._offset_trackers.get(event.tp)
                        if tracker is None:
                            logger.warning(
                                "Completion for untracked partition %s", event.tp
                            )
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

                        if event.status == CompletionStatus.FAILURE:
                            logger.error(
                                "Message processing failed for %s@%d: %s",
                                event.tp,
                                event.offset,
                                event.error,
                            )

                            max_retries = self._kafka_config.parallel_consumer.execution.max_retries
                            if (
                                self._kafka_config.dlq_enabled
                                and event.attempt >= max_retries  # type: ignore[attr-defined]
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
                                else:
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

                commits_to_make: List[tuple[DtoTopicPartition, int]] = []
                for tp, tracker in self._offset_trackers.items():
                    potential_hwm = tracker.last_committed_offset
                    for offset in tracker.completed_offsets:
                        if offset == potential_hwm + 1:
                            potential_hwm = offset
                        else:
                            break
                    if potential_hwm > tracker.last_committed_offset:
                        commits_to_make.append((tp, potential_hwm))

                if commits_to_make:
                    offsets_to_commit = []
                    for tp, safe_offset in commits_to_make:
                        tracker = self._offset_trackers[tp]
                        kafka_tp = KafkaTopicPartition(
                            tp.topic,
                            tp.partition,
                            safe_offset + 1,
                        )
                        offsets_to_commit.append(kafka_tp)
                    await asyncio.to_thread(
                        self.consumer.commit,
                        offsets=offsets_to_commit,
                        asynchronous=False,
                    )
                    for tp, _ in commits_to_make:
                        self._offset_trackers[tp].advance_high_water_mark()

                if not messages:
                    await asyncio.sleep(0.1)

        except Exception as exc:
            logger.error("Consumer loop error: %s", exc, exc_info=True)
        finally:
            await self._cleanup()
            self._shutdown_event.set()

    # ------------------------------------------------------------------
    def _get_partition_index(self, msg: Message) -> int:
        return hash(cast(bytes, msg.key() or b"")) % self._worker_pool_size

    async def _get_total_queued_messages(self) -> int:
        total = 0
        for queue_map in self._work_manager.get_virtual_queue_sizes().values():
            for size in queue_map.values():
                total += size
        return total

    def _log_partition_diagnostics(self) -> None:
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
            parts.append(
                "%s-%d queued=%d gaps=%d blocking=%s age=%s"
                % (
                    tp.topic,
                    tp.partition,
                    queued,
                    gap_count,
                    blocking_offset.start if blocking_offset else "none",
                    "%.2fs" % blocking_age if blocking_age is not None else "n/a",
                )
            )
            if (
                blocking_offset is not None
                and blocking_age is not None
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

        if not self._is_paused and current_load > self.MAX_IN_FLIGHT_MESSAGES:
            logger.warning(
                "Backpressure: pausing consumer (load=%d limit=%d)",
                current_load,
                self.MAX_IN_FLIGHT_MESSAGES,
            )
            self.consumer.pause(partitions)
            self._is_paused = True
        elif self._is_paused and current_load < self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME:
            logger.info(
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

    # ------------------------------------------------------------------
    def _on_assign(
        self, consumer: Consumer, partitions: List[KafkaTopicPartition]
    ) -> None:
        logger.info(
            "Partitions assigned: %s",
            ", ".join(f"{tp.topic}-{tp.partition}@{tp.offset}" for tp in partitions),
        )

        tp_dtos = [
            DtoTopicPartition(topic=tp.topic, partition=tp.partition)
            for tp in partitions
        ]
        self._work_manager.on_assign(tp_dtos)

        for partition in partitions:
            tp_dto = DtoTopicPartition(partition.topic, partition.partition)
            tracker = OffsetTracker(
                topic_partition=tp_dto,
                starting_offset=partition.offset,
                max_revoke_grace_ms=0,
                initial_completed_offsets=set(),
            )
            last_committed = (
                partition.offset - 1
                if partition.offset and partition.offset > 0
                else -1
            )
            tracker.last_committed_offset = last_committed
            tracker.last_fetched_offset = last_committed
            tracker.increment_epoch()
            self._offset_trackers[tp_dto] = tracker

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
                tp_to_commit = KafkaTopicPartition(
                    tp_dto.topic,
                    tp_dto.partition,
                    safe_offset + 1,
                )
                consumer.commit(offsets=[tp_to_commit], asynchronous=False)

            del self._offset_trackers[tp_dto]

    # ------------------------------------------------------------------
    async def start(self) -> None:
        try:
            self.producer = Producer(self._kafka_config.get_producer_config())
            self.admin = AdminClient(
                {"bootstrap.servers": self._kafka_config.BOOTSTRAP_SERVERS[0]}
            )
            self.consumer = Consumer(self._kafka_config.get_consumer_config())
            self.consumer.subscribe(
                [self._consume_topic],
                on_assign=self._on_assign,
                on_revoke=self._on_revoke,
            )
            self._running = True
            asyncio.create_task(self._run_consumer())
            logger.info("Kafka consumer subscribed to %s", self._consume_topic)
        except Exception as exc:
            logger.error("Failed to start BrokerPoller: %s", exc, exc_info=True)
            raise

    async def stop(self) -> None:
        if not self._running:
            return
        logger.info("Shutdown signal received")
        self._running = False
        await self._shutdown_event.wait()
        logger.info("BrokerPoller stopped")

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
