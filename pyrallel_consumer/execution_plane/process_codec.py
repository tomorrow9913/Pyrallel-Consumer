# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_codec.py
# Role: Serializes and deserializes WorkItem, CompletionEvent, registry, and batch IPC payloads.
# Extend here for wire-format changes shared by process transports and workers.
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
    )


def serialize_batch_payload(batch: list[WorkItem], flush_enqueued_at: float) -> bytes:
    """Serialize batch payload for process payload serialization.

    Args:
        batch (list[WorkItem]): worker process로 보낼 작업 항목 묶음입니다.
        flush_enqueued_at (float): batch가 flush된 monotonic timestamp입니다.

    Returns:
        bytes: msgpack으로 인코딩된 batch envelope입니다.

    """
    envelope: SerializedBatchEnvelope = {
        "items": [work_item_to_dict(item) for item in batch],
        "timing": {"flush_enqueued_at": flush_enqueued_at},
    }
    return msgpack.packb(envelope, use_bin_type=True)


def normalize_decoded_payloads(
    decoded: Any,
) -> tuple[list[SerializedWorkItem], dict[str, float]]:
    """Normalize decoded payloads for process payload serialization.

    Args:
        decoded (Any): msgpack에서 디코딩된 단건, 리스트, 또는 batch envelope입니다.

    Returns:
        tuple[list[SerializedWorkItem], dict[str, float]]: 정규화된 payload 목록과 timing metadata입니다.

    """
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
