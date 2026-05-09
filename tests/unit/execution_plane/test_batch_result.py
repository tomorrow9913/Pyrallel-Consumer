import pytest

from pyrallel_consumer.dto import (
    CompletionStatus,
    OrderingMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.batch_result import normalize_batch_worker_result
from pyrallel_consumer.worker import (
    BATCH_WORKER_ERROR_MAX_CHARS,
    BatchItemOutcome,
    BatchWorkerContractError,
)


def _item(item_id: str, offset: int) -> WorkItem:
    return WorkItem(
        id=item_id,
        tp=TopicPartition(topic="orders", partition=0),
        offset=offset,
        epoch=7,
        key="key",
        payload={"offset": offset},
    )


def test_batch_result_none_marks_every_pending_item_successful() -> None:
    # Given: a batch worker returns None for two pending items.
    items = [_item("a", 10), _item("b", 11)]

    events = normalize_batch_worker_result(
        pending_items=items,
        result=None,
        ordering_mode=OrderingMode.UNORDERED,
        attempt=1,
    )

    # Then: each pending item becomes one item-level success completion.
    assert [event.id for event in events] == ["a", "b"]
    assert [event.status for event in events] == [
        CompletionStatus.SUCCESS,
        CompletionStatus.SUCCESS,
    ]
    assert [event.attempt for event in events] == [1, 1]


def test_batch_result_unordered_explicit_mapping_emits_success_and_failure_events() -> (
    None
):
    # Given: unordered explicit outcomes include one success and one failure.
    items = [_item("a", 10), _item("b", 11)]

    events = normalize_batch_worker_result(
        pending_items=items,
        result={
            "a": BatchItemOutcome.success(),
            "b": BatchItemOutcome.failure("sink rejected"),
        },
        ordering_mode=OrderingMode.UNORDERED,
        attempt=2,
    )

    # Then: both outcomes are normalized into item-level completion events.
    assert [(event.id, event.status, event.error) for event in events] == [
        ("a", CompletionStatus.SUCCESS, None),
        ("b", CompletionStatus.FAILURE, "sink rejected"),
    ]
    assert [event.attempt for event in events] == [2, 2]


def test_batch_result_rejects_missing_unknown_and_duplicate_item_ids() -> None:
    # Given: pending items and invalid result/id shapes.
    items = [_item("a", 10), _item("b", 11)]

    # Then: missing/unknown IDs and duplicate pending IDs are fatal contract errors.
    with pytest.raises(BatchWorkerContractError, match="missing_item_ids"):
        normalize_batch_worker_result(
            pending_items=items,
            result={"a": BatchItemOutcome.success()},
            ordering_mode=OrderingMode.UNORDERED,
            attempt=1,
        )
    with pytest.raises(BatchWorkerContractError, match="unknown_item_ids"):
        normalize_batch_worker_result(
            pending_items=items,
            result={
                "a": BatchItemOutcome.success(),
                "b": BatchItemOutcome.success(),
                "c": BatchItemOutcome.success(),
            },
            ordering_mode=OrderingMode.UNORDERED,
            attempt=1,
        )
    with pytest.raises(BatchWorkerContractError, match="duplicate_work_item_ids"):
        normalize_batch_worker_result(
            pending_items=[_item("a", 10), _item("a", 11)],
            result=None,
            ordering_mode=OrderingMode.UNORDERED,
            attempt=1,
        )


def test_batch_result_ordered_prefix_requires_blocked_tail_after_first_failure() -> (
    None
):
    # Given: ordered pending items where the second item fails and the tail is blocked.
    items = [_item("a", 10), _item("b", 11), _item("c", 12)]

    events = normalize_batch_worker_result(
        pending_items=items,
        result={
            "a": BatchItemOutcome.success(),
            "b": BatchItemOutcome.failure("sink rejected"),
            "c": BatchItemOutcome.ordered_prefix_blocked(),
        },
        ordering_mode=OrderingMode.KEY_HASH,
        attempt=1,
    )

    # Then: only the successful prefix and first failure produce settling events.
    assert [(event.id, event.status) for event in events] == [
        ("a", CompletionStatus.SUCCESS),
        ("b", CompletionStatus.FAILURE),
    ]

    # And: a later success after failure is rejected instead of weakening ordering.
    with pytest.raises(BatchWorkerContractError, match="ordered_prefix_violation"):
        normalize_batch_worker_result(
            pending_items=items,
            result={
                "a": BatchItemOutcome.success(),
                "b": BatchItemOutcome.failure("sink rejected"),
                "c": BatchItemOutcome.success(),
            },
            ordering_mode=OrderingMode.KEY_HASH,
            attempt=1,
        )


def test_batch_result_bounds_large_item_failure_errors() -> None:
    # Given: a public batch worker returns an oversized item-level failure reason.
    oversized_error = "worker failure: " + ("x" * (BATCH_WORKER_ERROR_MAX_CHARS + 128))
    items = [_item("a", 10)]

    events = normalize_batch_worker_result(
        pending_items=items,
        result={"a": BatchItemOutcome.failure(oversized_error)},
        ordering_mode=OrderingMode.UNORDERED,
        attempt=1,
    )

    # Then: normalized completion errors remain bounded before crossing runtime queues.
    assert events[0].error is not None
    assert len(events[0].error) == BATCH_WORKER_ERROR_MAX_CHARS
    assert events[0].error == oversized_error[:BATCH_WORKER_ERROR_MAX_CHARS]
