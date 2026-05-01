# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_transport_worker_pipes.py
# Role: Implements worker-affine pipe transport with routing, slot accounting, and dispatch recovery.
# Extend here for pipe-based IPC behavior; keep generic transport contracts in process_transport.py.
from __future__ import annotations

import threading
import time
from multiprocessing import Pipe
from typing import Any, Callable

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import RouteBatch, WorkItem
from pyrallel_consumer.execution_plane.process_transport import (
    AsyncToThreadSubmitMixin,
    PendingDispatchRecovery,
    ProcessTransport,
    ProcessTransportCapabilities,
    RouteIdentity,
    SerializedWorkItem,
    logical_work_identity_from_payload,
    stable_worker_index_for_route,
    worker_execution_identity_from_payload,
)

PendingDispatchKey = tuple[int, str, int, int, str, int] | tuple[int, str]


class WorkerPipesProcessTransport(AsyncToThreadSubmitMixin, ProcessTransport):
    """Route process work through worker-affine pipe queues."""

    def __init__(
        self,
        *,
        process_count: int,
        queue_size: int,
        max_payload_bytes: int,
        serialize_work_item: Callable[[WorkItem], SerializedWorkItem],
        serialize_batch_payload: Callable[[Any, float], bytes],
        work_item_from_dict: Callable[[SerializedWorkItem], WorkItem],
        get_worker_pipe_senders: Callable[[], list[Any]],
        increment_in_flight: Callable[[], None],
        pipe_sentinel: bytes,
        slot_wait_liveness_check: Callable[[], None] | None = None,
        slot_wait_timeout_seconds: float = 0.05,
    ) -> None:
        """Initialize this component.

        Args:
            process_count: Number of process workers.
            queue_size: Maximum queued pipe messages.
            max_payload_bytes: Maximum allowed encoded payload size.
            serialize_work_item: Serialize work item value used to initialize this component.
            serialize_batch_payload: Serialize batch payload value used to initialize this component.
            work_item_from_dict: Work item from dict value used to initialize this component.
            get_worker_pipe_senders: Get worker pipe senders value used to initialize this component.
            increment_in_flight: Increment in flight value used to initialize this component.
            pipe_sentinel: Sentinel bytes used to stop pipe workers.
            slot_wait_liveness_check: Slot wait liveness check value used to initialize this component.
            slot_wait_timeout_seconds: Slot wait timeout seconds value used to initialize this component.

        """
        self._process_count = process_count
        self._max_payload_bytes = max_payload_bytes
        self._serialize_work_item = serialize_work_item
        self._serialize_batch_payload = serialize_batch_payload
        self._work_item_from_dict = work_item_from_dict
        self._get_worker_pipe_senders = get_worker_pipe_senders
        self._increment_in_flight = increment_in_flight
        self._pipe_sentinel = pipe_sentinel
        self._slot_wait_liveness_check = slot_wait_liveness_check
        self._slot_wait_timeout_seconds = slot_wait_timeout_seconds
        self._worker_pipe_queue_slots = threading.BoundedSemaphore(value=queue_size)
        self._pending_dispatch_lock = threading.Lock()
        self._pending_dispatch: dict[PendingDispatchKey, dict[str, Any]] = {}

    def dispatch_payload(
        self,
        payload: SerializedWorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        """Dispatch payload for worker-pipe process transport.

        Args:
            payload: Serialized or decoded payload handled by this function.
            route_identity: Routing identity used to choose the process worker.
            count_in_flight: Whether dispatch should increment in-flight accounting.

        Raises:
            Exception: Propagates serialization, validation, or pipe-send failures after
                releasing any pending dispatch accounting.

        """
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

    def dispatch_route_batch(
        self,
        route_batch: RouteBatch,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        """Dispatch one ordered route batch as a single worker-pipe payload."""
        worker_idx = stable_worker_index_for_route(route_identity, self._process_count)
        batch_with_worker = RouteBatch(
            batch_id=route_batch.batch_id,
            route_identity=route_batch.route_identity,
            worker_index=worker_idx,
            items=route_batch.items,
        )
        self._acquire_worker_pipe_queue_slot(worker_idx=worker_idx, payload={})
        pending_key = self._pending_dispatch_key_for_route_batch(
            worker_idx,
            batch_with_worker,
        )
        pending_payload = {
            "batch_id": batch_with_worker.batch_id,
            "route_identity": list(batch_with_worker.route_identity),
            "worker_index": worker_idx,
            "items": [
                self._serialize_work_item(item) for item in batch_with_worker.items
            ],
            "slot_released": False,
        }
        with self._pending_dispatch_lock:
            self._pending_dispatch[pending_key] = pending_payload
        try:
            packed = self._serialize_batch_payload(batch_with_worker, time.monotonic())
            self._validate_packed_payload(packed)
            self._send_packed_route_batch(
                worker_idx=worker_idx,
                batch_id=batch_with_worker.batch_id,
                packed_payload=packed,
            )
        except Exception:
            with self._pending_dispatch_lock:
                self._pending_dispatch.pop(pending_key, None)
            self._release_worker_pipe_queue_slot()
            raise

        if count_in_flight:
            for _item in batch_with_worker.items:
                self._increment_in_flight()

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        """Start worker task source for worker-pipe process transport.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            tuple[Any, bool] result produced by this function.

        """
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

    @property
    def capabilities(self) -> ProcessTransportCapabilities:
        """Return capabilities supported by this transport.

        Returns:
            ProcessTransportCapabilities result produced by this function.

        """
        return ProcessTransportCapabilities(pending_dispatch_recovery=True)

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        """Handle registry event for worker-pipe process transport.

        Args:
            event: Completion or registry event being processed.

        """
        kind = event.get("kind")
        if kind == "not_started":
            self._handle_not_started_route_batch_event(event)
            return
        if kind != "start":
            return
        key = event.get("key")
        payload = event.get("payload")
        pending_key = self._pending_dispatch_key_for_registry_start(key, payload)
        release_slot = False

        with self._pending_dispatch_lock:
            if pending_key in self._pending_dispatch:
                self._pending_dispatch.pop(pending_key, None)
                release_slot = True
            else:
                pending_key = self._pending_route_batch_key_for_registry_start(
                    key,
                    payload,
                )
                if pending_key in self._pending_dispatch:
                    pending_payload = self._pending_dispatch[pending_key]
                    self._remove_started_item_from_pending_route_batch(
                        pending_payload,
                        payload,
                    )
                    if not pending_payload.get("items"):
                        self._pending_dispatch.pop(pending_key, None)
                    if not pending_payload.get("slot_released", False):
                        pending_payload["slot_released"] = True
                        release_slot = True
                else:
                    return
            if pending_key is None:
                return
        if release_slot:
            self._release_worker_pipe_queue_slot()

    def _handle_not_started_route_batch_event(self, event: dict[str, Any]) -> None:
        """Remove skipped route-batch tail entries from pending dispatch state."""
        batch_id = event.get("batch_id")
        payloads = event.get("payloads")
        if batch_id is None or not isinstance(payloads, list):
            return
        with self._pending_dispatch_lock:
            for pending_key, pending_payload in list(self._pending_dispatch.items()):
                if (
                    not self._is_pending_route_batch(pending_payload)
                    or pending_payload.get("batch_id") != batch_id
                ):
                    continue
                for payload in payloads:
                    self._remove_started_item_from_pending_route_batch(
                        pending_payload,
                        payload,
                    )
                if not pending_payload.get("items"):
                    self._pending_dispatch.pop(pending_key, None)
                return

    def recover_pending_dispatches(self, idx: int) -> list[PendingDispatchRecovery]:
        """Recover pending dispatches for worker-pipe process transport.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            list[PendingDispatchRecovery] result produced by this function.

        """
        recovered: list[PendingDispatchRecovery] = []
        with self._pending_dispatch_lock:
            for key, payload in list(self._pending_dispatch.items()):
                if key[0] != idx:
                    continue
                if self._is_pending_route_batch(payload):
                    for item_payload in payload["items"]:
                        recovered_payload = dict(item_payload)
                        recovered.append(
                            PendingDispatchRecovery(
                                identity=worker_execution_identity_from_payload(
                                    idx,
                                    recovered_payload,
                                ),
                                payload=recovered_payload,
                            )
                        )
                else:
                    recovered_payload = dict(payload)
                    recovered.append(
                        PendingDispatchRecovery(
                            identity=worker_execution_identity_from_payload(
                                idx,
                                recovered_payload,
                            ),
                            payload=recovered_payload,
                        )
                    )
                self._pending_dispatch.pop(key, None)
                if not payload.get("slot_released", False):
                    self._release_worker_pipe_queue_slot()
        return recovered

    def requeue_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        """Requeue payloads for worker-pipe process transport.

        Args:
            payloads: Serialized payloads handled by this function.

        """
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
        """Handle signal shutdown within worker-pipe process transport.

        Args:
            worker_count: Number of worker shutdown signals to send.

        """
        del worker_count
        for sender in self._get_worker_pipe_senders():
            try:
                sender.send_bytes(self._pipe_sentinel)
            except (BrokenPipeError, EOFError, OSError, ValueError):
                continue

    def clear_pending_dispatches(self) -> None:
        """Clear pending dispatches for worker-pipe process transport."""
        with self._pending_dispatch_lock:
            self._pending_dispatch.clear()

    def close(self) -> None:
        """Release resources held by this component."""
        self.clear_pending_dispatches()
        for sender in self._get_worker_pipe_senders():
            close = getattr(sender, "close", None)
            if callable(close):
                close()

    @staticmethod
    def _pending_dispatch_key(
        worker_idx: int,
        payload: SerializedWorkItem,
    ) -> PendingDispatchKey:
        """Handle pending dispatch key within worker-pipe process transport.

        Args:
            worker_idx: Worker index selected for dispatch.
            payload: Serialized or decoded payload handled by this function.

        Returns:
            PendingDispatchKey result produced by this function.

        """
        identity = worker_execution_identity_from_payload(worker_idx, payload)
        return (
            identity.worker_index,
            identity.work.topic,
            identity.work.partition,
            identity.work.offset,
            identity.work.id,
            identity.work.epoch,
        )

    @staticmethod
    def _pending_dispatch_key_for_registry_start(
        key: Any,
        payload: Any,
    ) -> PendingDispatchKey | None:
        """Handle pending dispatch key for registry start within worker-pipe process transport.

        Args:
            key: Kafka record key or virtual queue key.
            payload: Serialized or decoded payload handled by this function.

        Returns:
            PendingDispatchKey | None result produced by this function.

        """
        if not isinstance(key, tuple) or len(key) < 4 or not isinstance(payload, dict):
            return None
        if (
            payload.get("topic") != key[1]
            or payload.get("partition") != key[2]
            or payload.get("offset") != key[3]
        ):
            return None
        return WorkerPipesProcessTransport._pending_dispatch_key(key[0], payload)

    @staticmethod
    def _pending_dispatch_key_for_route_batch(
        worker_idx: int,
        route_batch: RouteBatch,
    ) -> PendingDispatchKey:
        """Build the pending key for a pipe-level route batch send."""
        return (worker_idx, route_batch.batch_id)

    def _pending_route_batch_key_for_registry_start(
        self,
        key: Any,
        payload: Any,
    ) -> PendingDispatchKey | None:
        """Match a worker item start event back to its pending route batch."""
        if not isinstance(key, tuple) or len(key) < 4 or not isinstance(payload, dict):
            return None
        worker_idx = int(key[0])
        for pending_key, pending_payload in self._pending_dispatch.items():
            if pending_key[0] != worker_idx or not self._is_pending_route_batch(
                pending_payload
            ):
                continue
            for item_payload in pending_payload["items"]:
                if self._pending_dispatch_key_for_registry_start(
                    key,
                    item_payload,
                ) == self._pending_dispatch_key(worker_idx, payload):
                    return pending_key
        return None

    @staticmethod
    def _is_pending_route_batch(payload: dict[str, Any]) -> bool:
        """Return whether a pending dispatch payload represents a route batch."""
        return "batch_id" in payload and isinstance(payload.get("items"), list)

    @staticmethod
    def _remove_started_item_from_pending_route_batch(
        pending_payload: dict[str, Any],
        started_payload: Any,
    ) -> None:
        """Remove one started item from a pending route batch, leaving the tail."""
        if not isinstance(started_payload, dict):
            return
        started_identity = logical_work_identity_from_payload(started_payload)
        pending_payload["items"] = [
            item_payload
            for item_payload in pending_payload.get("items", [])
            if logical_work_identity_from_payload(item_payload) != started_identity
        ]

    def _release_worker_pipe_queue_slot(self) -> None:
        """Handle release worker pipe queue slot within worker-pipe process transport."""
        try:
            self._worker_pipe_queue_slots.release()
        except ValueError:
            return

    def _validate_packed_payload(self, payload: bytes) -> None:
        """Validate packed payload for worker-pipe process transport.

        Args:
            payload: Serialized or decoded payload handled by this function.

        Raises:
            ValueError: If the provided configuration or state is invalid.

        """
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
        """Handle acquire worker pipe queue slot within worker-pipe process transport.

        Args:
            worker_idx: Worker index selected for dispatch.
            payload: Serialized or decoded payload handled by this function.

        """
        del worker_idx, payload
        liveness_check = self._slot_wait_liveness_check
        timeout_seconds = self._slot_wait_timeout_seconds
        if liveness_check is None or timeout_seconds <= 0:
            self._worker_pipe_queue_slots.acquire()
            return

        while True:
            if self._worker_pipe_queue_slots.acquire(timeout=timeout_seconds):
                return
            liveness_check()

    def _send_packed_payload(
        self,
        *,
        worker_idx: int,
        payload: SerializedWorkItem,
        packed_payload: bytes,
    ) -> None:
        """Handle send packed payload within worker-pipe process transport.

        Args:
            worker_idx: Worker index selected for dispatch.
            payload: Serialized or decoded payload handled by this function.
            packed_payload: Serialized payload bytes to send to the worker.

        Raises:
            RuntimeError: If the runtime is in a failed or unsupported state.

        """
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

    def _send_packed_route_batch(
        self,
        *,
        worker_idx: int,
        batch_id: str,
        packed_payload: bytes,
    ) -> None:
        """Send one encoded route batch to a worker pipe."""
        senders = self._get_worker_pipe_senders()
        try:
            sender = senders[worker_idx]
        except IndexError as exc:
            raise RuntimeError(
                "Missing worker pipe sender for worker=%d batch_id=%s"
                % (worker_idx, batch_id)
            ) from exc

        send_bytes = getattr(sender, "send_bytes", None)
        if not callable(send_bytes):
            raise RuntimeError(
                "Worker pipe sender for worker=%d batch_id=%s is not writable"
                % (worker_idx, batch_id)
            )

        try:
            send_bytes(packed_payload)
        except Exception as exc:
            raise RuntimeError(
                "Failed to dispatch worker pipe route batch worker=%d batch_id=%s"
                % (worker_idx, batch_id)
            ) from exc
