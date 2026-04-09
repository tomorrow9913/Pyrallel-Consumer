from __future__ import annotations

import logging
from typing import Optional

from confluent_kafka import OFFSET_INVALID, Consumer, KafkaException
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class BrokerRebalanceSupport:
    def __init__(
        self,
        metadata_encoder: MetadataEncoder,
        tracker_factory=OffsetTracker,
        committed_lookup_timeout_seconds: float = 5.0,
    ) -> None:
        self._metadata_encoder = metadata_encoder
        self._tracker_factory = tracker_factory
        self._committed_lookup_timeout_seconds = committed_lookup_timeout_seconds

    def _decode_assignment_completed_offsets(
        self,
        *,
        strategy: str,
        partition: KafkaTopicPartition,
        committed_partition: Optional[KafkaTopicPartition],
        last_committed: int,
    ) -> set[int]:
        if strategy != "metadata_snapshot":
            return set()

        raw_metadata = getattr(committed_partition, "metadata", None)
        if not isinstance(raw_metadata, str) or not raw_metadata:
            raw_metadata = getattr(partition, "metadata", None)
        if not isinstance(raw_metadata, str) or not raw_metadata:
            return set()

        decoded = self._metadata_encoder.decode_metadata(raw_metadata)
        return {offset for offset in decoded if offset > last_committed}

    def _encode_revoke_metadata(
        self,
        *,
        strategy: str,
        tracker: OffsetTracker,
        base_offset: int,
    ) -> str:
        if strategy != "metadata_snapshot":
            return ""

        metadata_offsets = {
            offset for offset in tracker.completed_offsets if offset >= base_offset
        }
        metadata = self._metadata_encoder.encode_metadata(metadata_offsets, base_offset)
        if isinstance(metadata, str):
            return metadata
        if isinstance(metadata, (bytes, bytearray)):
            return bytes(metadata).decode("utf-8", errors="ignore")
        return ""

    def build_assignments(
        self,
        *,
        consumer: Consumer,
        partitions: list[KafkaTopicPartition],
        strategy: str,
        max_revoke_grace_ms: int,
        logger=None,
    ) -> dict[DtoTopicPartition, OffsetTracker]:
        if logger is None:
            logger = logging.getLogger(__name__)
        committed_offsets: dict[tuple[str, int], KafkaTopicPartition] = {}
        try:
            committed_partitions = consumer.committed(
                partitions,
                timeout=self._committed_lookup_timeout_seconds,
            )
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

        assignments: dict[DtoTopicPartition, OffsetTracker] = {}
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
                strategy=strategy,
                partition=partition,
                committed_partition=committed_partition,
                last_committed=last_committed,
            )
            tracker = self._tracker_factory(
                topic_partition=tp_dto,
                starting_offset=tracker_starting_offset,
                max_revoke_grace_ms=max_revoke_grace_ms,
                initial_completed_offsets=initial_completed_offsets,
            )
            tracker.last_committed_offset = last_committed
            tracker.last_fetched_offset = max(
                [last_committed, *initial_completed_offsets]
                if initial_completed_offsets
                else [last_committed]
            )
            tracker.increment_epoch()
            assignments[tp_dto] = tracker
        return assignments

    def handle_revoke(
        self,
        *,
        consumer: Consumer,
        partitions: list[KafkaTopicPartition],
        work_manager,
        offset_trackers: dict[DtoTopicPartition, OffsetTracker],
        drop_cached_partition_messages,
        strategy: str,
        logger,
    ) -> None:
        tp_dtos = [
            DtoTopicPartition(topic=tp.topic, partition=tp.partition)
            for tp in partitions
        ]
        work_manager.on_revoke(tp_dtos)

        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(tp_kafka.topic, tp_kafka.partition)
            drop_cached_partition_messages(tp_dto)
            tracker = offset_trackers.get(tp_dto)
            if tracker is None:
                continue

            tracker.advance_high_water_mark()
            safe_offset = tracker.last_committed_offset
            if safe_offset >= 0:
                metadata = self._encode_revoke_metadata(
                    strategy=strategy,
                    tracker=tracker,
                    base_offset=safe_offset + 1,
                )
                # Kafka committed offsets can only persist the next contiguous
                # restart position. In metadata_snapshot mode we also attach the
                # sparse completed offsets beyond that HWM here so assignment can
                # hydrate OffsetTracker later and avoid unnecessary replay.
                #
                # confluent-kafka accepts metadata at construction time, but the
                # shipped Python stub does not model that keyword argument yet.
                tp_to_commit = KafkaTopicPartition(
                    tp_dto.topic,
                    tp_dto.partition,
                    safe_offset + 1,
                    metadata=metadata,  # type: ignore[call-arg]
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

            del offset_trackers[tp_dto]
