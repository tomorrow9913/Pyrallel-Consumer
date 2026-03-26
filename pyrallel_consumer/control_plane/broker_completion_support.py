from __future__ import annotations

import logging
from collections import OrderedDict
from typing import Any, Awaitable, Callable, Optional

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class BrokerCompletionSupport:
    def __init__(
        self,
        *,
        kafka_config: KafkaConfig,
        work_manager: Any,
        offset_trackers: dict[DtoTopicPartition, OffsetTracker],
        message_cache: OrderedDict[tuple[DtoTopicPartition, int], tuple[Any, Any]],
        should_cache_message_payloads: Callable[[], bool],
        pop_cached_message: Callable[
            [tuple[DtoTopicPartition, int]], Optional[tuple[Any, Any]]
        ],
        publish_to_dlq: Callable[..., Awaitable[bool]],
        logger: logging.Logger,
    ) -> None:
        self._kafka_config = kafka_config
        self._work_manager = work_manager
        self._offset_trackers = offset_trackers
        self._message_cache = message_cache
        self._should_cache_message_payloads = should_cache_message_payloads
        self._pop_cached_message = pop_cached_message
        self._publish_to_dlq = publish_to_dlq
        self._logger = logger

    async def handle_blocking_timeouts(
        self,
        *,
        max_blocking_duration_ms: int,
    ) -> list[CompletionEvent]:
        if max_blocking_duration_ms <= 0:
            return []

        threshold_sec = max_blocking_duration_ms / 1000.0
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

    async def process_completed_events(
        self,
        completed_events: list[CompletionEvent],
    ) -> int:
        for event in completed_events:
            tracker = self._offset_trackers.get(event.tp)
            if tracker is None:
                self._logger.warning("Completion for untracked partition %s", event.tp)
                continue
            if event.epoch != tracker.get_current_epoch():
                self._logger.warning(
                    "Discarding zombie completion for %s@%d (epoch %d vs %d)",
                    event.tp,
                    event.offset,
                    event.epoch,
                    tracker.get_current_epoch(),
                )
                continue

            dlq_success = True

            if event.status == CompletionStatus.FAILURE:
                self._logger.error(
                    "Message processing failed for %s@%d: %s",
                    event.tp,
                    event.offset,
                    event.error,
                )

                max_retries = self._kafka_config.parallel_consumer.execution.max_retries
                if self._kafka_config.dlq_enabled and event.attempt >= max_retries:
                    cache_key = (event.tp, event.offset)
                    cached_msg = self._message_cache.get(cache_key)
                    if cached_msg is None and self._should_cache_message_payloads():
                        self._logger.warning(
                            "No cached raw payload for %s@%d, falling back to metadata-only DLQ publish",
                            event.tp,
                            event.offset,
                        )
                    msg_key, msg_value = (
                        cached_msg if cached_msg is not None else (None, None)
                    )
                    error_text = event.error or "Unknown error"
                    dlq_success = await self._publish_to_dlq(
                        tp=event.tp,
                        offset=event.offset,
                        epoch=event.epoch,
                        key=msg_key,
                        value=msg_value,
                        error=error_text,
                        attempt=event.attempt,
                    )

                if not dlq_success:
                    self._logger.error(
                        "DLQ publish failed for %s@%d, skipping commit and retaining cache for retry",
                        event.tp,
                        event.offset,
                    )
                    continue

            tracker.mark_complete(event.offset)
            self._pop_cached_message((event.tp, event.offset))

        return len(completed_events)
