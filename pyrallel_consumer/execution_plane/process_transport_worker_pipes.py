from __future__ import annotations

import threading
import time
from multiprocessing import Pipe
from multiprocessing.connection import Connection
from typing import Any, Callable

from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.process_transport import (
    AsyncToThreadSubmitMixin,
    ProcessTransport,
    RouteIdentity,
    SerializedWorkItem,
    stable_worker_index_for_route,
)


class WorkerPipesProcessTransport(AsyncToThreadSubmitMixin, ProcessTransport):
    def __init__(
        self,
        *,
        process_count: int,
        queue_size: int,
        serialize_work_item: Callable[[WorkItem], SerializedWorkItem],
        serialize_batch_payload: Callable[[list[WorkItem], float], bytes],
        work_item_from_dict: Callable[[SerializedWorkItem], WorkItem],
        get_worker_pipe_senders: Callable[[], list[Connection]],
        get_pending_pipe_dispatch: Callable[
            [], dict[tuple[int, str, int, int], SerializedWorkItem]
        ],
        release_worker_pipe_queue_slot: Callable[[], None],
        increment_in_flight: Callable[[], None],
        pipe_sentinel: bytes,
    ) -> None:
        self._process_count = process_count
        self._serialize_work_item = serialize_work_item
        self._serialize_batch_payload = serialize_batch_payload
        self._work_item_from_dict = work_item_from_dict
        self._get_worker_pipe_senders = get_worker_pipe_senders
        self._get_pending_pipe_dispatch = get_pending_pipe_dispatch
        self._release_worker_pipe_queue_slot = release_worker_pipe_queue_slot
        self._increment_in_flight = increment_in_flight
        self._pipe_sentinel = pipe_sentinel
        self._worker_pipe_queue_slots = threading.BoundedSemaphore(value=queue_size)
        self._pending_dispatch_lock = threading.Lock()

    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        work_item = self._work_item_from_dict(payload)
        worker_idx = stable_worker_index_for_route(route_identity, self._process_count)
        self._worker_pipe_queue_slots.acquire()
        pending_key = (
            worker_idx,
            payload["topic"],
            payload["partition"],
            payload["offset"],
        )
        pending_dispatch = self._get_pending_pipe_dispatch()
        with self._pending_dispatch_lock:
            pending_dispatch[pending_key] = dict(payload)
        try:
            packed = self._serialize_batch_payload([work_item], time.monotonic())
            self._get_worker_pipe_senders()[worker_idx].send_bytes(packed)
        except Exception:
            with self._pending_dispatch_lock:
                pending_dispatch.pop(pending_key, None)
            self._release_worker_pipe_queue_slot()
            raise

        if count_in_flight:
            self._increment_in_flight()

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        worker_receiver, parent_sender = Pipe(duplex=False)
        senders = self._get_worker_pipe_senders()
        if idx < len(senders):
            existing_sender = senders[idx]
            close_existing = getattr(existing_sender, "close", None)
            if callable(close_existing):
                close_existing()
            senders[idx] = parent_sender
        else:
            senders.append(parent_sender)
        return worker_receiver, True

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        if event.get("kind") != "start":
            return
        key = event.get("key")
        pending_dispatch = self._get_pending_pipe_dispatch()
        with self._pending_dispatch_lock:
            if key not in pending_dispatch:
                return
            pending_dispatch.pop(key, None)
        self._release_worker_pipe_queue_slot()

    def recover_pending_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        pending_dispatch = self._get_pending_pipe_dispatch()
        to_requeue: list[SerializedWorkItem] = []
        with self._pending_dispatch_lock:
            for key, payload in list(pending_dispatch.items()):
                if key[0] != idx:
                    continue
                recovered_payload = dict(payload)
                recovered_payload["requeue_attempts"] = (
                    recovered_payload.get("requeue_attempts", 0) + 1
                )
                to_requeue.append(recovered_payload)
                pending_dispatch.pop(key, None)
                self._release_worker_pipe_queue_slot()
        return to_requeue

    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        for payload in payloads:
            work_item = self._work_item_from_dict(payload)
            self.dispatch_payload(
                payload,
                route_identity=RouteIdentity(
                    topic=work_item.tp.topic,
                    partition=work_item.tp.partition,
                    key=work_item.key,
                ),
                count_in_flight=False,
            )

    def signal_shutdown(self, worker_count: int) -> None:
        del worker_count
        for sender in self._get_worker_pipe_senders():
            try:
                sender.send_bytes(self._pipe_sentinel)
            except (BrokenPipeError, EOFError, OSError, ValueError):
                continue

    def close(self) -> None:
        for sender in self._get_worker_pipe_senders():
            close = getattr(sender, "close", None)
            if callable(close):
                close()
