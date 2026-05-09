# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_codec.py
# Role: Serializes and deserializes WorkItem, CompletionEvent, registry, and batch IPC payloads.
# Extend here for wire-format changes shared by process transports and workers.
from __future__ import annotations

from typing import Any, Optional, cast

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import (
    WORK_ITEM_POISON_KEY_UNSET,
    BatchCompletion,
    CompletionEvent,
    CompletionStatus,
    RouteBatch,
    TopicPartition,
    WorkItem,
)

SerializedWorkItem = dict[str, Any]
SerializedCompletionEvent = dict[str, Any]
SerializedRegistryEvent = dict[str, Any]
SerializedBatchEnvelope = dict[str, Any]
SerializedRouteBatch = dict[str, Any]
SerializedBatchCompletion = dict[str, Any]
SerializedWorkerPipePayload = dict[str, Any]

WORKER_PIPE_KIND_WORK_ITEMS = "work_items"
WORKER_PIPE_KIND_ROUTE_BATCH = "route_batch"
BATCH_COMPLETION_KIND = "batch_completion"


def _require_fields(
    payload: dict[str, Any],
    required_fields: tuple[str, ...],
    error_name: str,
) -> None:
    """Raise a shaped validation error when required payload fields are absent."""
    missing_fields = [field for field in required_fields if field not in payload]
    if missing_fields:
        raise ValueError("%s_missing_%s" % (error_name, "_".join(missing_fields)))


def _require_non_empty_list(value: Any, field_name: str, error_name: str) -> list[Any]:
    """Return a non-empty list field or raise a shaped validation error."""
    if not isinstance(value, list):
        raise ValueError("%s_%s_not_list" % (error_name, field_name))
    if not value:
        raise ValueError("%s_%s_empty" % (error_name, field_name))
    return value


def work_item_to_dict(item: WorkItem) -> SerializedWorkItem:
    """Convert work item to dict.

    Args:
        item (WorkItem): 직렬화할 작업 항목입니다.

    Returns:
        SerializedWorkItem: process IPC로 보낼 수 있는 dict payload입니다.

    """
    payload: SerializedWorkItem = {
        "id": item.id,
        "topic": item.tp.topic,
        "partition": item.tp.partition,
        "offset": item.offset,
        "epoch": item.epoch,
        "key": item.key,
        "payload": item.payload,
        "requeue_attempts": item.requeue_attempts,
    }
    if item.poison_key is not WORK_ITEM_POISON_KEY_UNSET:
        payload["poison_key"] = item.poison_key
    return payload


def work_item_from_dict(payload: SerializedWorkItem) -> WorkItem:
    """Build work item from dict.

    Args:
        payload (SerializedWorkItem): WorkItem을 복원할 serialized payload입니다.

    Returns:
        WorkItem: 복원된 작업 항목입니다.

    """
    return WorkItem(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        key=payload.get("key"),
        payload=payload.get("payload"),
        requeue_attempts=payload.get("requeue_attempts", 0),
        poison_key=payload.get("poison_key", WORK_ITEM_POISON_KEY_UNSET),
    )


def work_item_identity_payload(payload: SerializedWorkItem) -> SerializedWorkItem:
    """Handle work item identity payload within process payload serialization.

    Args:
        payload (SerializedWorkItem): identity 필드를 추출할 serialized payload입니다.

    Returns:
        SerializedWorkItem: id, topic, partition, offset, epoch만 포함한 payload입니다.

    """
    return {
        "id": payload["id"],
        "topic": payload["topic"],
        "partition": payload["partition"],
        "offset": payload["offset"],
        "epoch": payload["epoch"],
    }


