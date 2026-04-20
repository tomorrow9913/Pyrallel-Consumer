from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.dto import OrderingMode
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def _make_message(
    *,
    topic: str = "test-topic",
    partition: int = 0,
    offset: int | None = 0,
    key: bytes | None = b"key-a",
    value: bytes | None = b"payload",
    error: object | None = None,
):
    message = MagicMock()
    message.topic.return_value = topic
    message.partition.return_value = partition
    message.offset.return_value = offset
    message.key.return_value = key
    message.value.return_value = value
    message.error.return_value = error
    return message


def _make_tracker(epoch: int = 1):
    tracker = MagicMock()
    tracker.get_current_epoch.return_value = epoch
    tracker.last_committed_offset = 0
    tracker.completed_offsets = []
    tracker.get_committable_high_water_mark.side_effect = (
        lambda min_inflight_offset=None: (
            min(
                max(tracker.completed_offsets),
                min_inflight_offset - 1,
            )
            if tracker.completed_offsets and min_inflight_offset is not None
            else (
                max(tracker.completed_offsets)
                if tracker.completed_offsets
                else tracker.last_committed_offset
            )
        )
    )
    return tracker


@pytest.mark.asyncio
async def test_dispatch_messages_groups_ordered_messages_and_uses_bulk_submit() -> None:
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = _make_tracker(epoch=3)
    cache_message_for_dlq = MagicMock()
    submit_message = AsyncMock()
    submit_grouped_messages = AsyncMock()

    support = BrokerDispatchSupport(
        ordering_mode=OrderingMode.KEY_HASH,
        offset_trackers={tp: tracker},
        cache_message_for_dlq=cache_message_for_dlq,
        submit_message=submit_message,
        submit_grouped_messages=submit_grouped_messages,
        get_min_inflight_offset=lambda _tp: None,
        logger=MagicMock(),
    )

    await support.dispatch_messages(
        [
            _make_message(offset=0, key=b"key-a", value=b"payload-a"),
            _make_message(offset=1, key=b"key-b", value=b"payload-b"),
        ]
    )

    submit_message.assert_not_awaited()
    submit_grouped_messages.assert_awaited_once()
    assert submit_grouped_messages.await_args is not None
    grouped_messages = submit_grouped_messages.await_args.args[0]
    assert grouped_messages == {
        (tp, b"key-a"): [(0, 3, b"payload-a", b"key-a")],
        (tp, b"key-b"): [(1, 3, b"payload-b", b"key-b")],
    }
    assert cache_message_for_dlq.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_messages_unordered_submits_directly_and_skips_invalid_messages() -> (
    None
):
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = _make_tracker(epoch=2)
    submit_message = AsyncMock()
    submit_grouped_messages = AsyncMock()
    logger = MagicMock()

    support = BrokerDispatchSupport(
        ordering_mode=OrderingMode.UNORDERED,
        offset_trackers={tp: tracker},
        cache_message_for_dlq=MagicMock(),
        submit_message=submit_message,
        submit_grouped_messages=submit_grouped_messages,
        get_min_inflight_offset=lambda _tp: None,
        logger=logger,
    )

    await support.dispatch_messages(
        [
            _make_message(error=RuntimeError("boom")),
            _make_message(topic=None),  # type: ignore[arg-type]
            _make_message(topic="other-topic"),
            _make_message(offset=None),
            _make_message(offset=5, key=b"direct-key", value=b"direct-payload"),
        ]
    )

    submit_grouped_messages.assert_not_awaited()
    submit_message.assert_awaited_once_with(
        tp=tp,
        offset=5,
        epoch=2,
        key=b"direct-key",
        payload=b"direct-payload",
    )
    assert logger.warning.call_count >= 2


def test_build_commit_candidates_clamps_safe_offset_by_min_inflight() -> None:
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = _make_tracker()
    tracker.last_committed_offset = 0
    tracker.completed_offsets = [1, 2, 3, 4, 5, 6]

    support = BrokerDispatchSupport(
        ordering_mode=OrderingMode.KEY_HASH,
        offset_trackers={tp: tracker},
        cache_message_for_dlq=MagicMock(),
        submit_message=AsyncMock(),
        submit_grouped_messages=AsyncMock(),
        get_min_inflight_offset=lambda _tp: 5,
        logger=MagicMock(),
    )

    commits_to_make = support.build_commit_candidates()

    assert commits_to_make == [(tp, 4)]
