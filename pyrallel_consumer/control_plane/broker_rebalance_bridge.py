from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from confluent_kafka import Consumer
from confluent_kafka import TopicPartition as KafkaTopicPartition

from ..dto import TopicPartition as DtoTopicPartition


@dataclass(frozen=True)
class RevokePreparation:
    """Prepared revoke state built on the BrokerPoller event loop."""

    revoked_tps: list[DtoTopicPartition]
    offsets_to_commit: list[KafkaTopicPartition]


class BrokerRebalanceBridge:
    """Bounded callback-to-event-loop bridge for assign/revoke state changes."""

    def __init__(
        self,
        *,
        get_event_loop: Callable[[], asyncio.AbstractEventLoop | None],
        timeout_seconds: Callable[[], float],
        control_lock: asyncio.Lock,
        assign_sync: Callable[[Consumer, list[KafkaTopicPartition]], None],
        prepare_revoke_sync: Callable[[list[KafkaTopicPartition]], RevokePreparation],
        cleanup_revoke_sync: Callable[
            [list[DtoTopicPartition], list[DtoTopicPartition]], None
        ],
        logger: Any,
    ) -> None:
        self._get_event_loop = get_event_loop
        self._timeout_seconds = timeout_seconds
        self._control_lock = control_lock
        self._assign_sync = assign_sync
        self._prepare_revoke_sync = prepare_revoke_sync
        self._cleanup_revoke_sync = cleanup_revoke_sync
        self._logger = logger

    def assign_from_callback(
        self, consumer: Consumer, partitions: list[KafkaTopicPartition]
    ) -> bool:
        """Run assign state mutation through the bounded event-loop bridge."""
        loop = self._get_event_loop()
        if loop is None or loop.is_closed():
            self._assign_sync(consumer, partitions)
            return True
        coroutine = self._assign_on_event_loop(consumer, partitions)
        try:
            asyncio.run_coroutine_threadsafe(coroutine, loop).result(
                timeout=self._timeout_seconds()
            )
            return True
        except Exception as exc:
            coroutine.close()
            self._logger.warning("Rebalance assign bridge failed: %s", exc)
            return False

    async def _assign_on_event_loop(
        self, consumer: Consumer, partitions: list[KafkaTopicPartition]
    ) -> None:
        """Apply assignment under the control lock on the event loop."""
        async with self._control_lock:
            self._assign_sync(consumer, partitions)

    def prepare_revoke_from_callback(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation | None:
        """Prepare revoke state through the bounded event-loop bridge."""
        loop = self._get_event_loop()
        if loop is None or loop.is_closed():
            return self._prepare_revoke_sync(partitions)
        try:
            return asyncio.run_coroutine_threadsafe(
                self._prepare_revoke_on_event_loop(partitions),
                loop,
            ).result(timeout=self._timeout_seconds())
        except Exception as exc:
            self._logger.warning("Rebalance revoke prep bridge failed: %s", exc)
            return None

    async def _prepare_revoke_on_event_loop(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation:
        """Build revoke preparation under the control lock on the event loop."""
        async with self._control_lock:
            return self._prepare_revoke_sync(partitions)

    def cleanup_revoke_from_callback(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> bool:
        """Clean up revoked partition state through the event-loop bridge."""
        loop = self._get_event_loop()
        if loop is None or loop.is_closed():
            self._cleanup_revoke_sync(revoked_tps, failed_tps)
            return True
        try:
            asyncio.run_coroutine_threadsafe(
                self._cleanup_revoke_on_event_loop(revoked_tps, failed_tps),
                loop,
            ).result(timeout=self._timeout_seconds())
            return True
        except Exception as exc:
            self._logger.warning("Rebalance revoke cleanup bridge failed: %s", exc)
            return False

    async def _cleanup_revoke_on_event_loop(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
    ) -> None:
        """Apply revoke cleanup under the control lock on the event loop."""
        async with self._control_lock:
            self._cleanup_revoke_sync(revoked_tps, failed_tps)
