from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any, Literal

from pyrallel_consumer.config import BatchWorkerConfig
from pyrallel_consumer.dto import OrderingMode, WorkItem
from pyrallel_consumer.worker import BATCH_WORKER_ERROR_MAX_CHARS

RoutePolicy = Literal["key_hash", "partition", "unordered"]
WorkerKind = Literal["single", "batch"]


class CompletionFailureClass(str, Enum):
    WORKER_FAILURE = "WORKER_FAILURE"
    BATCH_WORKER_CONTRACT_ERROR = "BATCH_WORKER_CONTRACT_ERROR"


def _is_async_callable(callable_: Callable[..., Any]) -> bool:
    if inspect.iscoroutinefunction(callable_):
        return True
    call = getattr(callable_, "__call__", None)
    return inspect.iscoroutinefunction(call)


def _derive_route_policy(ordering_mode: OrderingMode) -> RoutePolicy:
    if ordering_mode == OrderingMode.KEY_HASH:
        return "key_hash"
    if ordering_mode == OrderingMode.PARTITION:
        return "partition"
    if ordering_mode == OrderingMode.UNORDERED:
        return "unordered"
    raise ValueError(f"unsupported ordering_mode: {ordering_mode}")


@dataclass(frozen=True)
class BatchWorkerRuntimeSpec:
    ordering_mode: OrderingMode
    batch_worker_config: BatchWorkerConfig | object
    route_policy: RoutePolicy
    max_retries: int
    bounded_error_max_chars: int = BATCH_WORKER_ERROR_MAX_CHARS
    invalid_result_reason: str = "invalid_batch_worker_result"
    worker_failure_class: str = CompletionFailureClass.WORKER_FAILURE.value
    contract_error_failure_class: (
        str
    ) = CompletionFailureClass.BATCH_WORKER_CONTRACT_ERROR.value
    ordered_prefix_blocked_reason: str = "ordered_prefix_blocked"

    def __post_init__(self) -> None:
        expected_route_policy = _derive_route_policy(self.ordering_mode)
        if self.route_policy != expected_route_policy:
            raise ValueError(
                "route_policy must match ordering_mode: "
                f"expected {expected_route_policy}, got {self.route_policy}"
            )

    @classmethod
    def from_config(
        cls,
        *,
        ordering_mode: OrderingMode,
        batch_worker_config: BatchWorkerConfig | object,
        max_retries: int,
    ) -> "BatchWorkerRuntimeSpec":
        return cls(
            ordering_mode=ordering_mode,
            batch_worker_config=batch_worker_config,
            route_policy=_derive_route_policy(ordering_mode),
            max_retries=max_retries,
        )


@dataclass(frozen=True)
class WorkerSpec:
    kind: WorkerKind
    callable: Callable[..., Any]
    is_async: bool
    batch_runtime: BatchWorkerRuntimeSpec | None = None

    @classmethod
    def single(cls, worker: Callable[[WorkItem], Any]) -> "WorkerSpec":
        return cls(
            kind="single",
            callable=worker,
            is_async=_is_async_callable(worker),
        )

    @classmethod
    def batch(
        cls,
        batch_worker: Callable[..., Any],
        batch_runtime: BatchWorkerRuntimeSpec,
    ) -> "WorkerSpec":
        return cls(
            kind="batch",
            callable=batch_worker,
            is_async=_is_async_callable(batch_worker),
            batch_runtime=batch_runtime,
        )
