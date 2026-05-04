from __future__ import annotations

import argparse
import asyncio
import logging
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, List, Literal, Sequence

if __package__ in {None, ""}:
    project_root = Path(__file__).resolve().parent.parent
    project_root_str = str(project_root)
    if project_root_str not in sys.path:
        sys.path.insert(0, project_root_str)

from benchmarks.baseline_consumer import consume_messages
from benchmarks.benchmark_admin import check_kafka_connection as _check_kafka_connection
from benchmarks.benchmark_artifacts import (
    build_artifact_metadata as _build_artifact_metadata,
)
from benchmarks.benchmark_cli import build_parser
from benchmarks.benchmark_output import print_table as _print_table
from benchmarks.kafka_admin import TopicConfig, reset_topics_and_groups
from benchmarks.producer import produce_messages
from benchmarks.profiling import profile_session as _profile_session
from benchmarks.profiling import relaunch_with_pyspy as _relaunch_with_pyspy
from benchmarks.profiling import summarize_worker_profiles as _summarize_worker_profiles
from benchmarks.profiling import (
    wrap_process_worker_for_profile as _wrap_process_worker_for_profile,
)
from benchmarks.pyrallel_consumer_test import (
    ExecutionMode,
    ProcessFlushPolicy,
    run_pyrallel_consumer_test,
)
from benchmarks.stats import BenchmarkResult, BenchmarkStats, write_results_json
from benchmarks.workloads import select_workers as _select_workers
from pyrallel_consumer.dto import WorkItem

BenchmarkRunKind = Literal["baseline", "async", "process"]
BenchmarkWorkerBundle = tuple[
    Callable[[bytes], None],
    Callable[[WorkItem], Awaitable[None]],
    Callable[[WorkItem], None],
]


@dataclass(frozen=True)
class BenchmarkRunPlan:
    """Describe one benchmark run identity before side-effectful execution."""

    kind: BenchmarkRunKind
    workload: str
    ordering: str
    topic_name: str
    run_name: str
    group_id: str
    strict_completion_monitor_enabled: bool
    adaptive_concurrency_enabled: bool
    workload_options: dict[str, dict[str, object]] | None


def _normalize_metrics_port(metrics_port: int | None) -> int | None:
    """Normalize metrics port for benchmark orchestration."""
    if metrics_port is None or metrics_port <= 0:
        return None
    return metrics_port


def _list_listening_pids(port: int) -> tuple[str, ...]:
    """Return process ids listening on the given TCP port when discoverable."""
    try:
        result = subprocess.run(
            ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            capture_output=True,
            check=False,
            text=True,
        )
    except OSError:
        return ()
    if result.returncode not in {0, 1}:
        return ()
    pids = tuple(
        line.strip() for line in result.stdout.splitlines() if line.strip().isdigit()
    )
    return tuple(dict.fromkeys(pids))


def _ensure_metrics_port_available(metrics_port: int | None) -> None:
    """Fail before benchmark work starts when the metrics port is occupied."""
    if metrics_port is None:
        return
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", metrics_port))
        except OSError as exc:
            pids = _list_listening_pids(metrics_port)
            pid_suffix = "(PID %s)" % ",".join(pids) if pids else ""
            raise RuntimeError(
                "Metrics port %s is already in use%s. Stop the process using that "
                "port, choose another port with --metrics-port, or disable "
                "benchmark metrics with --metrics-port 0." % (metrics_port, pid_suffix)
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
    """Run baseline round for benchmark orchestration."""
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
    route_batch_size: int = 64,
    metrics_port: int | None = None,
    adaptive_concurrency_enabled: bool = False,
) -> BenchmarkResult:
    """Run pyrparallel round for benchmark orchestration."""
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
        route_batch_size=route_batch_size,
        process_batch_size=process_batch_size
        if mode == ExecutionMode.PROCESS
        else None,
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
        route_batch_size=route_batch_size,
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


def _reset_run_targets(
    *,
    bootstrap_servers: str,
    topic_name: str,
    group_id: str,
    num_partitions: int,
) -> None:
    """Handle reset run targets within benchmark orchestration."""
    print("Resetting benchmark topics/groups: %s | groups=%s" % (topic_name, group_id))
    reset_topics_and_groups(
        bootstrap_servers=bootstrap_servers,
        topics={topic_name: TopicConfig(num_partitions=num_partitions)},
        consumer_groups=[group_id],
    )


