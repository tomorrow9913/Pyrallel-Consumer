from __future__ import annotations

import asyncio
import queue
from multiprocessing import Queue
from typing import Any, Callable

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.process_transport import (
    PendingDispatchRecovery,
    ProcessTransport,
    RouteIdentity,
    SerializedWorkItem,
)


class SharedQueueProcessTransport(ProcessTransport):
    """Send process work through one shared multiprocessing queue."""

    def __init__(
        self,
        *,
        task_queue: Queue[Any],
        get_batch_accumulator: Callable[[], Any],
        work_item_from_dict: Callable[[SerializedWorkItem], WorkItem],
        increment_in_flight: Callable[[], None],
        sentinel: Any,
    ) -> None:
        self._task_queue = task_queue
        self._get_batch_accumulator = get_batch_accumulator
        self._work_item_from_dict = work_item_from_dict
        self._increment_in_flight = increment_in_flight
        self._sentinel = sentinel

    async def submit_work_item(
        self,
        work_item: WorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        """Submit work item for shared-queue process transport."""
        del route_identity
        batch_accumulator = self._get_batch_accumulator()
        if not batch_accumulator.add_nowait_fast_path(work_item):
            await asyncio.to_thread(batch_accumulator.add, work_item)
        if count_in_flight:
            self._increment_in_flight()

    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        """Dispatch payload for shared-queue process transport."""
        del route_identity
        batch_accumulator = self._get_batch_accumulator()
        work_item = self._work_item_from_dict(payload)
        if not batch_accumulator.add_nowait_fast_path(work_item):
            batch_accumulator.add(work_item)
        if count_in_flight:
            self._increment_in_flight()

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        """Start worker task source for shared-queue process transport."""
        del idx
        return self._task_queue, False

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        """Handle registry event for shared-queue process transport."""
        del event

    def recover_pending_dispatches(self, idx: int) -> list[PendingDispatchRecovery]:
        """Recover pending dispatches for shared-queue process transport."""
        del idx
        return []

    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        """Requeue payloads for shared-queue process transport."""
        if not payloads:
            return
        packed = msgpack.packb(payloads, use_bin_type=True)
        try:
            self._task_queue.put_nowait(packed)
        except queue.Full as exc:
            raise RuntimeError(
                "shared_queue transport queue is full during requeue"
            ) from exc

    def clear_pending_dispatches(self) -> None:
        """Clear pending dispatches for shared-queue process transport."""
        return None

    def signal_shutdown(self, worker_count: int) -> None:
        """Handle signal shutdown within shared-queue process transport."""
        for _ in range(worker_count):
            self._task_queue.put(self._sentinel)

    def close(self) -> None:
        """Release resources held by this component."""
        return None
