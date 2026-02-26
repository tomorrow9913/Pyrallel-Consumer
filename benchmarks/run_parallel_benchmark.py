from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List

from confluent_kafka.admin import AdminClient

from benchmarks.baseline_consumer import consume_messages
from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups
from benchmarks.producer import produce_messages
from benchmarks.pyrallel_consumer_test import ExecutionMode, run_pyrallel_consumer_test
from benchmarks.stats import BenchmarkResult, BenchmarkStats, write_results_json
from pyrallel_consumer.dto import WorkItem

_YAPPI_WORKER_STARTED = False


def _check_kafka_connection(bootstrap_servers: str) -> None:
    client = AdminClient({"bootstrap.servers": bootstrap_servers})
    try:
        client.list_topics(timeout=5)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to connect to Kafka at {bootstrap_servers}: {exc}"
        ) from exc


def _run_baseline_round(
    *,
    topic_name: str,
    num_messages: int,
    bootstrap_servers: str,
    num_partitions: int,
    num_keys: int,
    group_id: str,
    worker_fn: Callable[[bytes], None],
    workload: str,
) -> BenchmarkResult:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name="baseline",
        run_type="baseline",
        workload=workload,
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


def _sleep_worker(payload: bytes, sleep_ms: float) -> None:
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def _sleep_worker_async(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def _sleep_worker_process(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


def _cpu_worker(payload: bytes, iterations: int) -> None:
    payload.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload).digest()


async def _cpu_worker_async(item: WorkItem, iterations: int) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()
    await asyncio.sleep(0)


def _cpu_worker_process(item: WorkItem, iterations: int) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()


def _io_worker(payload: bytes, sleep_ms: float) -> None:
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def _io_worker_async(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def _io_worker_process(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


def _select_workers(
    *,
    workload: str,
    sleep_ms: float,
    cpu_iterations: int,
    io_sleep_ms: float,
) -> tuple[
    Callable[[bytes], None],
    Callable[[WorkItem], Awaitable[None]],
    Callable[[WorkItem], None],
]:
    if workload == "sleep":
        return (
            partial(_sleep_worker, sleep_ms=sleep_ms),
            partial(_sleep_worker_async, sleep_ms=sleep_ms),
            partial(_sleep_worker_process, sleep_ms=sleep_ms),
        )
    if workload == "cpu":
        return (
            partial(_cpu_worker, iterations=cpu_iterations),
            partial(_cpu_worker_async, iterations=cpu_iterations),
            partial(_cpu_worker_process, iterations=cpu_iterations),
        )
    if workload == "io":
        return (
            partial(_io_worker, sleep_ms=io_sleep_ms),
            partial(_io_worker_async, sleep_ms=io_sleep_ms),
            partial(_io_worker_process, sleep_ms=io_sleep_ms),
        )
    raise ValueError(f"Unknown workload: {workload}")


@contextmanager
def _profile_session(
    *,
    enabled: bool,
    run_name: str,
    output_dir: Path,
    clock: str,
    profile_threads: bool,
    profile_greenlets: bool,
    top_n: int,
):
    if not enabled:
        yield
        return
    try:
        import yappi
    except ImportError as exc:  # noqa: BLE001
        raise RuntimeError("yappi is required for profiling; install dev deps") from exc

    output_dir.mkdir(parents=True, exist_ok=True)
    yappi.set_clock_type(clock)
    yappi.start(profile_threads=profile_threads, profile_greenlets=profile_greenlets)
    try:
        yield
    finally:
        yappi.stop()
        stats = yappi.get_func_stats()
        prof_path = output_dir / f"{run_name}.prof"
        stats.save(str(prof_path), type="pstat")
        print(f"\n[profile] saved to {prof_path}")
        if top_n > 0:
            print(f"\nTop {top_n} functions by total time [{run_name}]\n")
            stats.sort("ttot")
            top_stats: Any = stats[:top_n]
            print(_format_stats_table(top_stats, limit=top_n))
        yappi.clear_stats()


def _stop_yappi_worker(path: Path) -> None:
    try:
        import yappi

        yappi.stop()
        stats = yappi.get_func_stats()
        stats.save(str(path), type="pstat")
        yappi.clear_stats()
        print(f"[profile worker] saved to {path}")
    except Exception:  # noqa: BLE001
        # Worker teardown should not crash the process if profiling fails
        return


def _format_stats_table(stats: Iterable[Any], *, limit: int) -> str:
    rows: list[tuple[str, int, float, float]] = []
    for entry in list(stats)[:limit]:
        name = entry.full_name if hasattr(entry, "full_name") else str(entry)
        ncall = getattr(entry, "ncall", 0)
        ttot = getattr(entry, "ttot", 0.0)
        tavg = getattr(entry, "tavg", 0.0)
        rows.append((name, ncall, ttot, tavg))
    if not rows:
        return "(no stats)"
    col_widths = [
        max(len("Function"), max(len(r[0]) for r in rows)),
        max(len("Calls"), max(len(str(r[1])) for r in rows)),
        len("Total(s)"),
        len("Avg(s)"),
    ]
    header = ["Function", "Calls", "Total(s)", "Avg(s)"]
    lines = [
        " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(header)),
        "-+-".join("-" * col_widths[i] for i in range(len(header))),
    ]
    for name, calls, ttot, tavg in rows:
        lines.append(
            " | ".join(
                [
                    name.ljust(col_widths[0]),
                    str(calls).rjust(col_widths[1]),
                    f"{ttot:.6f}".rjust(col_widths[2]),
                    f"{tavg:.6f}".rjust(col_widths[3]),
                ]
            )
        )
    return "\n".join(lines)


def _summarize_worker_profiles(
    run_name: str, profile_dir: Path, top_n: int, clock: str
) -> None:
    try:
        import yappi
    except Exception:  # noqa: BLE001
        return

    paths = list(profile_dir.glob(f"{run_name}-worker-*.prof"))
    if not paths:
        return

    merged = yappi.YFuncStats()
    for path in paths:
        try:
            merged.add(str(path))
        except Exception:  # noqa: BLE001
            continue

    merged_path = profile_dir / f"{run_name}-workers-merged.prof"
    merged.save(str(merged_path), type="pstat")
    print(
        f"[profile workers] merged stats saved to {merged_path} ({len(paths)} workers)"
    )

    if top_n > 0:
        merged.sort("ttot")
        print(f"\nTop {top_n} functions by total time [{run_name} workers]\n")
        print(_format_stats_table(merged, limit=top_n))


def _wrap_process_worker_for_profile(
    worker_fn: Callable[[WorkItem], None],
    *,
    output_dir: Path,
    run_name: str,
    clock: str,
    profile_threads: bool,
    profile_greenlets: bool,
) -> Callable[[WorkItem], None]:
    # Worker profiling disabled: yappi is unstable in worker processes and emits internal errors.
    return worker_fn


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
) -> BenchmarkResult:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name=run_name,
        run_type=mode.value,
        workload=workload,
        topic=topic_name,
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
    )
    if timed_out:
        raise RuntimeError(
            f"Pyrallel consumer ({mode}) timed out before processing all messages"
        )
    if summary is None:
        summary = stats.summary()
    return summary


def _print_table(results: List[BenchmarkResult]) -> None:
    headers = ["Run", "Type", "Topic", "Messages", "TPS", "Avg ms", "P99 ms"]
    rows = [
        [
            result.run_name,
            result.run_type,
            result.topic,
            f"{result.messages_processed:,}",
            f"{result.throughput_tps:,.2f}",
            f"{result.avg_processing_ms:.3f}",
            f"{result.p99_processing_ms:.3f}",
        ]
        for result in results
    ]
    widths = [
        max(len(headers[i]), max(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    header_line = " | ".join(headers[i].ljust(widths[i]) for i in range(len(headers)))
    divider = "-+-".join("-" * widths[i] for i in range(len(headers)))
    print(header_line)
    print(divider)
    for row in rows:
        print(" | ".join(row[i].ljust(widths[i]) for i in range(len(headers))))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Pyrallel throughput benchmarks")
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--num-messages", type=int, default=100_000)
    parser.add_argument("--num-keys", type=int, default=100)
    parser.add_argument("--num-partitions", type=int, default=8)
    parser.add_argument("--topic-prefix", default="pyrallel-benchmark")
    parser.add_argument("--baseline-group", default="baseline-benchmark-group")
    parser.add_argument("--async-group", default="async-benchmark-group")
    parser.add_argument("--process-group", default="process-benchmark-group")
    parser.add_argument(
        "--json-output",
        default=None,
        help="Path to write JSON summary (default benchmarks/results/<timestamp>.json)",
    )
    parser.add_argument("--skip-baseline", action="store_true")
    parser.add_argument("--skip-async", action="store_true")
    parser.add_argument("--skip-process", action="store_true")
    parser.add_argument(
        "--skip-reset",
        action="store_true",
        help="Skip deleting/recreating topics and consumer groups before benchmarks",
    )
    parser.add_argument(
        "--timeout-sec",
        type=int,
        default=60,
        help="Timeout in seconds for each Pyrallel consumer run",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level for benchmark run (use WARNING for cleaner TPS measurements)",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable yappi profiling for each run",
    )
    parser.add_argument(
        "--profile-dir",
        default="benchmarks/results/profiles",
        help="Directory to write .prof files when profiling",
    )
    parser.add_argument(
        "--profile-clock",
        choices=["wall", "cpu"],
        default="wall",
        help="yappi clock type",
    )
    parser.add_argument(
        "--profile-top-n",
        type=int,
        default=0,
        help="Print top N functions by total time after each profiled run (0 to skip)",
    )
    parser.add_argument(
        "--profile-threads",
        action="store_true",
        help="Profile threads when profiling is enabled",
    )
    parser.add_argument(
        "--profile-greenlets",
        action="store_true",
        help="Profile greenlets/async tasks when profiling is enabled",
    )
    parser.add_argument(
        "--profile-process-workers",
        action="store_true",
        help="Also profile process workers (off by default; can emit yappi internal errors).",
    )
    parser.add_argument(
        "--workload",
        choices=["sleep", "cpu", "io", "all"],
        default="sleep",
        help="Workload type applied to all modes (use 'all' to run sleep, cpu, io sequentially)",
    )
    parser.add_argument(
        "--worker-sleep-ms",
        type=float,
        default=5.0,
        help="Sleep per message for sleep workload",
    )
    parser.add_argument(
        "--worker-cpu-iterations",
        type=int,
        default=1000,
        help="Iterations for CPU workload",
    )
    parser.add_argument(
        "--worker-io-sleep-ms",
        type=float,
        default=5.0,
        help="Sleep per message for IO workload (simulated IO wait)",
    )
    args = parser.parse_args()

    log_level = getattr(logging, args.log_level, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("pyrallel_consumer").setLevel(log_level)
    logging.getLogger("benchmarks").setLevel(log_level)

    _check_kafka_connection(args.bootstrap_servers)

    workloads = ["sleep", "cpu", "io"] if args.workload == "all" else [args.workload]
    profile_dir = Path(args.profile_dir)

    results: List[BenchmarkResult] = []

    for workload in workloads:
        suffix = f"-{workload}" if args.workload == "all" else ""
        topic_configs: dict[str, TopicConfig] = {}
        consumer_groups: list[str] = []
        if not args.skip_baseline:
            topic_configs[f"{args.topic_prefix}{suffix}-baseline"] = TopicConfig(
                num_partitions=args.num_partitions
            )
            consumer_groups.append(f"{args.baseline_group}{suffix}")
        if not args.skip_async:
            topic_configs[f"{args.topic_prefix}{suffix}-async"] = TopicConfig(
                num_partitions=args.num_partitions
            )
            consumer_groups.append(f"{args.async_group}{suffix}")
        if not args.skip_process:
            topic_configs[f"{args.topic_prefix}{suffix}-process"] = TopicConfig(
                num_partitions=args.num_partitions
            )
            consumer_groups.append(f"{args.process_group}{suffix}")

        if not topic_configs:
            raise RuntimeError("All benchmark runs are skipped; nothing to execute")

        if not args.skip_reset:
            unique_groups = list(dict.fromkeys(consumer_groups))
            print(
                "Resetting benchmark topics/groups: %s | groups=%s"
                % (", ".join(topic_configs.keys()), ", ".join(unique_groups))
            )
            reset_topics_and_groups(
                bootstrap_servers=args.bootstrap_servers,
                topics=topic_configs,
                consumer_groups=unique_groups,
            )

        baseline_worker, async_worker_fn, process_worker_fn = _select_workers(
            workload=workload,
            sleep_ms=args.worker_sleep_ms,
            cpu_iterations=args.worker_cpu_iterations,
            io_sleep_ms=args.worker_io_sleep_ms,
        )

        workload_prefix = f"{workload}-" if args.workload == "all" else ""

        if not args.skip_baseline:
            topic_name = f"{args.topic_prefix}{suffix}-baseline"
            run_name = f"{workload_prefix}baseline"
            with _profile_session(
                enabled=args.profile,
                run_name=run_name,
                output_dir=profile_dir,
                clock=args.profile_clock,
                profile_threads=args.profile_threads,
                profile_greenlets=args.profile_greenlets,
                top_n=args.profile_top_n,
            ):
                results.append(
                    _run_baseline_round(
                        topic_name=topic_name,
                        num_messages=args.num_messages,
                        bootstrap_servers=args.bootstrap_servers,
                        num_partitions=args.num_partitions,
                        num_keys=args.num_keys,
                        group_id=f"{args.baseline_group}{suffix}",
                        worker_fn=baseline_worker,
                        workload=workload,
                    )
                )

        async def run_async_rounds() -> List[BenchmarkResult]:
            async_results: List[BenchmarkResult] = []
            if not args.skip_async:
                topic_name = f"{args.topic_prefix}{suffix}-async"
                run_name = f"{workload_prefix}pyrallel-async"
                with _profile_session(
                    enabled=args.profile,
                    run_name=run_name,
                    output_dir=profile_dir,
                    clock=args.profile_clock,
                    profile_threads=args.profile_threads,
                    profile_greenlets=args.profile_greenlets,
                    top_n=args.profile_top_n,
                ):
                    async_results.append(
                        await _run_pyrparallel_round(
                            topic_name=topic_name,
                            run_name=run_name,
                            mode=ExecutionMode.ASYNC,
                            num_messages=args.num_messages,
                            bootstrap_servers=args.bootstrap_servers,
                            num_partitions=args.num_partitions,
                            num_keys=args.num_keys,
                            group_id=f"{args.async_group}{suffix}",
                            timeout_sec=args.timeout_sec,
                            async_worker_fn=async_worker_fn,
                            process_worker_fn=process_worker_fn,
                            workload=workload,
                        )
                    )
            if not args.skip_process:
                topic_name = f"{args.topic_prefix}{suffix}-process"
                run_name = f"{workload_prefix}pyrallel-process"
                prof_process_worker = process_worker_fn
                if args.profile and args.profile_process_workers:
                    prof_process_worker = _wrap_process_worker_for_profile(
                        process_worker_fn,
                        output_dir=profile_dir,
                        run_name=run_name,
                        clock=args.profile_clock,
                        profile_threads=args.profile_threads,
                        profile_greenlets=args.profile_greenlets,
                    )
                # Skip profiling for process mode to avoid yappi instability.
                async_results.append(
                    await _run_pyrparallel_round(
                        topic_name=topic_name,
                        run_name=run_name,
                        mode=ExecutionMode.PROCESS,
                        num_messages=args.num_messages,
                        bootstrap_servers=args.bootstrap_servers,
                        num_partitions=args.num_partitions,
                        num_keys=args.num_keys,
                        group_id=f"{args.process_group}{suffix}",
                        timeout_sec=args.timeout_sec,
                        async_worker_fn=async_worker_fn,
                        process_worker_fn=prof_process_worker,
                        workload=workload,
                    )
                )
                if args.profile and args.profile_process_workers:
                    _summarize_worker_profiles(
                        run_name,
                        profile_dir=profile_dir,
                        top_n=args.profile_top_n,
                        clock=args.profile_clock,
                    )
            return async_results

        results.extend(asyncio.run(run_async_rounds()))

    _print_table(results)
    output_path = args.json_output
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = f"benchmarks/results/{timestamp}.json"
    options = {k: v for k, v in vars(args).items()}
    write_results_json(results, Path(output_path), options=options)
    print(f"\nJSON summary written to {output_path}")


if __name__ == "__main__":
    main()
