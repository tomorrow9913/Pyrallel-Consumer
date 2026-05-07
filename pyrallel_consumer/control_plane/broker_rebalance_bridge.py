from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from confluent_kafka import TopicPartition as KafkaTopicPartition

from ..dto import TopicPartition as DtoTopicPartition
from .offset_tracker import OffsetTracker


@dataclass(frozen=True)
class RevokePreparation:
    """Prepared revoke state built on the BrokerPoller event loop."""

    revoked_tps: list[DtoTopicPartition]
    offsets_to_commit: list[KafkaTopicPartition]


class _BridgeCallState:
    """Track whether a bridged callback has entered synchronous state mutation."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started = False
        self._cancelled = False

    def mark_started(self) -> bool:
        """Mark the sync mutation as started unless callback timeout cancelled it."""
        with self._lock:
            if self._cancelled:
                return False
            self._started = True
            return True

    def cancel_if_not_started(self) -> bool:
        """Cancel future mutation only when the sync phase has not started."""
        with self._lock:
            if self._started:
                return False
            self._cancelled = True
            return True


class BrokerRebalanceBridge:
    """Bounded callback-to-event-loop bridge for assign/revoke state changes."""

    def __init__(
        self,
        *,
        get_event_loop: Callable[[], asyncio.AbstractEventLoop | None],
        timeout_seconds: Callable[[], float],
        assign_timeout_seconds: Callable[[], float],
        control_lock: asyncio.Lock,
        assign_sync: Callable[[dict[DtoTopicPartition, OffsetTracker]], None],
        prepare_revoke_sync: Callable[[list[KafkaTopicPartition]], RevokePreparation],
        cleanup_revoke_sync: Callable[
            [list[DtoTopicPartition], list[DtoTopicPartition]], None
        ],
        logger: Any,
    ) -> None:
        self._get_event_loop = get_event_loop
        self._timeout_seconds = timeout_seconds
        self._assign_timeout_seconds = assign_timeout_seconds
        self._control_lock = control_lock
        self._assign_sync = assign_sync
        self._prepare_revoke_sync = prepare_revoke_sync
        self._cleanup_revoke_sync = cleanup_revoke_sync
        self._logger = logger

    def assign_from_callback(
        self, assignments: dict[DtoTopicPartition, OffsetTracker]
    ) -> bool:
        """Run assign state mutation through the bounded event-loop bridge."""
        loop = self._get_event_loop()
        if loop is None or loop.is_closed():
            self._assign_sync(assignments)
            return True
        state = _BridgeCallState()
        future = asyncio.run_coroutine_threadsafe(
            self._assign_on_event_loop(assignments, state),
            loop,
        )
        try:
            future.result(timeout=self._assign_timeout_seconds())
            return True
        except Exception as exc:
            if isinstance(exc, TimeoutError) and not state.cancel_if_not_started():
                try:
                    future.result()
                    return True
                except Exception as drain_exc:  # noqa: BLE001
                    self._logger.warning(
                        "Rebalance assign bridge failed after timeout drain: %s",
                        drain_exc,
                    )
                    return False
            state.cancel_if_not_started()
            future.cancel()
            self._logger.warning("Rebalance assign bridge failed: %s", exc)
            return False

    async def _assign_on_event_loop(
        self,
        assignments: dict[DtoTopicPartition, OffsetTracker],
        state: _BridgeCallState,
    ) -> None:
        """Apply assignment under the control lock on the event loop."""
        async with self._control_lock:
            if not state.mark_started():
                raise asyncio.CancelledError
            self._assign_sync(assignments)

    def prepare_revoke_from_callback(
        self, partitions: list[KafkaTopicPartition]
    ) -> RevokePreparation | None:
        """Prepare revoke state through the bounded event-loop bridge."""
        loop = self._get_event_loop()
        if loop is None or loop.is_closed():
            return self._prepare_revoke_sync(partitions)
        state = _BridgeCallState()
        future = asyncio.run_coroutine_threadsafe(
            self._prepare_revoke_on_event_loop(partitions, state),
            loop,
        )
        try:
            return future.result(timeout=self._timeout_seconds())
        except Exception as exc:
            if isinstance(exc, TimeoutError) and not state.cancel_if_not_started():
                try:
                    return future.result()
                except Exception as drain_exc:  # noqa: BLE001
                    self._logger.warning(
                        "Rebalance revoke prep bridge failed after timeout drain: %s",
                        drain_exc,
                    )
                    return None
            state.cancel_if_not_started()
            future.cancel()
            self._logger.warning("Rebalance revoke prep bridge failed: %s", exc)
            return None

    async def _prepare_revoke_on_event_loop(
        self,
        partitions: list[KafkaTopicPartition],
        state: _BridgeCallState,
    ) -> RevokePreparation:
        """Build revoke preparation under the control lock on the event loop."""
        async with self._control_lock:
            if not state.mark_started():
                raise asyncio.CancelledError
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
        state = _BridgeCallState()
        future = asyncio.run_coroutine_threadsafe(
            self._cleanup_revoke_on_event_loop(revoked_tps, failed_tps, state),
            loop,
        )
        try:
            future.result(timeout=self._timeout_seconds())
            return True
        except Exception as exc:
            if isinstance(exc, TimeoutError) and not state.cancel_if_not_started():
                try:
                    future.result()
                    return True
                except Exception as drain_exc:  # noqa: BLE001
                    self._logger.warning(
                        "Rebalance revoke cleanup bridge failed after timeout drain: %s",
                        drain_exc,
                    )
                    return False
            state.cancel_if_not_started()
            future.cancel()
            self._logger.warning("Rebalance revoke cleanup bridge failed: %s", exc)
            return False

    async def _cleanup_revoke_on_event_loop(
        self,
        revoked_tps: list[DtoTopicPartition],
        failed_tps: list[DtoTopicPartition],
        state: _BridgeCallState,
    ) -> None:
        """Apply revoke cleanup under the control lock on the event loop."""
        async with self._control_lock:
            if not state.mark_started():
                raise asyncio.CancelledError
            self._cleanup_revoke_sync(revoked_tps, failed_tps)
