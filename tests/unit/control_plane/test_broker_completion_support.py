from __future__ import annotations

import time
from collections import OrderedDict
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


@pytest.mark.asyncio
async def test_handle_blocking_timeouts_forces_failure_and_polls_completion() -> None:
    from pyrallel_consumer.control_plane.broker_completion_support import (
        BrokerCompletionSupport,
    )

    tp = DtoTopicPartition(topic="demo", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=500,
    )
    tracker.update_last_fetched_offset(0)
    tracker.get_gaps()
    tracker._blocking_offset_timestamps[0] = time.time() - 1.0

    kafka_config = KafkaConfig()
    kafka_config.parallel_consumer.execution.max_retries = 3
    work_manager = MagicMock()
    forced_event = CompletionEvent(
        id="forced",
        tp=tp,
        offset=0,
        epoch=0,
        status=CompletionStatus.FAILURE,
        error="forced",
        attempt=3,
    )
    work_manager.force_fail = AsyncMock(return_value=True)
    work_manager.poll_completed_events = AsyncMock(return_value=[forced_event])

    support = BrokerCompletionSupport(
        kafka_config=kafka_config,
        work_manager=work_manager,
        offset_trackers={tp: tracker},
        message_cache=OrderedDict(),
        should_cache_message_payloads=lambda: False,
        pop_cached_message=lambda _cache_key: None,
        publish_to_dlq=AsyncMock(),
        logger=MagicMock(),
    )

    timeout_events = await support.handle_blocking_timeouts(max_blocking_duration_ms=1)

    assert timeout_events == [forced_event]
    work_manager.force_fail.assert_awaited_once()
    work_manager.poll_completed_events.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_process_completed_events_marks_complete_and_clears_cache() -> None:
    from pyrallel_consumer.control_plane.broker_completion_support import (
        BrokerCompletionSupport,
    )

    tp = DtoTopicPartition(topic="demo", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    message_cache = OrderedDict({(tp, 0): (b"k", b"v")})
    popped_cache_keys: list[tuple[DtoTopicPartition, int]] = []

    support = BrokerCompletionSupport(
        kafka_config=KafkaConfig(),
        work_manager=MagicMock(),
        offset_trackers={tp: tracker},
        message_cache=message_cache,
        should_cache_message_payloads=lambda: False,
        pop_cached_message=lambda cache_key: popped_cache_keys.append(cache_key),
        publish_to_dlq=AsyncMock(return_value=True),
        logger=MagicMock(),
    )

    await support.process_completed_events(
        [
            CompletionEvent(
                id="done",
                tp=tp,
                offset=0,
                epoch=tracker.get_current_epoch(),
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            )
        ]
    )

    assert 0 in tracker.completed_offsets
    assert popped_cache_keys == [(tp, 0)]


@pytest.mark.asyncio
async def test_process_completed_events_falls_back_to_metadata_only_dlq() -> None:
    from pyrallel_consumer.control_plane.broker_completion_support import (
        BrokerCompletionSupport,
    )

    tp = DtoTopicPartition(topic="demo", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=100,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    kafka_config = KafkaConfig()
    kafka_config.dlq_enabled = True
    kafka_config.parallel_consumer.execution.max_retries = 3
    publish_to_dlq = AsyncMock(return_value=True)

    support = BrokerCompletionSupport(
        kafka_config=kafka_config,
        work_manager=MagicMock(),
        offset_trackers={tp: tracker},
        message_cache=OrderedDict(),
        should_cache_message_payloads=lambda: True,
        pop_cached_message=lambda _cache_key: None,
        publish_to_dlq=publish_to_dlq,
        logger=MagicMock(),
    )

    await support.process_completed_events(
        [
            CompletionEvent(
                id="failed",
                tp=tp,
                offset=100,
                epoch=tracker.get_current_epoch(),
                status=CompletionStatus.FAILURE,
                error="boom",
                attempt=3,
            )
        ]
    )

    publish_to_dlq.assert_awaited_once_with(
        tp=tp,
        offset=100,
        epoch=tracker.get_current_epoch(),
        key=None,
        value=None,
        error="boom",
        attempt=3,
    )


@pytest.mark.asyncio
async def test_process_completed_events_retries_pending_dlq_failure_and_marks_complete() -> (
    None
):
    from pyrallel_consumer.control_plane.broker_completion_support import (
        BrokerCompletionSupport,
    )

    tp = DtoTopicPartition(topic="demo", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=100,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    message_cache = OrderedDict({(tp, 100): (b"key", b"value")})
    popped_cache_keys: list[tuple[DtoTopicPartition, int]] = []
    kafka_config = KafkaConfig()
    kafka_config.dlq_enabled = True
    kafka_config.parallel_consumer.execution.max_retries = 3
    publish_to_dlq = AsyncMock(side_effect=[False, True])

    support = BrokerCompletionSupport(
        kafka_config=kafka_config,
        work_manager=MagicMock(),
        offset_trackers={tp: tracker},
        message_cache=message_cache,
        should_cache_message_payloads=lambda: True,
        pop_cached_message=lambda cache_key: popped_cache_keys.append(cache_key),
        publish_to_dlq=publish_to_dlq,
        logger=MagicMock(),
    )

    failed_event = CompletionEvent(
        id="failed-work-id",
        tp=tp,
        offset=100,
        epoch=tracker.get_current_epoch(),
        status=CompletionStatus.FAILURE,
        error="boom",
        attempt=3,
    )

    await support.process_completed_events([failed_event])

    assert 100 not in tracker.completed_offsets
    assert popped_cache_keys == []
    assert support._pending_dlq_events[(tp, 100)].id == "failed-work-id"

    await support.process_completed_events([])

    assert 100 in tracker.completed_offsets
    assert popped_cache_keys == [(tp, 100)]
    assert publish_to_dlq.await_count == 2
    second_call = publish_to_dlq.await_args_list[1].kwargs
    assert second_call == {
        "tp": tp,
        "offset": 100,
        "epoch": tracker.get_current_epoch(),
        "key": b"key",
        "value": b"value",
        "error": "boom",
        "attempt": 3,
    }
