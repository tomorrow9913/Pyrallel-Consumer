from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, List, Sequence

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

from confluent_kafka.admin import AdminClient

from benchmarks.baseline_consumer import consume_messages
from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups
from benchmarks.producer import produce_messages
from benchmarks.pyrallel_consumer_test import ExecutionMode, run_pyrallel_consumer_test
from benchmarks.stats import BenchmarkResult, BenchmarkStats, write_results_json
from pyrallel_consumer.dto import OrderingMode, WorkItem

_YAPPI_WORKER_STARTED = False
_WORKLOAD_CHOICES = ("sleep", "cpu", "io")
_ORDER_CHOICES = tuple(mode.value for mode in OrderingMode)
_STRICT_COMPLETION_MONITOR_CHOICES = ("on", "off")


def _parse_csv_selection(
    value: str, *, argument_name: str, choices: Sequence[str]
) -> list[str]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in value.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if item not in choices:
            choices_str = ", ".join(choices)
            raise argparse.ArgumentTypeError(
                "%s must contain only %s (got %r)" % (argument_name, choices_str, item)
            )
        if item in seen:
            continue
        seen.add(item)
        items.append(item)
    if not items:
        raise argparse.ArgumentTypeError(
            "%s must contain at least one value" % argument_name
        )
    return items


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
    ordering: str = "key_hash",
    ensure_topic_exists: bool = True,
    strict_completion_monitor_enabled: bool = True,
    process_batch_size: int | None = None,
    process_max_batch_wait_ms: int | None = None,
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
        run_type=mode.value,
        workload=workload,
        ordering=ordering,
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
        ordering_mode=ordering,
        ensure_topic_exists=ensure_topic_exists,
        strict_completion_monitor_enabled=strict_completion_monitor_enabled,
        process_batch_size=process_batch_size,
        process_max_batch_wait_ms=process_max_batch_wait_ms,
    )
    if timed_out:
        raise RuntimeError(
            f"Pyrallel consumer ({mode}) timed out before processing all messages"
        )
    if summary is None:
        summary = stats.summary()
    return summary


