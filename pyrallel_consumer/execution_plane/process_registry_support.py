from __future__ import annotations

import queue
from collections.abc import Callable
from typing import Any, Optional

from pyrallel_consumer.dto import TopicPartition

SerializedWorkItem = dict[str, Any]
InFlightRegistryKey = tuple[int, str, int, int]
InFlightRegistry = dict[InFlightRegistryKey, SerializedWorkItem]


class ProcessRegistrySupport:
    """Utility methods for in-flight registry state transitions."""

    @staticmethod
    def recover_dead_worker_items(
        *,
        worker_index: int,
        in_flight_registry: InFlightRegistry,
        max_retries: int,
        emit_worker_recovery_failure: Callable[..., None],
    ) -> list[SerializedWorkItem]:
        items = [
            (key, payload)
            for key, payload in in_flight_registry.items()
            if key[0] == worker_index
        ]
        to_requeue: list[SerializedWorkItem] = []
        for key, payload in items:
            if payload.get("timed_out"):
                emit_worker_recovery_failure(
                    worker_index,
                    payload,
                    error=payload.get("timeout_error", "task_timeout"),
                    attempt=payload.get("attempt", 1),
                    timeout_failure=True,
                )
                in_flight_registry.pop(key, None)
                continue

            attempts = payload.get("requeue_attempts", 0)
            if attempts >= max_retries:
                emit_worker_recovery_failure(
                    worker_index,
                    payload,
                    error="worker_died_max_retries",
                    attempt=attempts,
                )
            else:
                recovered_payload = dict(payload)
                recovered_payload["requeue_attempts"] = attempts + 1
                to_requeue.append(recovered_payload)

            in_flight_registry.pop(key, None)

        return to_requeue

    @staticmethod
    def apply_registry_event(
        *,
        event: dict[str, Any],
        in_flight_registry: InFlightRegistry,
        record_main_to_worker_ipc: Callable[[Any], None],
        record_worker_exec: Callable[[Any], None],
    ) -> None:
        kind = event.get("kind")
        key = event.get("key")
        if kind == "start" and key is not None:
            payload = dict(event.get("payload", {}))
            payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
            in_flight_registry[key] = payload
            return

        if kind == "timeout" and key in in_flight_registry:
            payload = dict(in_flight_registry.get(key, {}))
            payload["timed_out"] = True
            payload["timeout_error"] = event.get("timeout_error", "task_timeout")
            payload["attempt"] = event.get("attempt", 1)
            in_flight_registry[key] = payload
            return

        if kind == "done" and key is not None:
            in_flight_registry.pop(key, None)
            return

        if kind == "batch_received":
            record_main_to_worker_ipc(event.get("main_to_worker_ipc_seconds", 0.0))
            return

        if kind == "batch_completed":
            record_worker_exec(event.get("worker_exec_seconds", 0.0))

    @staticmethod
    def drain_registry_event_queue(
        *,
        registry_event_queue: Any,
        apply_event: Callable[[dict[str, Any]], None],
    ) -> int:
        if registry_event_queue is None:
            return 0

        drained = 0
        while True:
            try:
                event = registry_event_queue.get_nowait()
            except queue.Empty:
                return drained
            drained += 1
            apply_event(event)

    @staticmethod
    def get_min_inflight_offset(
        *,
        in_flight_registry: InFlightRegistry,
        tp: TopicPartition,
    ) -> Optional[int]:
        min_offset = None
        for (_worker_index, topic, partition, _offset), payload in (
            in_flight_registry.items()
        ):
            if topic == tp.topic and partition == tp.partition:
                value = payload.get("offset")
                if isinstance(value, int):
                    if min_offset is None or value < min_offset:
                        min_offset = value
        return min_offset
