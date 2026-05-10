from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal, TypeAlias

from pyrallel_consumer.dto import WorkItem

BATCH_WORKER_ERROR_MAX_CHARS = 4096

BatchItemStatus: TypeAlias = Literal[
    "success",
    "failure",
    "ordered_prefix_blocked",
]


@dataclass(frozen=True)
class BatchItemOutcome:
    """Item-level outcome returned by a public batch worker."""

    status: BatchItemStatus
    error: str | None = None

    @staticmethod
    def success() -> "BatchItemOutcome":
        """Return a successful item outcome."""
        return BatchItemOutcome(status="success")

    @staticmethod
    def failure(error: str | None = None) -> "BatchItemOutcome":
        """Return a failed item outcome with an optional bounded reason."""
        return BatchItemOutcome(status="failure", error=error)

    @staticmethod
    def ordered_prefix_blocked() -> "BatchItemOutcome":
        """Return an ordered-tail item outcome that has not started yet."""
        return BatchItemOutcome(status="ordered_prefix_blocked")


BatchWorkerResult: TypeAlias = Mapping[str, BatchItemOutcome] | None
AsyncBatchWorker: TypeAlias = Callable[
    [Sequence[WorkItem]], Awaitable[BatchWorkerResult]
]
SyncBatchWorker: TypeAlias = Callable[[Sequence[WorkItem]], BatchWorkerResult]


def _bound_batch_worker_error_reason(reason: str) -> str:
    """Clamp batch-worker error text before it reaches events or logs."""
    if len(reason) <= BATCH_WORKER_ERROR_MAX_CHARS:
        return reason
    return reason[:BATCH_WORKER_ERROR_MAX_CHARS]


class BatchWorkerContractError(RuntimeError):
    """Fatal public batch-worker contract violation surfaced by the runtime."""

    code: Literal["invalid_batch_worker_result"] = "invalid_batch_worker_result"
    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = _bound_batch_worker_error_reason(reason)
        super().__init__(self.reason)