def launch_tui() -> None:
    """Launch tui for benchmark orchestration."""
    from benchmarks.tui.app import BenchmarkTuiApp

    BenchmarkTuiApp().run()


def _warn_on_tiny_partition_process_defaults(args: argparse.Namespace) -> None:
    """Handle warn on tiny partition process defaults within benchmark orchestration."""
    if args.skip_process:
        return
    if "sleep" not in args.workloads:
        return
    if "partition" not in args.order:
        return
    if _effective_sleep_ms(args) > 0.5:
        return
    if args.process_batch_size is not None:
        return
    if args.process_max_batch_wait_ms is not None:
        return
    if args.process_flush_policy is not None:
        return
    if args.process_demand_flush_min_residence_ms is not None:
        return

    print(
        "[warning] Tiny process partition benchmark detected; default batching can dominate throughput. "
        "Compare with --process-batch-size 1 --process-max-batch-wait-ms 0.",
        flush=True,
    )


def _resolve_effective_process_batching(
    args: argparse.Namespace,
    *,
    strict_completion_monitor_enabled: bool | None = None,
) -> tuple[int | None, int | None]:
    """Resolve effective process batching for benchmark orchestration."""
    process_batch_size = args.process_batch_size
    process_max_batch_wait_ms = args.process_max_batch_wait_ms

    if args.skip_process:
        return process_batch_size, process_max_batch_wait_ms
    if "sleep" not in args.workloads:
        return process_batch_size, process_max_batch_wait_ms
    if "partition" not in args.order:
        return process_batch_size, process_max_batch_wait_ms
    if strict_completion_monitor_enabled is None:
        strict_completion_monitor_enabled = "on" in args.strict_completion_monitor
    if not strict_completion_monitor_enabled:
        return process_batch_size, process_max_batch_wait_ms
    if _effective_sleep_ms(args) > 0.5:
        return process_batch_size, process_max_batch_wait_ms
    if process_batch_size is not None:
        return process_batch_size, process_max_batch_wait_ms
    if process_max_batch_wait_ms is not None:
        return process_batch_size, process_max_batch_wait_ms
    if args.process_flush_policy is not None:
        return process_batch_size, process_max_batch_wait_ms
    if args.process_demand_flush_min_residence_ms is not None:
        return process_batch_size, process_max_batch_wait_ms

    print(
        "[info] Auto-tuning process micro-batch for strict partition run: "
        "process_batch_size=1, process_max_batch_wait_ms=0",
        flush=True,
    )
    return 1, 0


def _effective_sleep_ms(args: argparse.Namespace) -> float:
    """Return the selected sleep workload cost for benchmark heuristics."""
    workload_options = getattr(args, "workload_options", {}) or {}
    sleep_options = workload_options.get("sleep", {})
    raw_sleep_ms = sleep_options.get("sleep_ms", args.worker_sleep_ms)
    if raw_sleep_ms is None:
        return 0.5
    return float(raw_sleep_ms)


