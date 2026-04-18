from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from itertools import islice
from typing import Any, Awaitable, Callable, Optional, Tuple, cast

from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka import KafkaException

from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class DlqCacheSupport:
    @staticmethod
    def estimate_cached_payload_bytes(payload: Any) -> int:
        if payload is None:
            return 0
        if isinstance(payload, memoryview):
            return len(payload)
        if isinstance(payload, (bytes, bytearray)):
            return len(payload)
        if isinstance(payload, str):
            return len(payload.encode("utf-8"))
        return 0

    def get_cached_message_size(self, key: Any, value: Any) -> int:
        return self.estimate_cached_payload_bytes(
            key
        ) + self.estimate_cached_payload_bytes(value)

    def pop_cached_message(
        self,
        message_cache: OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]],
        size_bytes: int,
        cache_key: Tuple[DtoTopicPartition, int],
    ) -> tuple[Optional[Tuple[Any, Any]], int]:
        cached_message = message_cache.pop(cache_key, None)
        if cached_message is None:
            return None, size_bytes

        size_bytes = max(0, size_bytes - self.get_cached_message_size(*cached_message))
        return cached_message, size_bytes

    def cache_message_for_dlq(
        self,
        *,
        message_cache: OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]],
        size_bytes: int,
        should_cache: bool,
        max_bytes: int,
        tp: DtoTopicPartition,
        offset: int,
        key: Any,
        value: Any,
        logger: logging.Logger,
    ) -> int:
        cache_key = (tp, offset)
        if not should_cache:
            _, size_bytes = self.pop_cached_message(
                message_cache, size_bytes, cache_key
            )
            return size_bytes

        _, size_bytes = self.pop_cached_message(message_cache, size_bytes, cache_key)
        entry_size = self.get_cached_message_size(key, value)

        if max_bytes > 0 and entry_size > max_bytes:
            logger.warning(
                "Skipping raw DLQ cache for %s@%d because payload size %d exceeds cache budget %d",
                tp,
                offset,
                entry_size,
                max_bytes,
            )
            return size_bytes

        while message_cache and max_bytes > 0 and size_bytes + entry_size > max_bytes:
            evicted_key, evicted_value = message_cache.popitem(last=False)
            size_bytes = max(
                0,
                size_bytes - self.get_cached_message_size(*evicted_value),
            )
            logger.warning(
                "Evicted raw DLQ cache entry for %s@%d to stay within %d bytes",
                evicted_key[0],
                evicted_key[1],
                max_bytes,
            )

        message_cache[cache_key] = (key, value)
        return size_bytes + entry_size

    def drop_partition_messages(
        self,
        *,
        message_cache: OrderedDict[Tuple[DtoTopicPartition, int], Tuple[Any, Any]],
        size_bytes: int,
        tp: DtoTopicPartition,
    ) -> int:
        cache_keys_to_remove = [
            cache_key for cache_key in message_cache if cache_key[0] == tp
        ]
        for cache_key in cache_keys_to_remove:
            _, size_bytes = self.pop_cached_message(
                message_cache, size_bytes, cache_key
            )
        return size_bytes


