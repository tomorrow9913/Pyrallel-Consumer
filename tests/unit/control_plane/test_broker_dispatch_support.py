from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
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


def _make_restored_tracker(tp: DtoTopicPartition) -> OffsetTracker:
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=4,
        max_revoke_grace_ms=0,
        initial_completed_offsets={4, 6, 7},
    )
    tracker.rehydrate_assignment_state(
        last_committed_offset=3,
        last_fetched_offset=7,
    )
    tracker.increment_epoch()
    return tracker


@pytest.mark.asyncio
async def test_dispatch_messages_groups_ordered_messages_and_uses_bulk_submit() -> None:
    # Given: inputs for `dispatch messages groups ordered messages and...` are prepared.
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
    # When: the broker dispatch support code path is exercised.
    submit_grouped_messages.assert_awaited_once()
    # Then: the expected `dispatch messages groups ordered messages and...` behavior is asserted.
    assert submit_grouped_messages.await_args is not None
    grouped_messages = submit_grouped_messages.await_args.args[0]
    assert grouped_messages == {
        (tp, b"key-a"): [(0, 3, b"payload-a", b"key-a")],
        (tp, b"key-b"): [(1, 3, b"payload-b", b"key-b")],
    }
    assert cache_message_for_dlq.call_count == 2


@pytest.mark.asyncio
async def test_dispatch_messages_partition_mode_groups_by_partition_not_key() -> None:
    # Given: inputs for `dispatch messages partition mode groups by pa...` are prepared.
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp_0 = DtoTopicPartition(topic="test-topic", partition=0)
    tp_1 = DtoTopicPartition(topic="test-topic", partition=1)
    tracker_0 = _make_tracker(epoch=4)
    tracker_1 = _make_tracker(epoch=5)
    submit_message = AsyncMock()
    submit_grouped_messages = AsyncMock()

    support = BrokerDispatchSupport(
        ordering_mode=OrderingMode.PARTITION,
        offset_trackers={tp_0: tracker_0, tp_1: tracker_1},
        cache_message_for_dlq=MagicMock(),
        submit_message=submit_message,
        submit_grouped_messages=submit_grouped_messages,
        get_min_inflight_offset=lambda _tp: None,
        logger=MagicMock(),
    )

    await support.dispatch_messages(
        [
            _make_message(partition=0, offset=0, key=b"key-a", value=b"payload-a"),
            _make_message(partition=0, offset=1, key=b"key-b", value=b"payload-b"),
            _make_message(partition=1, offset=0, key=b"key-a", value=b"payload-c"),
        ]
    )

    submit_message.assert_not_awaited()
    # When: the broker dispatch support code path is exercised.
    submit_grouped_messages.assert_awaited_once()
    # Then: the expected `dispatch messages partition mode groups by pa...` behavior is asserted.
    assert submit_grouped_messages.await_args is not None
    grouped_messages = submit_grouped_messages.await_args.args[0]
    assert grouped_messages == {
        (tp_0, 0): [
            (0, 4, b"payload-a", b"key-a"),
            (1, 4, b"payload-b", b"key-b"),
        ],
        (tp_1, 1): [(0, 5, b"payload-c", b"key-a")],
    }


@pytest.mark.asyncio
async def test_dispatch_messages_unordered_submits_directly_and_skips_invalid_messages() -> (
    None
):
    # Given: inputs for `dispatch messages unordered submits directly...` are prepared.
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
    # When: the broker dispatch support code path is exercised.
    submit_message.assert_awaited_once_with(
        tp=tp,
        offset=5,
        epoch=2,
        key=b"direct-key",
        payload=b"direct-payload",
    )
    # Then: the expected `dispatch messages unordered submits directly...` behavior is asserted.
    assert logger.warning.call_count >= 2


def test_build_commit_candidates_clamps_safe_offset_by_min_inflight() -> None:
    # Given: inputs for `build commit candidates clamps safe offset by...` are prepared.
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

    # When: the broker dispatch support code path is exercised.
    commits_to_make = support.build_commit_candidates()

    # Then: the expected `build commit candidates clamps safe offset by...` behavior is asserted.
    assert commits_to_make == [(tp, 4)]