def _build_benchmark_run_plans(args: argparse.Namespace) -> list[BenchmarkRunPlan]:
    """Build the benchmark run matrix without executing any benchmark work."""
    plans: list[BenchmarkRunPlan] = []
    workloads = list(args.workloads)
    orderings = list(args.order)
    strict_monitor_modes = list(args.strict_completion_monitor)
    adaptive_concurrency_modes = list(args.adaptive_concurrency)

    for workload in workloads:
        workload_options = _workload_options_for(args.workload_options, workload)
        for ordering in orderings:
            suffix = "-%s-%s" % (workload, ordering)
            run_prefix = "%s-%s" % (workload, ordering)

            if not args.skip_baseline:
                plans.append(
                    BenchmarkRunPlan(
                        kind="baseline",
                        workload=workload,
                        ordering=ordering,
                        topic_name="%s%s-baseline" % (args.topic_prefix, suffix),
                        run_name="%s-baseline" % run_prefix,
                        group_id="%s%s" % (args.baseline_group, suffix),
                        strict_completion_monitor_enabled=True,
                        adaptive_concurrency_enabled=False,
                        workload_options=workload_options,
                    )
                )

            for strict_monitor_mode in strict_monitor_modes:
                strict_completion_monitor_enabled = strict_monitor_mode == "on"
                strict_suffix = ""
                if len(strict_monitor_modes) > 1 or strict_monitor_mode != "on":
                    strict_suffix = "-strict-%s" % strict_monitor_mode

                for adaptive_concurrency_mode in adaptive_concurrency_modes:
                    adaptive_concurrency_enabled = adaptive_concurrency_mode == "on"
                    adaptive_suffix = ""
                    if (
                        len(adaptive_concurrency_modes) > 1
                        or adaptive_concurrency_mode != "off"
                    ):
                        adaptive_suffix = "-adaptive-%s" % adaptive_concurrency_mode
                    mode_suffix = "%s%s" % (strict_suffix, adaptive_suffix)

                    if not args.skip_async:
                        plans.append(
                            BenchmarkRunPlan(
                                kind="async",
                                workload=workload,
                                ordering=ordering,
                                topic_name="%s%s-async%s"
                                % (args.topic_prefix, suffix, mode_suffix),
                                run_name="%s-pyrallel-async%s"
                                % (run_prefix, mode_suffix),
                                group_id="%s%s%s"
                                % (args.async_group, suffix, mode_suffix),
                                strict_completion_monitor_enabled=(
                                    strict_completion_monitor_enabled
                                ),
                                adaptive_concurrency_enabled=(
                                    adaptive_concurrency_enabled
                                ),
                                workload_options=workload_options,
                            )
                        )

                    if not args.skip_process:
                        plans.append(
                            BenchmarkRunPlan(
                                kind="process",
                                workload=workload,
                                ordering=ordering,
                                topic_name="%s%s-process%s"
                                % (args.topic_prefix, suffix, mode_suffix),
                                run_name="%s-pyrallel-process%s"
                                % (run_prefix, mode_suffix),
                                group_id="%s%s%s"
                                % (args.process_group, suffix, mode_suffix),
                                strict_completion_monitor_enabled=(
                                    strict_completion_monitor_enabled
                                ),
                                adaptive_concurrency_enabled=(
                                    adaptive_concurrency_enabled
                                ),
                                workload_options=workload_options,
                            )
                        )
    return plans


def _group_benchmark_run_plans(
    plans: Sequence[BenchmarkRunPlan],
) -> list[list[BenchmarkRunPlan]]:
    """Group prepared plans by the event-loop isolation boundary."""
    grouped_plans: list[list[BenchmarkRunPlan]] = []
    current_group: list[BenchmarkRunPlan] = []
    current_key: tuple[str, str] | None = None

    for plan in plans:
        plan_key = (plan.workload, plan.ordering)
        if current_key is not None and plan_key != current_key:
            grouped_plans.append(current_group)
            current_group = []
        current_key = plan_key
        current_group.append(plan)

    if current_group:
        grouped_plans.append(current_group)
    return grouped_plans


def _workers_for_plan(
    args: argparse.Namespace,
    *,
    plan: BenchmarkRunPlan,
    worker_bundles: dict[str, BenchmarkWorkerBundle],
) -> BenchmarkWorkerBundle:
    """Return cached workers for a workload plan."""
    if plan.workload not in worker_bundles:
        worker_bundles[plan.workload] = _select_workers(
            workload=plan.workload,
            sleep_ms=args.worker_sleep_ms,
            cpu_iterations=args.worker_cpu_iterations,
            io_sleep_ms=args.worker_io_sleep_ms,
            workload_options=plan.workload_options,
            validate_process_worker=not args.skip_process,
        )
    return worker_bundles[plan.workload]


def _process_batching_for_plan(
    args: argparse.Namespace,
    *,
    plan: BenchmarkRunPlan,
    process_batching: dict[tuple[str, str, bool], tuple[int | None, int | None]],
) -> tuple[int | None, int | None]:
    """Resolve process batching once per workload/order/strict variant."""
    key = (
        plan.workload,
        plan.ordering,
        plan.strict_completion_monitor_enabled,
    )
    if key not in process_batching:
        process_batching[key] = _resolve_effective_process_batching(
            args,
            strict_completion_monitor_enabled=(plan.strict_completion_monitor_enabled),
        )
    return process_batching[key]