class BrokerCommitPlanner:
    def __init__(
        self,
        metadata_encoder: MetadataEncoder,
        max_completed_offsets: int,
    ) -> None:
        self.metadata_encoder = metadata_encoder
        self._max_completed_offsets = max_completed_offsets

    def decode_assignment_completed_offsets(
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

        decoded = self.metadata_encoder.decode_metadata(raw_metadata)
        return {offset for offset in decoded if offset > last_committed}

    def get_commit_metadata_offsets(
        self, tracker: OffsetTracker, base_offset: int
    ) -> set[int]:
        if hasattr(tracker.completed_offsets, "irange"):
            return set(
                islice(
                    tracker.completed_offsets.irange(minimum=base_offset),
                    self._max_completed_offsets,
                )
            )

        return {offset for offset in tracker.completed_offsets if offset >= base_offset}

    def encode_revoke_metadata(
        self,
        *,
        strategy: str,
        tracker: OffsetTracker,
        base_offset: int,
    ) -> str:
        if strategy != "metadata_snapshot":
            return ""
        metadata_offsets = self.get_commit_metadata_offsets(tracker, base_offset)
        metadata = self.metadata_encoder.encode_metadata(metadata_offsets, base_offset)
        if isinstance(metadata, str):
            return metadata
        if isinstance(metadata, (bytes, bytearray)):
            return bytes(metadata).decode("utf-8", errors="ignore")
        return ""

    def build_offsets_to_commit(
        self,
        *,
        commits_to_make: list[tuple[DtoTopicPartition, int]],
        trackers: dict[DtoTopicPartition, OffsetTracker],
        strategy: str,
    ) -> list[KafkaTopicPartition]:
        offsets_to_commit: list[KafkaTopicPartition] = []
        for tp, safe_offset in commits_to_make:
            tracker = trackers[tp]
            base_offset = safe_offset + 1
            metadata_text = ""
            if strategy == "metadata_snapshot":
                metadata_offsets = self.get_commit_metadata_offsets(
                    tracker, base_offset
                )
                metadata = self.metadata_encoder.encode_metadata(
                    metadata_offsets, base_offset
                )
                if isinstance(metadata, (bytes, bytearray)):
                    metadata_text = bytes(metadata).decode("utf-8", errors="ignore")
                elif isinstance(metadata, str):
                    metadata_text = metadata

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

        return offsets_to_commit


class BrokerCommitSupport:
    def __init__(
        self,
        *,
        commit_planner: BrokerCommitPlanner,
        logger: logging.Logger,
    ) -> None:
        self._commit_planner = commit_planner
        self._logger = logger

    async def commit_ready_offsets(
        self,
        *,
        commit_lock: asyncio.Lock,
        control_lock: asyncio.Lock,
        build_commit_candidates: Callable[[], list[tuple[DtoTopicPartition, int]]],
        commit_offsets: Callable[[list[tuple[DtoTopicPartition, int]]], Awaitable[None]],
    ) -> None:
        async with commit_lock:
            async with control_lock:
                commits_to_make = build_commit_candidates()
            if commits_to_make:
                await commit_offsets(commits_to_make)

    async def commit_offsets(
        self,
        *,
        consumer: Any,
        offset_trackers: dict[DtoTopicPartition, OffsetTracker],
        control_lock: asyncio.Lock,
        commits_to_make: list[tuple[DtoTopicPartition, int]],
        strategy: str,
        to_thread: Callable[..., Awaitable[Any]],
    ) -> None:
        async with control_lock:
            tracked_commits: list[tuple[DtoTopicPartition, int]] = []
            tracker_snapshot: dict[DtoTopicPartition, OffsetTracker] = {}
            for tp, safe_offset in commits_to_make:
                tracker = offset_trackers.get(tp)
                if tracker is None:
                    self._logger.debug(
                        "Skipping commit candidate for untracked partition %s", tp
                    )
                    continue
                tracked_commits.append((tp, safe_offset))
                tracker_snapshot[tp] = tracker

        if not tracked_commits:
            return

        offsets_to_commit = self._commit_planner.build_offsets_to_commit(
            commits_to_make=tracked_commits,
            trackers=tracker_snapshot,
            strategy=strategy,
        )

        max_attempts = 2
        for attempt in range(max_attempts):
            try:
                await to_thread(
                    consumer.commit,
                    offsets=offsets_to_commit,
                    asynchronous=False,
                )
                async with control_lock:
                    for tp, safe_offset in tracked_commits:
                        tracker = offset_trackers.get(tp)
                        if tracker is None:
                            continue
                        tracker.commit_through(safe_offset)
                return
            except KafkaException as exc:
                if attempt < max_attempts - 1:
                    self._logger.warning(
                        "Commit failed (attempt %d/%d), retrying: %s",
                        attempt + 1,
                        max_attempts,
                        exc,
                    )
                else:
                    self._logger.error(
                        "Commit failed after %d attempts, skipping: %s",
                        max_attempts,
                        exc,
                    )


class BrokerDrainSupport:
    async def drain_completion_events_once(
        self,
        *,
        poll_completed_events: Callable[[], Awaitable[list[Any]]],
        handle_blocking_timeouts: Callable[[], Awaitable[list[Any]]],
        process_completed_events: Callable[[list[Any]], Awaitable[None]],
        schedule: Callable[[], Awaitable[None]],
    ) -> bool:
        completed_events = await poll_completed_events()
        timeout_events = await handle_blocking_timeouts()
        if timeout_events:
            completed_events.extend(timeout_events)
        if not completed_events:
            return False

        await process_completed_events(completed_events)
        await schedule()
        return True