def completion_event_to_dict(
    event: CompletionEvent,
    extra_fields: Optional[dict[str, Any]] = None,
) -> SerializedCompletionEvent:
    """Convert completion event to dict.

    Args:
        event (CompletionEvent): 직렬화할 완료 이벤트입니다.
        extra_fields (Optional[dict[str, Any]]): IPC timing 등 추가로 병합할 필드입니다.

    Returns:
        SerializedCompletionEvent: process IPC로 보낼 수 있는 완료 이벤트 payload입니다.

    """
    payload: SerializedCompletionEvent = {
        "id": event.id,
        "topic": event.tp.topic,
        "partition": event.tp.partition,
        "offset": event.offset,
        "epoch": event.epoch,
        "status": event.status.value,
        "error": event.error,
        "attempt": event.attempt,
        "terminal": event.terminal,
        "failure_class": event.failure_class,
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def completion_event_from_dict(
    payload: SerializedCompletionEvent,
) -> CompletionEvent:
    """Build completion event from dict.

    Args:
        payload (SerializedCompletionEvent): 완료 이벤트를 복원할 serialized payload입니다.

    Returns:
        CompletionEvent: 복원된 완료 이벤트입니다.

    """
    return CompletionEvent(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        status=CompletionStatus(payload["status"]),
        error=payload.get("error"),
        attempt=payload["attempt"],
        terminal=bool(payload.get("terminal", False)),
        failure_class=payload.get("failure_class"),
    )


def route_batch_to_dict(batch: RouteBatch) -> SerializedRouteBatch:
    """Convert an internal route batch to a process wire payload."""
    return {
        "batch_id": batch.batch_id,
        "route_identity": list(batch.route_identity),
        "worker_index": batch.worker_index,
        "items": [work_item_to_dict(item) for item in batch.items],
    }


def route_batch_from_dict(payload: SerializedRouteBatch) -> RouteBatch:
    """Build an internal route batch from a process wire payload."""
    _require_fields(
        payload, ("batch_id", "route_identity", "items"), "invalid_route_batch"
    )
    route_identity = payload["route_identity"]
    if not isinstance(route_identity, (list, tuple)):
        raise ValueError("invalid_route_batch_route_identity_not_sequence")
    items = _require_non_empty_list(
        payload["items"],
        "items",
        "invalid_route_batch",
    )
    return RouteBatch(
        batch_id=payload["batch_id"],
        route_identity=tuple(route_identity),
        worker_index=payload.get("worker_index"),
        items=[work_item_from_dict(item) for item in items],
    )


def batch_completion_to_dict(
    completion: BatchCompletion,
) -> SerializedBatchCompletion:
    """Convert an internal batch completion to a process wire payload."""
    return {
        "batch_id": completion.batch_id,
        "route_identity": list(completion.route_identity),
        "results": [completion_event_to_dict(event) for event in completion.results],
    }


def batch_completion_from_dict(
    payload: SerializedBatchCompletion,
) -> BatchCompletion:
    """Build an internal batch completion from a process wire payload."""
    _require_fields(
        payload,
        ("batch_id", "route_identity", "results"),
        "invalid_batch_completion",
    )
    route_identity = payload["route_identity"]
    if not isinstance(route_identity, (list, tuple)):
        raise ValueError("invalid_batch_completion_route_identity_not_sequence")
    results = _require_non_empty_list(
        payload["results"],
        "results",
        "invalid_batch_completion",
    )
    return BatchCompletion(
        batch_id=payload["batch_id"],
        route_identity=tuple(route_identity),
        results=[completion_event_from_dict(event) for event in results],
    )


def serialize_batch_completion_payload(
    completion: BatchCompletion,
    completion_enqueued_at: float,
) -> bytes:
    """Serialize a worker-to-parent batch completion envelope."""
    envelope = {
        "kind": BATCH_COMPLETION_KIND,
        "completion": batch_completion_to_dict(completion),
        "timing": {"completion_enqueued_at": completion_enqueued_at},
    }
    return cast(bytes, msgpack.packb(envelope, use_bin_type=True))


def decode_batch_completion_payload(
    item: Any,
    max_bytes: int,
) -> dict[str, Any]:
    """Decode and validate a worker-to-parent batch completion envelope."""
    decoded = (
        _decode_msgpack_payload(item, max_bytes)
        if isinstance(item, (bytes, bytearray))
        else item
    )
    if not isinstance(decoded, dict) or decoded.get("kind") != BATCH_COMPLETION_KIND:
        raise ValueError("invalid_batch_completion_payload")
    return dict(decoded)


def serialize_batch_payload(
    batch: list[WorkItem] | RouteBatch,
    flush_enqueued_at: float,
) -> bytes:
    """Serialize batch payload for process payload serialization.

    Args:
        batch (list[WorkItem]): worker process로 보낼 작업 항목 묶음입니다.
        flush_enqueued_at (float): batch가 flush된 monotonic timestamp입니다.

    Returns:
        bytes: msgpack으로 인코딩된 batch envelope입니다.

    """
    return serialize_worker_pipe_payload(batch, flush_enqueued_at)


def serialize_worker_pipe_payload(
    payload: list[WorkItem] | RouteBatch,
    flush_enqueued_at: float,
) -> bytes:
    """Serialize a worker-pipe payload with an explicit payload kind."""
    timing = {"flush_enqueued_at": flush_enqueued_at}
    if isinstance(payload, RouteBatch):
        envelope: SerializedWorkerPipePayload = {
            "kind": WORKER_PIPE_KIND_ROUTE_BATCH,
            "batch": route_batch_to_dict(payload),
            "timing": timing,
        }
    else:
        envelope = {
            "kind": WORKER_PIPE_KIND_WORK_ITEMS,
            "items": [work_item_to_dict(item) for item in payload],
            "timing": timing,
        }
    return cast(bytes, msgpack.packb(envelope, use_bin_type=True))


def _decode_msgpack_payload(item: bytes | bytearray, max_bytes: int) -> Any:
    """Decode one msgpack payload while enforcing the configured byte limit."""
    if len(item) > max_bytes:
        raise ValueError("payload_too_large")
    unpacker = msgpack.Unpacker(raw=False, max_buffer_size=max_bytes)
    unpacker.feed(item)
    decoded_items = list(unpacker)
    if len(decoded_items) == 1:
        return decoded_items[0]
    return decoded_items


def decode_worker_pipe_payload(
    item: Any,
    max_bytes: int,
) -> SerializedWorkerPipePayload:
    """Decode a worker-pipe payload envelope and validate its payload kind."""
    decoded = (
        _decode_msgpack_payload(item, max_bytes)
        if isinstance(item, (bytes, bytearray))
        else item
    )
    if not isinstance(decoded, dict):
        return {
            "kind": WORKER_PIPE_KIND_WORK_ITEMS,
            "items": [dict(entry) for entry in decoded],
            "timing": {},
        }

    kind = decoded.get("kind")
    if kind is None:
        if "items" in decoded:
            payload = dict(decoded)
            payload["kind"] = WORKER_PIPE_KIND_WORK_ITEMS
            return payload
        return {
            "kind": WORKER_PIPE_KIND_WORK_ITEMS,
            "items": [dict(decoded)],
            "timing": {},
        }
    if kind not in {WORKER_PIPE_KIND_WORK_ITEMS, WORKER_PIPE_KIND_ROUTE_BATCH}:
        raise ValueError("unknown_worker_pipe_payload_kind:%s" % kind)
    return dict(decoded)


def normalize_decoded_payloads(
    decoded: Any,
) -> tuple[list[SerializedWorkItem], dict[str, Any]]:
    """Normalize decoded payloads for process payload serialization.

    Args:
        decoded (Any): msgpack에서 디코딩된 단건, 리스트, 또는 batch envelope입니다.

    Returns:
        tuple[list[SerializedWorkItem], dict[str, float]]: 정규화된 payload 목록과 timing metadata입니다.

    """
    if isinstance(decoded, dict):
        kind = decoded.get("kind")
        if kind == WORKER_PIPE_KIND_ROUTE_BATCH:
            batch = route_batch_from_dict(decoded["batch"])
            timing = decoded.get("timing", {})
            timing_values: dict[str, Any] = {
                key: float(value)
                for key, value in dict(timing).items()
                if isinstance(value, (int, float))
            }
            timing_values["route_batch_id"] = batch.batch_id
            timing_values["route_identity"] = tuple(batch.route_identity)
            return [work_item_to_dict(item) for item in batch.items], timing_values
        if kind is not None and kind != WORKER_PIPE_KIND_WORK_ITEMS:
            raise ValueError("unknown_worker_pipe_payload_kind:%s" % kind)
        if "items" in decoded:
            timing = decoded.get("timing", {})
            timing_values = {
                key: float(value)
                for key, value in dict(timing).items()
                if isinstance(value, (int, float))
            }
            return [dict(entry) for entry in decoded.get("items", [])], timing_values
        payload = dict(decoded)
        payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
        return [payload], {}

    if isinstance(decoded, list):
        payloads: list[SerializedWorkItem] = []
        for entry in decoded:
            if isinstance(entry, WorkItem):
                payload = work_item_to_dict(entry)
            else:
                payload = dict(entry)
            payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
            payloads.append(payload)
        return payloads, {}

    if isinstance(decoded, WorkItem):
        payload = work_item_to_dict(decoded)
    else:
        payload = dict(decoded)
    payload["requeue_attempts"] = payload.get("requeue_attempts", 0)
    return [payload], {}


def decode_incoming_payloads(
    item: Any, max_bytes: int
) -> tuple[list[SerializedWorkItem], dict[str, Any]]:
    """Decode incoming payloads for process payload serialization.

    Args:
        item (Any): worker가 받은 raw bytes 또는 이미 디코딩된 payload입니다.
        max_bytes (int): 허용할 최대 encoded payload 크기입니다.

    Returns:
        tuple[list[SerializedWorkItem], dict[str, float]]: 정규화된 payload 목록과 timing metadata입니다.

    Raises:
        ValueError: encoded payload가 max_bytes를 초과하면 발생합니다.

    """
    if isinstance(item, (bytes, bytearray)):
        decoded = _decode_msgpack_payload(item, max_bytes)
        return normalize_decoded_payloads(decoded)
    return normalize_decoded_payloads(item)


def decode_incoming_item(item: Any, max_bytes: int) -> list[WorkItem]:
    """Decode incoming item for process payload serialization.

    Args:
        item (Any): worker가 받은 raw bytes 또는 decoded payload입니다.
        max_bytes (int): 허용할 최대 encoded payload 크기입니다.

    Returns:
        list[WorkItem]: 복원된 작업 항목 목록입니다.

    """
    return [
        work_item_from_dict(payload)
        for payload in decode_incoming_payloads(item, max_bytes)[0]
    ]