def _execute_baseline_run_plan(
    args: argparse.Namespace,
    *,
    plan: BenchmarkRunPlan,
    profile_dir: Path,
    baseline_worker: Callable[[bytes], None],
) -> BenchmarkResult:
    """Execute one baseline benchmark plan."""
    with _profile_session(
        enabled=args.profile,
        run_name=plan.run_name,
        output_dir=profile_dir,
        clock=args.profile_clock,
        profile_threads=args.profile_threads,
        profile_greenlets=args.profile_greenlets,
        top_n=args.profile_top_n,
    ):
        if not args.skip_reset:
            _reset_run_targets(
                bootstrap_servers=args.bootstrap_servers,
                topic_name=plan.topic_name,
                group_id=plan.group_id,
                num_partitions=args.num_partitions,
            )
        return _run_baseline_round(
            run_name=plan.run_name,
            topic_name=plan.topic_name,
            num_messages=args.num_messages,
            bootstrap_servers=args.bootstrap_servers,
            num_partitions=args.num_partitions,
            num_keys=args.num_keys,
            group_id=plan.group_id,
            worker_fn=baseline_worker,
            workload=plan.workload,
            ordering=plan.ordering,
            ensure_topic_exists=args.skip_reset,
        )


async def _execute_async_benchmark_run_plans(
    args: argparse.Namespace,
    *,
    plans: Sequence[BenchmarkRunPlan],
    metrics_port: int | None,
    profile_dir: Path,
    worker_bundles: dict[str, BenchmarkWorkerBundle],
    process_batching: dict[tuple[str, str, bool], tuple[int | None, int | None]],
) -> List[BenchmarkResult]:
    """Execute one workload/order async+process plan group."""
    results: List[BenchmarkResult] = []

    for plan in plans:
        _, async_worker_fn, process_worker_fn = _workers_for_plan(
            args,
            plan=plan,
            worker_bundles=worker_bundles,
        )
        (
            effective_process_batch_size,
            effective_process_max_batch_wait_ms,
        ) = _process_batching_for_plan(
            args,
            plan=plan,
            process_batching=process_batching,
        )
        if not args.skip_reset:
            _reset_run_targets(
                bootstrap_servers=args.bootstrap_servers,
                topic_name=plan.topic_name,
                group_id=plan.group_id,
                num_partitions=args.num_partitions,
            )

        if plan.kind == "async":
            with _profile_session(
                enabled=args.profile,
                run_name=plan.run_name,
                output_dir=profile_dir,
                clock=args.profile_clock,
                profile_threads=args.profile_threads,
                profile_greenlets=args.profile_greenlets,
                top_n=args.profile_top_n,
            ):
                results.append(
                    await _run_pyrparallel_round(
                        topic_name=plan.topic_name,
                        run_name=plan.run_name,
                        mode=ExecutionMode.ASYNC,
                        num_messages=args.num_messages,
                        bootstrap_servers=args.bootstrap_servers,
                        num_partitions=args.num_partitions,
                        num_keys=args.num_keys,
                        group_id=plan.group_id,
                        timeout_sec=args.timeout_sec,
                        async_worker_fn=async_worker_fn,
                        process_worker_fn=process_worker_fn,
                        workload=plan.workload,
                        ordering=plan.ordering,
                        ensure_topic_exists=args.skip_reset,
                        strict_completion_monitor_enabled=(
                            plan.strict_completion_monitor_enabled
                        ),
                        process_count=None,
                        process_batch_size=effective_process_batch_size,
                        process_max_batch_wait_ms=(effective_process_max_batch_wait_ms),
                        process_flush_policy=args.process_flush_policy,
                        process_demand_flush_min_residence_ms=(
                            args.process_demand_flush_min_residence_ms
                        ),
                        route_batch_size=1,
                        metrics_port=metrics_port,
                        adaptive_concurrency_enabled=(
                            plan.adaptive_concurrency_enabled
                        ),
                    )
                )
            continue

        prof_process_worker = process_worker_fn
        if args.profile and args.profile_process_workers:
            prof_process_worker = _wrap_process_worker_for_profile(
                process_worker_fn,
                output_dir=profile_dir,
                run_name=plan.run_name,
                clock=args.profile_clock,
                profile_threads=args.profile_threads,
                profile_greenlets=args.profile_greenlets,
            )
        results.append(
            await _run_pyrparallel_round(
                topic_name=plan.topic_name,
                run_name=plan.run_name,
                mode=ExecutionMode.PROCESS,
                num_messages=args.num_messages,
                bootstrap_servers=args.bootstrap_servers,
                num_partitions=args.num_partitions,
                num_keys=args.num_keys,
                group_id=plan.group_id,
                timeout_sec=args.timeout_sec,
                async_worker_fn=async_worker_fn,
                process_worker_fn=prof_process_worker,
                workload=plan.workload,
                ordering=plan.ordering,
                ensure_topic_exists=args.skip_reset,
                strict_completion_monitor_enabled=(
                    plan.strict_completion_monitor_enabled
                ),
                process_count=args.process_count,
                process_batch_size=effective_process_batch_size,
                process_max_batch_wait_ms=effective_process_max_batch_wait_ms,
                process_flush_policy=args.process_flush_policy,
                process_demand_flush_min_residence_ms=(
                    args.process_demand_flush_min_residence_ms
                ),
                route_batch_size=args.process_route_batch_size,
                metrics_port=metrics_port,
                adaptive_concurrency_enabled=plan.adaptive_concurrency_enabled,
            )
        )
        if args.profile and args.profile_process_workers:
            _summarize_worker_profiles(
                plan.run_name,
                profile_dir=profile_dir,
                top_n=args.profile_top_n,
                clock=args.profile_clock,
            )
    return results