def _print_table(results: List[BenchmarkResult]) -> None:
    headers = ["Run", "Type", "Order", "Topic", "Messages", "TPS", "Avg ms", "P99 ms"]
    rows = [
        [
            result.run_name,
            result.run_type,
            result.ordering,
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


_PYSPY_FORMAT_EXTENSIONS: dict[str, str] = {
    "flamegraph": ".svg",
    "speedscope": ".json",
    "chrometrace": ".json",
    "raw": ".txt",
}


def _relaunch_with_pyspy(args: argparse.Namespace) -> int:
    """Re-execute the benchmark script under py-spy.

    Builds a ``py-spy record`` (or ``py-spy top``) command that wraps the
    current interpreter re-running this module with ``--_pyspy-child`` so the
    child process skips the relaunch and runs the actual benchmark.  py-spy's
    ``--subprocesses`` flag captures worker processes spawned by the
    ``ProcessExecutionEngine``.
    """
    py_spy_bin = shutil.which("py-spy")
    if py_spy_bin is None:
        raise RuntimeError(
            "py-spy not found on PATH. Install it via: uv add --dev py-spy"
        )

    output_dir = Path(args.py_spy_output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # -- build the child command (strip py-spy flags, add sentinel) --
    child_argv: list[str] = [sys.executable, "-m", "benchmarks.run_parallel_benchmark"]
    skip_next = False
    for arg in args._raw_argv:
        if skip_next:
            skip_next = False
            continue
        # strip all --py-spy* flags
        if (
            arg == "--py-spy"
            or arg == "--py-spy-native"
            or arg == "--py-spy-idle"
            or arg == "--py-spy-top"
        ):
            continue
        if arg.startswith("--py-spy-format"):
            if "=" not in arg:
                skip_next = True
            continue
        if arg.startswith("--py-spy-output"):
            if "=" not in arg:
                skip_next = True
            continue
        if arg.startswith("--py-spy-rate"):
            if "=" not in arg:
                skip_next = True
            continue
        child_argv.append(arg)
    child_argv.append("--_pyspy-child")

    # -- build the py-spy command --
    if args.py_spy_top:
        # top mode: interactive live view
        cmd: list[str] = [py_spy_bin, "top", "--subprocesses"]
        cmd.extend(["--rate", str(args.py_spy_rate)])
        if args.py_spy_native:
            cmd.append("--native")
        if args.py_spy_idle:
            cmd.append("--idle")
        cmd.append("--")
        cmd.extend(child_argv)
        print(f"[py-spy top] {' '.join(cmd)}")
        result = subprocess.run(cmd)
        return result.returncode

    # record mode: write profile to file
    fmt = args.py_spy_format
    ext = _PYSPY_FORMAT_EXTENSIONS.get(fmt, ".svg")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_file = output_dir / f"pyspy-{fmt}-{timestamp}{ext}"

    cmd = [
        py_spy_bin,
        "record",
        "--subprocesses",
        "--format",
        fmt,
        "--output",
        str(output_file),
        "--rate",
        str(args.py_spy_rate),
    ]
    if args.py_spy_native:
        cmd.append("--native")
    if args.py_spy_idle:
        cmd.append("--idle")
    cmd.append("--")
    cmd.extend(child_argv)

    print(f"[py-spy record] {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode == 0:
        print(f"\n[py-spy] profile saved to {output_file}")
    else:
        print(f"\n[py-spy] py-spy exited with code {result.returncode}")
    return result.returncode


def build_parser() -> argparse.ArgumentParser:
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
        "--workloads",
        type=lambda value: _parse_csv_selection(
            value,
            argument_name="--workloads",
            choices=_WORKLOAD_CHOICES,
        ),
        default=["sleep"],
        help="Comma-separated workloads to run (choices: sleep,cpu,io)",
    )
    parser.add_argument(
        "--order",
        type=lambda value: _parse_csv_selection(
            value,
            argument_name="--order",
            choices=_ORDER_CHOICES,
        ),
        default=["key_hash"],
        help="Comma-separated ordering modes to run (choices: key_hash,partition,unordered)",
    )
    parser.add_argument(
        "--strict-completion-monitor",
        type=lambda value: _parse_csv_selection(
            value,
            argument_name="--strict-completion-monitor",
            choices=_STRICT_COMPLETION_MONITOR_CHOICES,
        ),
        default=["on"],
        help="Comma-separated strict completion monitor modes to run (choices: on,off)",
    )
    parser.add_argument(
        "--worker-sleep-ms",
        type=float,
        default=0.5,
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
        default=0.5,
        help="Sleep per message for IO workload (simulated IO wait)",
    )
    parser.add_argument(
        "--process-batch-size",
        type=int,
        default=None,
        help="Override process-mode micro-batch size for benchmark runs",
    )
    parser.add_argument(
        "--process-max-batch-wait-ms",
        type=int,
        default=None,
        help="Override process-mode micro-batch wait in milliseconds for benchmark runs",
    )
    # -- py-spy profiling options (process mode) --
    parser.add_argument(
        "--py-spy",
        action="store_true",
        help="Enable py-spy profiling for process mode (wraps the benchmark via self-relaunch)",
    )
    parser.add_argument(
        "--py-spy-format",
        choices=["flamegraph", "speedscope", "raw", "chrometrace"],
        default="flamegraph",
        help="py-spy output format (default: flamegraph)",
    )
    parser.add_argument(
        "--py-spy-output",
        default="benchmarks/results/pyspy",
        help="Directory to write py-spy output files (default: benchmarks/results/pyspy)",
    )
    parser.add_argument(
        "--py-spy-rate",
        type=int,
        default=100,
        help="py-spy sampling rate in Hz (default: 100)",
    )
    parser.add_argument(
        "--py-spy-native",
        action="store_true",
        help="Include native C extension frames in py-spy output",
    )
    parser.add_argument(
        "--py-spy-idle",
        action="store_true",
        help="Include idle thread stacks in py-spy output",
    )
    parser.add_argument(
        "--py-spy-top",
        action="store_true",
        help="Use py-spy top (live view) instead of record",
    )
    parser.add_argument(
        "--_pyspy-child",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,  # internal: marks this as the child process under py-spy
    )
    return parser


def _reset_run_targets(
    *,
    bootstrap_servers: str,
    topic_name: str,
    group_id: str,
    num_partitions: int,
) -> None:
    print("Resetting benchmark topics/groups: %s | groups=%s" % (topic_name, group_id))
    reset_topics_and_groups(
        bootstrap_servers=bootstrap_servers,
        topics={topic_name: TopicConfig(num_partitions=num_partitions)},
        consumer_groups=[group_id],
    )


def launch_tui() -> None:
    from benchmarks.tui.app import BenchmarkTuiApp

    BenchmarkTuiApp().run()


def run_benchmark(
    args: argparse.Namespace, raw_argv: Sequence[str] | None = None
) -> None:
    args._raw_argv = list(raw_argv or [])

    # -- py-spy self-relaunch gate --
    # When --py-spy is requested and we are NOT already the child process,
    # re-execute ourselves under py-spy and exit with its return code.
    if args.py_spy and not args._pyspy_child:
        raise SystemExit(_relaunch_with_pyspy(args))

    log_level = getattr(logging, args.log_level, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    logging.getLogger("pyrallel_consumer").setLevel(log_level)
    logging.getLogger("benchmarks").setLevel(log_level)

    _check_kafka_connection(args.bootstrap_servers)

    workloads = list(args.workloads)
    orderings = list(args.order)
    strict_monitor_modes = list(args.strict_completion_monitor)
    profile_dir = Path(args.profile_dir)

    results: List[BenchmarkResult] = []

    has_runs = not (args.skip_baseline and args.skip_async and args.skip_process)
    if not has_runs:
        raise RuntimeError("All benchmark runs are skipped; nothing to execute")

    for workload in workloads:
        baseline_worker, async_worker_fn, process_worker_fn = _select_workers(
            workload=workload,
            sleep_ms=args.worker_sleep_ms,
            cpu_iterations=args.worker_cpu_iterations,
            io_sleep_ms=args.worker_io_sleep_ms,
        )

        for ordering in orderings:
            suffix = "-%s-%s" % (workload, ordering)
            run_prefix = "%s-%s" % (workload, ordering)

            if not args.skip_baseline:
                topic_name = f"{args.topic_prefix}{suffix}-baseline"
                run_name = f"{run_prefix}-baseline"
                group_id = f"{args.baseline_group}{suffix}"
                with _profile_session(
                    enabled=args.profile,
                    run_name=run_name,
                    output_dir=profile_dir,
                    clock=args.profile_clock,
                    profile_threads=args.profile_threads,
                    profile_greenlets=args.profile_greenlets,
                    top_n=args.profile_top_n,
                ):
                    if not args.skip_reset:
                        _reset_run_targets(
                            bootstrap_servers=args.bootstrap_servers,
                            topic_name=topic_name,
                            group_id=group_id,
                            num_partitions=args.num_partitions,
                        )
                    results.append(
                        _run_baseline_round(
                            run_name=run_name,
                            topic_name=topic_name,
                            num_messages=args.num_messages,
                            bootstrap_servers=args.bootstrap_servers,
                            num_partitions=args.num_partitions,
                            num_keys=args.num_keys,
                            group_id=group_id,
                            worker_fn=baseline_worker,
                            workload=workload,
                            ordering=ordering,
                            ensure_topic_exists=args.skip_reset,
                        )
                    )

            async def run_async_rounds() -> List[BenchmarkResult]:
                async_results: List[BenchmarkResult] = []
                for strict_monitor_mode in strict_monitor_modes:
                    strict_completion_monitor_enabled = strict_monitor_mode == "on"
                    strict_suffix = ""
                    if len(strict_monitor_modes) > 1 or strict_monitor_mode != "on":
                        strict_suffix = "-strict-%s" % strict_monitor_mode

                    if not args.skip_async:
                        topic_name = f"{args.topic_prefix}{suffix}-async{strict_suffix}"
                        run_name = f"{run_prefix}-pyrallel-async{strict_suffix}"
                        group_id = f"{args.async_group}{suffix}{strict_suffix}"
                        if not args.skip_reset:
                            _reset_run_targets(
                                bootstrap_servers=args.bootstrap_servers,
                                topic_name=topic_name,
                                group_id=group_id,
                                num_partitions=args.num_partitions,
                            )
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
                                    group_id=group_id,
                                    timeout_sec=args.timeout_sec,
                                    async_worker_fn=async_worker_fn,
                                    process_worker_fn=process_worker_fn,
                                    workload=workload,
                                    ordering=ordering,
                                    ensure_topic_exists=args.skip_reset,
                                    strict_completion_monitor_enabled=(
                                        strict_completion_monitor_enabled
                                    ),
                                    process_batch_size=args.process_batch_size,
                                    process_max_batch_wait_ms=(
                                        args.process_max_batch_wait_ms
                                    ),
                                )
                            )
                    if not args.skip_process:
                        topic_name = (
                            f"{args.topic_prefix}{suffix}-process{strict_suffix}"
                        )
                        run_name = f"{run_prefix}-pyrallel-process{strict_suffix}"
                        group_id = f"{args.process_group}{suffix}{strict_suffix}"
                        if not args.skip_reset:
                            _reset_run_targets(
                                bootstrap_servers=args.bootstrap_servers,
                                topic_name=topic_name,
                                group_id=group_id,
                                num_partitions=args.num_partitions,
                            )
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
                        async_results.append(
                            await _run_pyrparallel_round(
                                topic_name=topic_name,
                                run_name=run_name,
                                mode=ExecutionMode.PROCESS,
                                num_messages=args.num_messages,
                                bootstrap_servers=args.bootstrap_servers,
                                num_partitions=args.num_partitions,
                                num_keys=args.num_keys,
                                group_id=group_id,
                                timeout_sec=args.timeout_sec,
                                async_worker_fn=async_worker_fn,
                                process_worker_fn=prof_process_worker,
                                workload=workload,
                                ordering=ordering,
                                ensure_topic_exists=args.skip_reset,
                                strict_completion_monitor_enabled=(
                                    strict_completion_monitor_enabled
                                ),
                                process_batch_size=args.process_batch_size,
                                process_max_batch_wait_ms=(
                                    args.process_max_batch_wait_ms
                                ),
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


def main(argv: Sequence[str] | None = None) -> None:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        launch_tui()
        return

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    run_benchmark(args, raw_argv=raw_argv)


if __name__ == "__main__":
    main()
