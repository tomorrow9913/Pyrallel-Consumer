"""Yappi profiler for Pyrallel-Consumer benchmarks.

Profiles baseline, async, and process consumer modes independently,
saves pstats-compatible .prof files for snakeviz visualisation, and
prints a top-N summary to stdout.

Usage examples:
    # Profile all three modes (default 10 000 messages each):
    uv run python -m benchmarks.profile_benchmark_yappi

    # Profile only async mode with 50 000 messages:
    uv run python -m benchmarks.profile_benchmark_yappi --modes async --num-messages 50000

    # Open a profile in snakeviz:
    uv run snakeviz benchmarks/results/profiles/async_profile.prof
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys
import time
from datetime import datetime, timezone
from functools import partial
from typing import Awaitable, Callable, List, Sequence

import yappi

from benchmarks.baseline_consumer import consume_messages
from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups
from benchmarks.producer import produce_messages
from benchmarks.pyrallel_consumer_test import ExecutionMode, run_pyrallel_consumer_test
from benchmarks.stats import BenchmarkStats
from pyrallel_consumer.dto import WorkItem


def _sleep_work_payload(payload: bytes, sleep_ms: float) -> None:
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def _sleep_work_async_item(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def _sleep_work_process_item(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

VALID_MODES = ("baseline", "async", "process")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Profile Pyrallel-Consumer benchmarks with yappi"
    )
    parser.add_argument(
        "--modes",
        nargs="+",
        choices=VALID_MODES,
        default=list(VALID_MODES),
        help="Which consumer modes to profile (default: all three)",
    )
    parser.add_argument("--bootstrap-servers", default="localhost:9092")
    parser.add_argument("--num-messages", type=int, default=10_000)
    parser.add_argument("--num-keys", type=int, default=100)
    parser.add_argument("--num-partitions", type=int, default=8)
    parser.add_argument("--topic-prefix", default="pyrallel-prof")
    parser.add_argument("--timeout-sec", type=int, default=120)
    parser.add_argument(
        "--worker-sleep-ms",
        type=float,
        default=5.0,
        help="Sleep per message to simulate work (ms). Applied to all modes.",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=30,
        help="Number of top functions to print per mode",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory to save .prof files (default: benchmarks/results/profiles/<timestamp>/)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
    )
    return parser.parse_args(argv)


def _ensure_output_dir(output_dir: str | None) -> pathlib.Path:
    if output_dir:
        out = pathlib.Path(output_dir)
    else:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = pathlib.Path("benchmarks/results/profiles") / timestamp
    out.mkdir(parents=True, exist_ok=True)
    return out


def _reset_topics(
    bootstrap_servers: str,
    topic_names: List[str],
    num_partitions: int,
    consumer_groups: List[str],
) -> None:
    topic_configs = {
        name: TopicConfig(num_partitions=num_partitions) for name in topic_names
    }
    reset_topics_and_groups(
        bootstrap_servers=bootstrap_servers,
        topics=topic_configs,
        consumer_groups=consumer_groups,
    )


def _profile_baseline(
    *,
    bootstrap_servers: str,
    topic_name: str,
    group_id: str,
    num_messages: int,
    num_keys: int,
    num_partitions: int,
    worker_fn: Callable[[bytes], None],
) -> None:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name="baseline-profile",
        run_type="baseline",
        workload="baseline",
        topic=topic_name,
        target_messages=num_messages,
    )
    consume_messages(
        num_messages_to_process=num_messages,
        bootstrap_servers=bootstrap_servers,
        topic_name=topic_name,
        group_id=group_id,
        stats=stats,
        worker_fn=worker_fn,
    )


async def _profile_pyrallel(
    *,
    mode: ExecutionMode,
    bootstrap_servers: str,
    topic_name: str,
    group_id: str,
    num_messages: int,
    num_keys: int,
    num_partitions: int,
    timeout_sec: int,
    async_worker_fn: Callable[[WorkItem], Awaitable[None]],
    process_worker_fn: Callable[[WorkItem], None],
) -> None:
    produce_messages(
        num_messages=num_messages,
        num_keys=num_keys,
        num_partitions=num_partitions,
        topic_name=topic_name,
        bootstrap_servers=bootstrap_servers,
    )
    stats = BenchmarkStats(
        run_name="%s-profile" % mode.value,
        run_type=mode.value,
        workload=mode.value,
        topic=topic_name,
        target_messages=num_messages,
    )
    timed_out, _, _ = await run_pyrallel_consumer_test(
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
        print(
            "WARNING: %s consumer timed out — profile data is still usable but incomplete"
            % mode
        )


def _save_and_print(mode_label: str, output_dir: pathlib.Path, top_n: int) -> None:
    prof_path = output_dir / ("%s_profile.prof" % mode_label)

    func_stats = yappi.get_func_stats()
    func_stats.save(str(prof_path), type="pstat")
    print("\n%s .prof saved → %s" % (mode_label, prof_path))
    print("  Open with: uv run snakeviz %s\n" % prof_path)

    func_stats.sort("ttot", "desc")
    print("=== Top %d functions by total time [%s] ===" % (top_n, mode_label))
    func_stats.print_all(
        out=sys.stdout,
        columns={
            0: ("name", 80),
            1: ("ncall", 10),
            2: ("tsub", 8),
            3: ("ttot", 8),
            4: ("tavg", 8),
        },
    )

    yappi.clear_stats()


def run_profile(argv: Sequence[str] | None = None) -> None:
    args = _parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level, logging.WARNING),
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    output_dir = _ensure_output_dir(args.output_dir)
    modes: List[str] = args.modes

    topic_map = {m: "%s-%s" % (args.topic_prefix, m) for m in modes}
    group_map = {
        "baseline": "prof-baseline-group",
        "async": "prof-async-group",
        "process": "prof-process-group",
    }

    all_topics = [topic_map[m] for m in modes]
    all_groups = [group_map[m] for m in modes]

    print("Resetting topics/groups for profiling …")
    _reset_topics(args.bootstrap_servers, all_topics, args.num_partitions, all_groups)

    sleep_work = partial(_sleep_work_payload, sleep_ms=args.worker_sleep_ms)
    sleep_work_async = partial(_sleep_work_async_item, sleep_ms=args.worker_sleep_ms)
    sleep_work_process = partial(
        _sleep_work_process_item, sleep_ms=args.worker_sleep_ms
    )

    # ── Baseline ──────────────────────────────────────────────────────
    if "baseline" in modes:
        print("\n▶ Profiling BASELINE consumer (%d messages) …" % args.num_messages)
        yappi.set_clock_type("wall")
        yappi.start(profile_threads=True)
        try:
            _profile_baseline(
                bootstrap_servers=args.bootstrap_servers,
                topic_name=topic_map["baseline"],
                group_id=group_map["baseline"],
                num_messages=args.num_messages,
                num_keys=args.num_keys,
                num_partitions=args.num_partitions,
                worker_fn=sleep_work,
            )
        finally:
            yappi.stop()
        _save_and_print("baseline", output_dir, args.top_n)

    # ── Async ─────────────────────────────────────────────────────────
    if "async" in modes:
        print("\n▶ Profiling ASYNC consumer (%d messages) …" % args.num_messages)
        yappi.set_clock_type("wall")
        yappi.start(profile_threads=True, profile_greenlets=True)
        try:
            asyncio.run(
                _profile_pyrallel(
                    mode=ExecutionMode.ASYNC,
                    bootstrap_servers=args.bootstrap_servers,
                    topic_name=topic_map["async"],
                    group_id=group_map["async"],
                    num_messages=args.num_messages,
                    num_keys=args.num_keys,
                    num_partitions=args.num_partitions,
                    timeout_sec=args.timeout_sec,
                    async_worker_fn=sleep_work_async,
                    process_worker_fn=sleep_work_process,
                )
            )
        finally:
            yappi.stop()
        _save_and_print("async", output_dir, args.top_n)

    # ── Process ───────────────────────────────────────────────────────
    if "process" in modes:
        print("\n▶ Profiling PROCESS consumer (%d messages) …" % args.num_messages)
        yappi.set_clock_type("wall")
        yappi.start(profile_threads=True)
        try:
            asyncio.run(
                _profile_pyrallel(
                    mode=ExecutionMode.PROCESS,
                    bootstrap_servers=args.bootstrap_servers,
                    topic_name=topic_map["process"],
                    group_id=group_map["process"],
                    num_messages=args.num_messages,
                    num_keys=args.num_keys,
                    num_partitions=args.num_partitions,
                    timeout_sec=args.timeout_sec,
                    async_worker_fn=sleep_work_async,
                    process_worker_fn=sleep_work_process,
                )
            )
        finally:
            yappi.stop()
        _save_and_print("process", output_dir, args.top_n)

    # ── Summary ───────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("Profile output directory: %s" % output_dir)
    print("Files:")
    for prof_file in sorted(output_dir.glob("*.prof")):
        print("  • %s" % prof_file)
    print("\nVisualize any profile with:")
    print("  uv run snakeviz <path-to-.prof>")
    print("=" * 60)


if __name__ == "__main__":
    run_profile()
