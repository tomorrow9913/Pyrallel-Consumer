import pytest

from pyrallel_consumer.dto import TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.process_engine import (
    _calculate_backoff,
    _decode_incoming_item,
    _work_item_to_dict,
)


def test_decode_incoming_item_raises_on_oversize():
    # Given: inputs for `decode incoming item raises on oversize` are prepared.
    # When: the process engine helper code path is exercised.
    # Then: the expected `decode incoming item raises on oversize` behavior is asserted.
    with pytest.raises(ValueError, match="payload_too_large"):
        _decode_incoming_item(b"012345", max_bytes=2)


def test_decode_incoming_item_decodes_msgpack_batch():
    # Given: inputs for `decode incoming item decodes msgpack batch` are prepared.
    import msgpack

    items = [
        _work_item_to_dict(
            WorkItem(
                id="wi-1",
                tp=TopicPartition("demo", 0),
                offset=1,
                epoch=0,
                key=b"k1",
                payload=b"p1",
            )
        ),
        _work_item_to_dict(
            WorkItem(
                id="wi-2",
                tp=TopicPartition("demo", 1),
                offset=2,
                epoch=0,
                key=b"k2",
                payload=b"p2",
            )
        ),
    ]

    packed = msgpack.packb(items, use_bin_type=True)
    decoded = _decode_incoming_item(packed, max_bytes=1024)

    # When: the process engine helper code path is exercised.
    offsets = [(item.tp.partition, item.offset) for item in decoded]
    # Then: the expected `decode incoming item decodes msgpack batch` behavior is asserted.
    assert offsets == [(0, 1), (1, 2)]


def test_calculate_backoff_with_exponential_and_jitter(monkeypatch):
    # Given: inputs for `calculate backoff with exponential and jitter` are prepared.
    monkeypatch.setattr("random.randint", lambda a, b: b)

    delay = _calculate_backoff(
        attempt=2,
        retry_backoff_ms=100,
        exponential_backoff=True,
        max_retry_backoff_ms=500,
        retry_jitter_ms=50,
    )

    # When: the process engine helper code path is exercised.
    # Then: the expected `calculate backoff with exponential and jitter` behavior is asserted.
    assert delay == pytest.approx((200 + 50) / 1000.0)


def test_work_item_roundtrip_preserves_requeue_attempts():
    # Given: inputs for `work item roundtrip preserves requeue attempts` are prepared.
    import msgpack

    payload = {
        "id": "wi-1",
        "topic": "demo",
        "partition": 0,
        "offset": 1,
        "epoch": 0,
        "key": b"k1",
        "payload": b"p1",
        "requeue_attempts": 3,
    }

    packed = msgpack.packb([payload], use_bin_type=True)
    # When: the process engine helper code path is exercised.
    decoded = _decode_incoming_item(packed, max_bytes=1024)

    # Then: the expected `work item roundtrip preserves requeue attempts` behavior is asserted.
    assert decoded[0].requeue_attempts == 3
    assert _work_item_to_dict(decoded[0])["requeue_attempts"] == 3
