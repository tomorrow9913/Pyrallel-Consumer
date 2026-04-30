from __future__ import annotations

from typing import Any, Optional

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.dto import (
    WORK_ITEM_POISON_KEY_UNSET,
    CompletionEvent,
    CompletionStatus,
    TopicPartition,
    WorkItem,
)

SerializedWorkItem = dict[str, Any]
SerializedCompletionEvent = dict[str, Any]
SerializedRegistryEvent = dict[str, Any]
SerializedBatchEnvelope = dict[str, Any]


def work_item_to_dict(item: WorkItem) -> SerializedWorkItem:
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
    payload: SerializedCompletionEvent = {
        "id": event.id,
        "topic": event.tp.topic,
        "partition": event.tp.partition,
        "offset": event.offset,
        "epoch": event.epoch,
        "status": event.status.value,
        "error": event.error,
        "attempt": event.attempt,
    }
    if extra_fields:
        payload.update(extra_fields)
    return payload


def completion_event_from_dict(
    payload: SerializedCompletionEvent,
) -> CompletionEvent:
    return CompletionEvent(
        id=payload["id"],
        tp=TopicPartition(payload["topic"], payload["partition"]),
        offset=payload["offset"],
        epoch=payload["epoch"],
        status=CompletionStatus(payload["status"]),
        error=payload.get("error"),
        attempt=payload["attempt"],
    )


def serialize_batch_payload(batch: list[WorkItem], flush_enqueued_at: float) -> bytes:
    envelope: SerializedBatchEnvelope = {
        "items": [work_item_to_dict(item) for item in batch],
        "timing": {"flush_enqueued_at": flush_enqueued_at},
    }
    return msgpack.packb(envelope, use_bin_type=True)


def normalize_decoded_payloads(
    decoded: Any,
) -> tuple[list[SerializedWorkItem], dict[str, float]]:
    if isinstance(decoded, dict):
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
) -> tuple[list[SerializedWorkItem], dict[str, float]]:
    if isinstance(item, (bytes, bytearray)):
        if len(item) > max_bytes:
            raise ValueError("payload_too_large")
        unpacker = msgpack.Unpacker(raw=False, max_buffer_size=max_bytes)
        unpacker.feed(item)
        decoded_items = list(unpacker)
        if len(decoded_items) == 1:
            decoded = decoded_items[0]
        else:
            decoded = decoded_items
        return normalize_decoded_payloads(decoded)
    return normalize_decoded_payloads(item)


def decode_incoming_item(item: Any, max_bytes: int) -> list[WorkItem]:
    return [
        work_item_from_dict(payload)
        for payload in decode_incoming_payloads(item, max_bytes)[0]
    ]
