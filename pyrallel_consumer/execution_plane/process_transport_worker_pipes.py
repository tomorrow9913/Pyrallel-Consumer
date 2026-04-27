from __future__ import annotations

import threading
import time
from multiprocessing import Pipe
from typing import Any, Callable

import msgpack  # type: ignore[import-untyped]

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
        max_payload_bytes: int,
        serialize_work_item: Callable[[WorkItem], SerializedWorkItem],
        serialize_batch_payload: Callable[[list[WorkItem], float], bytes],
        work_item_from_dict: Callable[[SerializedWorkItem], WorkItem],
        get_worker_pipe_senders: Callable[[], list[Any]],
        increment_in_flight: Callable[[], None],
        pipe_sentinel: bytes,
    ) -> None:
        self._process_count = process_count
        self._max_payload_bytes = max_payload_bytes
        self._serialize_work_item = serialize_work_item
        self._serialize_batch_payload = serialize_batch_payload
        self._work_item_from_dict = work_item_from_dict
        self._get_worker_pipe_senders = get_worker_pipe_senders
        self._increment_in_flight = increment_in_flight
        self._pipe_sentinel = pipe_sentinel
        self._worker_pipe_queue_slots = threading.BoundedSemaphore(value=queue_size)
        self._pending_dispatch_lock = threading.Lock()
        self._pending_dispatch: dict[
            tuple[int, str, int, int, str, int], SerializedWorkItem
        ] = {}

    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        work_item = self._work_item_from_dict(payload)
        worker_idx = stable_worker_index_for_route(route_identity, self._process_count)
        self._acquire_worker_pipe_queue_slot(worker_idx=worker_idx, payload=payload)
        pending_key = self._pending_dispatch_key(worker_idx, payload)
        with self._pending_dispatch_lock:
            self._pending_dispatch[pending_key] = dict(payload)
        try:
            packed = self._serialize_batch_payload([work_item], time.monotonic())
            self._validate_packed_payload(packed)
            self._send_packed_payload(
                worker_idx=worker_idx,
                payload=payload,
                packed_payload=packed,
            )
        except Exception:
            with self._pending_dispatch_lock:
                self._pending_dispatch.pop(pending_key, None)
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
        payload = event.get("payload")
        pending_key = None
        if isinstance(key, tuple) and key and isinstance(payload, dict):
            pending_key = self._pending_dispatch_key(key[0], payload)

        with self._pending_dispatch_lock:
            if pending_key in self._pending_dispatch:
                self._pending_dispatch.pop(pending_key, None)
            elif key in self._pending_dispatch:
                self._pending_dispatch.pop(key, None)
            else:
                return
        self._release_worker_pipe_queue_slot()

    def recover_pending_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        to_requeue: list[SerializedWorkItem] = []
        with self._pending_dispatch_lock:
            for key, payload in list(self._pending_dispatch.items()):
                if key[0] != idx:
                    continue
                recovered_payload = dict(payload)
                recovered_payload["requeue_attempts"] = (
                    recovered_payload.get("requeue_attempts", 0) + 1
                )
                to_requeue.append(recovered_payload)
                self._pending_dispatch.pop(key, None)
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

    def clear_pending_dispatches(self) -> None:
        with self._pending_dispatch_lock:
            self._pending_dispatch.clear()

    def close(self) -> None:
        self.clear_pending_dispatches()
        for sender in self._get_worker_pipe_senders():
            close = getattr(sender, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _pending_dispatch_key(
        worker_idx: int,
        payload: SerializedWorkItem,
    ) -> tuple[int, str, int, int, str, int]:
        return (
            worker_idx,
            payload["topic"],
            payload["partition"],
            payload["offset"],
            str(payload.get("id", "")),
            int(payload.get("epoch", 0)),
        )

    def _release_worker_pipe_queue_slot(self) -> None:
        try:
            self._worker_pipe_queue_slots.release()
        except ValueError:
            return

    def _validate_packed_payload(self, payload: bytes) -> None:
        if len(payload) > self._max_payload_bytes:
            raise ValueError("payload_too_large")

        unpacker = msgpack.Unpacker(
            raw=False,
            max_buffer_size=self._max_payload_bytes,
        )
        try:
            unpacker.feed(payload)
            decoded_items = list(unpacker)
        except Exception as exc:
            raise ValueError("invalid_worker_pipe_payload") from exc

        if not decoded_items:
            raise ValueError("invalid_worker_pipe_payload")

    def _acquire_worker_pipe_queue_slot(
        self,
        *,
        worker_idx: int,
        payload: SerializedWorkItem,
    ) -> None:
        del worker_idx, payload
        self._worker_pipe_queue_slots.acquire()

    def _send_packed_payload(
        self,
        *,
        worker_idx: int,
        payload: SerializedWorkItem,
        packed_payload: bytes,
    ) -> None:
        senders = self._get_worker_pipe_senders()
        try:
            sender = senders[worker_idx]
        except IndexError as exc:
            raise RuntimeError(
                "Missing worker pipe sender for worker=%d offset=%d"
                % (worker_idx, payload["offset"])
            ) from exc

        send_bytes = getattr(sender, "send_bytes", None)
        if not callable(send_bytes):
            raise RuntimeError(
                "Worker pipe sender for worker=%d offset=%d is not writable"
                % (worker_idx, payload["offset"])
            )

        try:
            send_bytes(packed_payload)
        except Exception as exc:
            raise RuntimeError(
                "Failed to dispatch worker pipe payload worker=%d offset=%d"
                % (worker_idx, payload["offset"])
            ) from exc
