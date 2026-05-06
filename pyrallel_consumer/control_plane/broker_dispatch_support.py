# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_dispatch_support.py
# Role: Converts consumed Kafka messages into WorkManager submissions under ordering rules.
# Extend here for dispatch planning; keep Kafka polling ownership in broker_poller.py.
from __future__ import annotations

from typing import Any, Awaitable, Callable

from confluent_kafka import Message

from pyrallel_consumer.dto import OrderingMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


class BrokerDispatchSupport:
    """Group helper operations for broker dispatch planning."""

    def __init__(
        self,
        *,
        ordering_mode: OrderingMode,
        offset_trackers: dict[DtoTopicPartition, Any],
        cache_message_for_dlq: Callable[..., None],
        submit_message: Callable[..., Awaitable[None]],
        submit_grouped_messages: Callable[
            [dict[tuple[DtoTopicPartition, Any], list[tuple[int, int, Any, Any]]]],
            Awaitable[None],
        ],
        get_min_inflight_offset: Callable[[DtoTopicPartition], int | None],
        record_completed_offset_skip: Callable[[DtoTopicPartition, int], None]
        | None = None,
        logger: Any,
    ) -> None:
        """Initialize this component.

        Args:
            ordering_mode: Ordering mode used by the scheduler.
            offset_trackers: Offset trackers value used to initialize this component.
            cache_message_for_dlq: Cache message for dlq value used to initialize this component.
            submit_message: Submit message value used to initialize this component.
            submit_grouped_messages: Submit grouped messages value used to initialize this component.
            get_min_inflight_offset: Get min inflight offset value used to initialize this component.
            record_completed_offset_skip: Optional callback for skipped restored offsets.
            logger: Logger used for diagnostics.

        """
        self._ordering_mode = ordering_mode
        self._offset_trackers = offset_trackers
        self._cache_message_for_dlq = cache_message_for_dlq
        self._submit_message = submit_message
        self._submit_grouped_messages = submit_grouped_messages
        self._get_min_inflight_offset = get_min_inflight_offset
        self._record_completed_offset_skip = record_completed_offset_skip
        self._logger = logger

    async def dispatch_messages(self, messages: list[Message]) -> None:
        """Dispatch messages for broker dispatch planning.

        Args:
            messages: Messages value used by this function.

        """
        grouped_messages: dict[
            tuple[DtoTopicPartition, Any], list[tuple[int, int, Any, Any]]
        ] = {}

        for msg in messages:
            if msg.error():
                self._logger.warning("Consumed message with error: %s", msg.error())
                continue

            topic = msg.topic()
            partition = msg.partition()
            if topic is None or partition is None:
                self._logger.warning("Received message with None topic or partition")
                continue

            tp = DtoTopicPartition(topic=topic, partition=partition)
            offset_val = msg.offset()
            if offset_val is None:
                continue

            tracker = self._offset_trackers.get(tp)
            if tracker is None:
                self._logger.warning("Untracked partition %s - skipping", tp)
                continue

            if tracker.is_completed_uncommitted(offset_val) is True:
                if self._record_completed_offset_skip is not None:
                    try:
                        self._record_completed_offset_skip(tp, offset_val)
                    except Exception:
                        self._logger.exception(
                            "Completed-offset skip diagnostic callback failed for %s@%d",
                            tp,
                            offset_val,
                        )
                continue

            self._cache_message_for_dlq(
                tp=tp,
                offset=offset_val,
                key=msg.key(),
                value=msg.value(),
            )

            submit_key: Any
            if self._ordering_mode == OrderingMode.PARTITION:
                submit_key = tp.partition
            else:
                submit_key = msg.key()

            if self._ordering_mode == OrderingMode.UNORDERED:
                await self._submit_message(
                    tp=tp,
                    offset=offset_val,
                    epoch=tracker.get_current_epoch(),
                    key=submit_key,
                    payload=msg.value(),
                )
            else:
                grouped_messages.setdefault((tp, submit_key), []).append(
                    (
                        offset_val,
                        tracker.get_current_epoch(),
                        msg.value(),
                        msg.key(),
                    )
                )

        if grouped_messages:
            await self._submit_grouped_messages(grouped_messages)

    def build_commit_candidates(self) -> list[tuple[DtoTopicPartition, int]]:
        """Build commit candidates for broker dispatch planning.

        Returns:
            list[tuple[DtoTopicPartition, int]] result produced by this function.

        """
        commits_to_make: list[tuple[DtoTopicPartition, int]] = []

        for tp, tracker in self._offset_trackers.items():
            min_inflight = self._get_min_inflight_offset(tp)
            potential_hwm = tracker.get_committable_high_water_mark(min_inflight)
            if potential_hwm > tracker.last_committed_offset:
                commits_to_make.append((tp, potential_hwm))

        return commits_to_make