def _execute_benchmark_run_plans(
    args: argparse.Namespace,
    *,
    plans: Sequence[BenchmarkRunPlan],
    metrics_port: int | None,
    profile_dir: Path,
) -> List[BenchmarkResult]:
    """Execute prepared benchmark plans while preserving loop isolation."""
    results: List[BenchmarkResult] = []
    worker_bundles: dict[str, BenchmarkWorkerBundle] = {}
    process_batching: dict[tuple[str, str, bool], tuple[int | None, int | None]] = {}

    for plan_group in _group_benchmark_run_plans(plans):
        async_plans: list[BenchmarkRunPlan] = []
        for plan in plan_group:
            if plan.kind != "baseline":
                async_plans.append(plan)
                continue
            baseline_worker, _, _ = _workers_for_plan(
                args,
                plan=plan,
                worker_bundles=worker_bundles,
            )
            results.append(
                _execute_baseline_run_plan(
                    args,
                    plan=plan,
                    profile_dir=profile_dir,
                    baseline_worker=baseline_worker,
                )
            )

        if async_plans:
            results.extend(
                asyncio.run(
                    _execute_async_benchmark_run_plans(
                        args,
                        plans=async_plans,
                        metrics_port=metrics_port,
                        profile_dir=profile_dir,
                        worker_bundles=worker_bundles,
                        process_batching=process_batching,
                    )
                )
            )

    return results


def run_benchmark(
    args: argparse.Namespace, raw_argv: Sequence[str] | None = None
) -> None:
    """Run benchmark for benchmark orchestration."""
    args._raw_argv = list(raw_argv or [])
    metrics_port = _normalize_metrics_port(args.metrics_port)

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
    _ensure_metrics_port_available(metrics_port)
    profile_dir = Path(args.profile_dir)

    _warn_on_tiny_partition_process_defaults(args)

    plans = _build_benchmark_run_plans(args)
    if not plans:
        raise RuntimeError("All benchmark runs are skipped; nothing to execute")

    results = _execute_benchmark_run_plans(
        args,
        plans=plans,
        metrics_port=metrics_port,
        profile_dir=profile_dir,
    )

    _print_table(results)
    output_path = args.json_output
    if output_path is None:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_path = f"benchmarks/results/{timestamp}.json"
    options = {k: v for k, v in vars(args).items()}
    artifact_metadata = _build_artifact_metadata(output_path=output_path)
    write_results_json(
        results,
        Path(output_path),
        options=options,
        artifact_metadata=artifact_metadata,
    )
    print(f"\nJSON summary written to {output_path}")


def _workload_options_for(
    workload_options: dict[str, dict[str, object]] | None, workload: str
) -> dict[str, dict[str, object]] | None:
    """Return only generic options for the workload currently being built."""
    if workload_options is None:
        return None
    selected_options = workload_options.get(workload)
    if not selected_options:
        return None
    return {workload: selected_options}


def main(argv: Sequence[str] | None = None) -> None:
    """Run the command-line entrypoint."""
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if not raw_argv:
        launch_tui()
        return

    parser = build_parser()
    args = parser.parse_args(raw_argv)
    try:
        run_benchmark(args, raw_argv=raw_argv)
    except RuntimeError as exc:
        raise SystemExit("error: %s" % exc) from None


if __name__ == "__main__":
    main()
