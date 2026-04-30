from __future__ import annotations

from typing import Awaitable, Callable, Literal

from benchmarks.baseline_consumer import consume_messages
from benchmarks.producer import produce_messages
from benchmarks.pyrallel_consumer_test import (
    ExecutionMode,
    ProcessFlushPolicy,
    run_pyrallel_consumer_test,
)
from benchmarks.stats import BenchmarkResult, BenchmarkStats
from pyrallel_consumer.dto import WorkItem

ProcessTransportMode = Literal["shared_queue", "worker_pipes"]


def _run_baseline_round(
    *,
    run_name: str,
    topic_name: str,
    num_messages: int,
    bootstrap_servers: str,
    num_partitions: int,
    num_keys: int,
    group_id: str,
    worker_fn: Callable[[bytes], None],
    workload: str,
    ordering: str = "key_hash",
    ensure_topic_exists: bool = True,
) -> BenchmarkResult:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
        ensure_topic_exists=ensure_topic_exists,
    )
    stats = BenchmarkStats(
        run_name=run_name,
        run_type="baseline",
        workload=workload,
        ordering=ordering,
        topic=topic_name,
        target_messages=num_messages,
    )
    result = consume_messages(
        num_messages_to_process=num_messages,
        bootstrap_servers=bootstrap_servers,
        topic_name=topic_name,
        group_id=group_id,
        stats=stats,
        worker_fn=worker_fn,
    )
    if result is None:
        result = stats.summary()
    return result


async def _run_pyrparallel_round(
    *,
    topic_name: str,
    run_name: str,
    mode: ExecutionMode,
    num_messages: int,
    bootstrap_servers: str,
    num_partitions: int,
    num_keys: int,
    group_id: str,
    timeout_sec: int,
    async_worker_fn: Callable[[WorkItem], Awaitable[None]],
    process_worker_fn: Callable[[WorkItem], None],
    workload: str,
    ordering: str = "key_hash",
    ensure_topic_exists: bool = True,
    strict_completion_monitor_enabled: bool = True,
    process_count: int | None = None,
    process_batch_size: int | None = None,
    process_max_batch_wait_ms: int | None = None,
    process_flush_policy: ProcessFlushPolicy | None = None,
    process_demand_flush_min_residence_ms: int | None = None,
    process_transport_mode: ProcessTransportMode | None = None,
    metrics_port: int | None = None,
    adaptive_concurrency_enabled: bool = False,
) -> BenchmarkResult:
    effective_process_transport_mode = (
        process_transport_mode if mode == ExecutionMode.PROCESS else None
    )
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
        ensure_topic_exists=ensure_topic_exists,
    )
    stats = BenchmarkStats(
        run_name=run_name,
        run_type=mode.value,
        workload=workload,
        ordering=ordering,
        topic=topic_name,
        process_transport_mode=effective_process_transport_mode,
        target_messages=num_messages,
    )
    timed_out, _, summary = await run_pyrallel_consumer_test(
        num_messages=num_messages,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
        consumer_group=group_id,
        execution_mode=mode.value,
        num_partitions=num_partitions,
        stats_tracker=stats,
        timeout_sec=timeout_sec,
        async_worker_fn=async_worker_fn,
        process_worker_fn=process_worker_fn,
        ordering_mode=ordering,
        ensure_topic_exists=ensure_topic_exists,
        strict_completion_monitor_enabled=strict_completion_monitor_enabled,
        process_count=process_count if mode == ExecutionMode.PROCESS else None,
        process_batch_size=process_batch_size,
        process_max_batch_wait_ms=process_max_batch_wait_ms,
        process_flush_policy=process_flush_policy,
        process_demand_flush_min_residence_ms=(process_demand_flush_min_residence_ms),
        process_transport_mode=effective_process_transport_mode,
        metrics_port=metrics_port,
        adaptive_concurrency_enabled=adaptive_concurrency_enabled,
    )
    if timed_out:
        raise RuntimeError(
            f"Pyrallel consumer ({mode}) timed out before processing all messages"
        )
    if summary is None:
        summary = stats.summary()
    return summary
