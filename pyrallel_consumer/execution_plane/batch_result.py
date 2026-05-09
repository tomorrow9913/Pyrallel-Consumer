from collections.abc import Mapping, Sequence

from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    OrderingMode,
    WorkItem,
)
from pyrallel_consumer.worker import (
    BatchItemOutcome,
    BatchWorkerContractError,
    _bound_batch_worker_error_reason,
)

_DEFAULT_BATCH_ITEM_FAILURE = "batch_worker_item_failed"


def _contract_error(reason: str) -> BatchWorkerContractError:
    return BatchWorkerContractError(f"invalid_batch_worker_result:{reason}")


def _validate_unique_pending_ids(pending_items: Sequence[WorkItem]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for item in pending_items:
        if item.id in seen:
            duplicates.add(item.id)
        seen.add(item.id)
    if duplicates:
        raise _contract_error("duplicate_work_item_ids")


def _completion_event(
    item: WorkItem,
    *,
    status: CompletionStatus,
    error: str | None,
    attempt: int,
) -> CompletionEvent:
    bounded_error = (
        _bound_batch_worker_error_reason(error) if error is not None else None
    )
    return CompletionEvent(
        id=item.id,
        tp=item.tp,
        offset=item.offset,
        epoch=item.epoch,
        status=status,
        error=bounded_error,
        attempt=attempt,
    )


def _validate_mapping_keys(
    pending_items: Sequence[WorkItem], result: Mapping[str, BatchItemOutcome]
) -> None:
    expected_ids = {item.id for item in pending_items}
    actual_ids = set(result.keys())
    missing_ids = expected_ids - actual_ids
    unknown_ids = actual_ids - expected_ids
    if missing_ids:
        raise _contract_error("missing_item_ids")
    if unknown_ids:
        raise _contract_error("unknown_item_ids")


def _validate_outcome(outcome: object) -> BatchItemOutcome:
    if not isinstance(outcome, BatchItemOutcome):
        raise _contract_error("invalid_outcome_shape")
    if outcome.status == "success":
        if outcome.error:
            raise _contract_error("success_with_error")
        return outcome
    if outcome.status == "failure":
        return outcome
    if outcome.status == "ordered_prefix_blocked":
        if outcome.error:
            raise _contract_error("ordered_prefix_blocked_with_error")
        return outcome
    raise _contract_error("unknown_status")


def ordered_prefix_blocked_tail_items(
    *,
    pending_items: Sequence[WorkItem],
    result: Mapping[str, BatchItemOutcome] | None,
) -> list[WorkItem]:
    """Return ordered batch-worker tail items explicitly reported as not started."""
    if result is None or not isinstance(result, Mapping):
        return []
    saw_failure = False
    tail_items: list[WorkItem] = []
    for item in pending_items:
        outcome = result.get(item.id)
        if outcome is None:
            continue
        if saw_failure:
            if outcome.status == "ordered_prefix_blocked":
                tail_items.append(item)
            continue
        if outcome.status == "failure":
            saw_failure = True
    return tail_items


def normalize_batch_worker_result(
    *,
    pending_items: Sequence[WorkItem],
    result: Mapping[str, BatchItemOutcome] | None,
    ordering_mode: OrderingMode,
    attempt: int,
) -> list[CompletionEvent]:
    _validate_unique_pending_ids(pending_items)
    if result is None:
        return [
            _completion_event(
                item,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=attempt,
            )
            for item in pending_items
        ]
    if not isinstance(result, Mapping):
        raise _contract_error("invalid_result_shape")

    _validate_mapping_keys(pending_items, result)
    if ordering_mode == OrderingMode.UNORDERED:
        events: list[CompletionEvent] = []
        for item in pending_items:
            outcome = _validate_outcome(result[item.id])
            if outcome.status == "ordered_prefix_blocked":
                raise _contract_error("ordered_prefix_blocked_unordered")
            if outcome.status == "success":
                events.append(
                    _completion_event(
                        item,
                        status=CompletionStatus.SUCCESS,
                        error=None,
                        attempt=attempt,
                    )
                )
            else:
                events.append(
                    _completion_event(
                        item,
                        status=CompletionStatus.FAILURE,
                        error=outcome.error or _DEFAULT_BATCH_ITEM_FAILURE,
                        attempt=attempt,
                    )
                )
        return events

    events = []
    saw_failure = False
    for item in pending_items:
        outcome = _validate_outcome(result[item.id])
        if not saw_failure:
            if outcome.status == "ordered_prefix_blocked":
                raise _contract_error("ordered_prefix_violation")
            if outcome.status == "success":
                events.append(
                    _completion_event(
                        item,
                        status=CompletionStatus.SUCCESS,
                        error=None,
                        attempt=attempt,
                    )
                )
                continue
            saw_failure = True
            events.append(
                _completion_event(
                    item,
                    status=CompletionStatus.FAILURE,
                    error=outcome.error or _DEFAULT_BATCH_ITEM_FAILURE,
                    attempt=attempt,
                )
            )
            continue
        if outcome.status != "ordered_prefix_blocked":
            raise _contract_error("ordered_prefix_violation")
    return events
