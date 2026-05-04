from __future__ import annotations

import pickle
from collections.abc import Awaitable, Callable
from typing import cast

from pyrallel_consumer.dto import WorkItem

from .base import (
    BenchmarkWorkload,
    WorkloadContext,
    WorkloadOptionMetadata,
    WorkloadOptionSchema,
    build_workload_options,
    describe_workload_options,
)
from .cpu import cpu_worker, cpu_worker_async, cpu_worker_process
from .io import io_worker, io_worker_async, io_worker_process
from .registry import (
    WorkloadRecord,
    WorkloadRegistry,
    discover_workloads,
    discover_workloads_from,
)
from .sleep import sleep_worker, sleep_worker_async, sleep_worker_process

_REGISTRY_CACHE: WorkloadRegistry | None = None


def _registry() -> WorkloadRegistry:
    """Handle  registry for benchmark workload discovery."""
    global _REGISTRY_CACHE
    if _REGISTRY_CACHE is None:
        _REGISTRY_CACHE = discover_workloads()
    return _REGISTRY_CACHE


def reset_registry_cache() -> None:
    """Clear the workload registry cache for tests or explicit rediscovery."""
    global _REGISTRY_CACHE
    _REGISTRY_CACHE = None


def all_records() -> tuple[WorkloadRecord, ...]:
    """Handle all records for benchmark workload discovery."""
    return _registry().all_records()


def records() -> tuple[WorkloadRecord, ...]:
    """Handle records for benchmark workload discovery."""
    return _registry().records()


def available_records() -> tuple[WorkloadRecord, ...]:
    """Handle available records for benchmark workload discovery."""
    return _registry().available_records()


def available_names() -> tuple[str, ...]:
    """Handle available names for benchmark workload discovery."""
    return _registry().available_names()


def get_available(name: str) -> type[BenchmarkWorkload]:
    """Handle get available for benchmark workload discovery."""
    return _registry().get_available(name)


def select_workers(
    *,
    workload: str,
    sleep_ms: float | None = None,
    cpu_iterations: int | None = None,
    io_sleep_ms: float | None = None,
    workload_options: dict[str, dict[str, object]] | None = None,
    validate_process_worker: bool = True,
) -> tuple[
    Callable[[bytes], None],
    Callable[[WorkItem], Awaitable[None]],
    Callable[[WorkItem], None],
]:
    """Handle select workers for benchmark workload discovery."""
    workload_cls = get_available(workload)
    options = build_workload_options(
        workload_cls,
        legacy_values={
            "sleep.sleep_ms": sleep_ms,
            "cpu.iterations": cpu_iterations,
            "io.sleep_ms": io_sleep_ms,
        },
        workload_options=workload_options,
    )
    context = WorkloadContext(options=options)
    workload_instance = workload_cls()
    return _validated_worker_tuple(
        workload,
        (
            ("baseline_worker", workload_instance.baseline_worker(context)),
            ("async_worker", workload_instance.async_worker(context)),
            ("process_worker", workload_instance.process_worker(context)),
        ),
        validate_process_worker=validate_process_worker,
    )


def _validated_worker_tuple(
    workload: str,
    workers: tuple[
        tuple[str, object],
        tuple[str, object],
        tuple[str, object],
    ],
    *,
    validate_process_worker: bool = True,
) -> tuple[
    Callable[[bytes], None],
    Callable[[WorkItem], Awaitable[None]],
    Callable[[WorkItem], None],
]:
    """Handle  validated worker tuple for benchmark workload discovery."""
    for method_name, worker in workers:
        if not callable(worker):
            raise ValueError(
                f"Workload {workload!r} {method_name} returned a non-callable worker"
            )
    if validate_process_worker:
        process_worker = workers[2][1]
        try:
            pickle.dumps(process_worker)
        except Exception as exc:  # noqa: BLE001 - expose contract failure clearly.
            raise ValueError(
                f"Workload {workload!r} process_worker returned a non-picklable "
                f"worker: {exc.__class__.__name__}: {exc}"
            ) from exc

    baseline_worker, async_worker, process_worker = (worker for _, worker in workers)
    return (
        cast(Callable[[bytes], None], baseline_worker),
        cast(Callable[[WorkItem], Awaitable[None]], async_worker),
        cast(Callable[[WorkItem], None], process_worker),
    )


__all__ = [
    "BenchmarkWorkload",
    "WorkloadContext",
    "WorkloadOptionMetadata",
    "WorkloadOptionSchema",
    "WorkloadRecord",
    "WorkloadRegistry",
    "all_records",
    "available_names",
    "available_records",
    "build_workload_options",
    "cpu_worker",
    "cpu_worker_async",
    "cpu_worker_process",
    "discover_workloads",
    "discover_workloads_from",
    "describe_workload_options",
    "get_available",
    "io_worker",
    "io_worker_async",
    "io_worker_process",
    "records",
    "reset_registry_cache",
    "select_workers",
    "sleep_worker",
    "sleep_worker_async",
    "sleep_worker_process",
]