@pytest.mark.parametrize(
    "ordering_mode",
    [OrderingMode.UNORDERED, OrderingMode.KEY_HASH, OrderingMode.PARTITION],
)
@pytest.mark.asyncio
async def test_dispatch_messages_skips_restored_completed_uncommitted_offsets(
    ordering_mode: OrderingMode,
) -> None:
    # Given: inputs for `dispatch messages skips restored completed un...` are prepared.
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = _make_restored_tracker(tp)
    cache_message_for_dlq = MagicMock()
    submit_message = AsyncMock()
    submit_grouped_messages = AsyncMock()
    skipped_offsets: list[tuple[DtoTopicPartition, int]] = []

    support = BrokerDispatchSupport(
        ordering_mode=ordering_mode,
        offset_trackers={tp: tracker},
        cache_message_for_dlq=cache_message_for_dlq,
        submit_message=submit_message,
        submit_grouped_messages=submit_grouped_messages,
        get_min_inflight_offset=lambda _tp: None,
        record_completed_offset_skip=lambda skipped_tp,
        skipped_offset: skipped_offsets.append((skipped_tp, skipped_offset)),
        logger=MagicMock(),
    )

    # When: the broker dispatch support code path is exercised.
    await support.dispatch_messages(
        [_make_message(offset=offset, key=b"key-a") for offset in (4, 5, 6, 7)]
    )

    # Then: the expected `dispatch messages skips restored completed un...` behavior is asserted.
    assert skipped_offsets == [(tp, 4), (tp, 6), (tp, 7)]
    cache_message_for_dlq.assert_called_once_with(
        tp=tp,
        offset=5,
        key=b"key-a",
        value=b"payload",
    )
    if ordering_mode == OrderingMode.UNORDERED:
        submit_message.assert_awaited_once_with(
            tp=tp,
            offset=5,
            epoch=1,
            key=b"key-a",
            payload=b"payload",
        )
        submit_grouped_messages.assert_not_awaited()
    else:
        submit_message.assert_not_awaited()
        submit_grouped_messages.assert_awaited_once()
        assert submit_grouped_messages.await_args is not None
        grouped_messages = submit_grouped_messages.await_args.args[0]
        submit_key = (
            tp.partition if ordering_mode == OrderingMode.PARTITION else b"key-a"
        )
        assert grouped_messages == {(tp, submit_key): [(5, 1, b"payload", b"key-a")]}


@pytest.mark.asyncio
async def test_dispatch_messages_skips_when_skip_callback_raises() -> None:
    # Given: inputs for `dispatch messages skips when skip callback ra...` are prepared.
    # When: the broker dispatch support code path is exercised.
    from pyrallel_consumer.control_plane.broker_dispatch_support import (
        BrokerDispatchSupport,
    )

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = _make_restored_tracker(tp)
    cache_message_for_dlq = MagicMock()
    submit_message = AsyncMock()
    submit_grouped_messages = AsyncMock()
    logger = MagicMock()

    def raise_on_skip(_tp: DtoTopicPartition, _offset: int) -> None:
        raise RuntimeError("skip metric failed")

    support = BrokerDispatchSupport(
        ordering_mode=OrderingMode.UNORDERED,
        offset_trackers={tp: tracker},
        cache_message_for_dlq=cache_message_for_dlq,
        submit_message=submit_message,
        submit_grouped_messages=submit_grouped_messages,
        get_min_inflight_offset=lambda _tp: None,
        record_completed_offset_skip=raise_on_skip,
        logger=logger,
    )

    await support.dispatch_messages([_make_message(offset=4), _make_message(offset=5)])

    logger.exception.assert_called_once_with(
        "Completed-offset skip diagnostic callback failed for %s@%d",
        tp,
        4,
    )
    cache_message_for_dlq.assert_called_once_with(
        tp=tp,
        offset=5,
        key=b"key-a",
        value=b"payload",
    )
    # Then: the expected `dispatch messages skips when skip callback ra...` behavior is asserted.
    submit_message.assert_awaited_once_with(
        tp=tp,
        offset=5,
        epoch=1,
        key=b"key-a",
        payload=b"payload",
    )
