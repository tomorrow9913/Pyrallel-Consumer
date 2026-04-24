from __future__ import annotations

import asyncio
import queue
from multiprocessing import Queue
from typing import Any, Callable

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.process_transport import (
    ProcessTransport,
    RouteIdentity,
    SerializedWorkItem,
)


class SharedQueueProcessTransport(ProcessTransport):
    def __init__(
        self,
        *,
        task_queue: Queue[Any],
        batch_accumulator: Any,
        work_item_from_dict: Callable[[SerializedWorkItem], WorkItem],
        increment_in_flight: Callable[[], None],
        sentinel: Any,
    ) -> None:
        self._task_queue = task_queue
        self._batch_accumulator = batch_accumulator
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
        del route_identity
        if not self._batch_accumulator.add_nowait_fast_path(work_item):
            await asyncio.to_thread(self._batch_accumulator.add, work_item)
        if count_in_flight:
            self._increment_in_flight()

    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        del route_identity
        work_item = self._work_item_from_dict(payload)
        if not self._batch_accumulator.add_nowait_fast_path(work_item):
            self._batch_accumulator.add(work_item)
        if count_in_flight:
            self._increment_in_flight()

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        del idx
        return self._task_queue, False

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        del event

    def recover_pending_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        del idx
        return []

    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        if not payloads:
            return
        packed = msgpack.packb(payloads, use_bin_type=True)
        try:
            self._task_queue.put(packed)
        except queue.Full as exc:
            raise RuntimeError(
                "shared_queue transport queue is full during requeue"
            ) from exc

    def signal_shutdown(self, worker_count: int) -> None:
        for _ in range(worker_count):
            self._task_queue.put(self._sentinel)

    def close(self) -> None:
        return None
