from __future__ import annotations

import argparse
from typing import Sequence

from pyrallel_consumer.dto import OrderingMode

_WORKLOAD_CHOICES = ("sleep", "cpu", "io")
_ORDER_CHOICES = tuple(mode.value for mode in OrderingMode)
_STRICT_COMPLETION_MONITOR_CHOICES = ("on", "off")
_ADAPTIVE_CONCURRENCY_CHOICES = ("off", "on")
_PROCESS_FLUSH_POLICY_CHOICES = (
    "size_or_timer",
    "demand",
    "demand_min_residence",
)
_PROCESS_TRANSPORT_CHOICES = ("shared_queue", "worker_pipes")


def parse_csv_selection(
    value: str, *, argument_name: str, choices: Sequence[str]
) -> list[str]:
    """Parse csv selection for benchmark cli."""
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


def build_parser() -> argparse.ArgumentParser:
    """Build parser for benchmark cli."""
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
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--workloads",
            choices=_WORKLOAD_CHOICES,
        ),
        default=["sleep"],
        help="Comma-separated workloads to run (choices: sleep,cpu,io)",
    )
    parser.add_argument(
        "--order",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--order",
            choices=_ORDER_CHOICES,
        ),
        default=["key_hash"],
        help="Comma-separated ordering modes to run (choices: key_hash,partition,unordered)",
    )
    parser.add_argument(
        "--strict-completion-monitor",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--strict-completion-monitor",
            choices=_STRICT_COMPLETION_MONITOR_CHOICES,
        ),
        default=["on"],
        help="Comma-separated strict completion monitor modes to run (choices: on,off)",
    )
    parser.add_argument(
        "--adaptive-concurrency",
        type=lambda value: parse_csv_selection(
            value,
            argument_name="--adaptive-concurrency",
            choices=_ADAPTIVE_CONCURRENCY_CHOICES,
        ),
        default=["off"],
        help="Comma-separated adaptive concurrency modes for Pyrallel runs (choices: off,on)",
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
        "--process-count",
        type=int,
        default=None,
        help="Override process-mode worker count for benchmark runs",
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
    parser.add_argument(
        "--process-flush-policy",
        choices=_PROCESS_FLUSH_POLICY_CHOICES,
        default=None,
        help="Override process-mode flush policy for benchmark runs",
    )
    parser.add_argument(
        "--process-demand-flush-min-residence-ms",
        type=int,
        default=None,
        help="Override minimum residence time before demand flush is allowed",
    )
    parser.add_argument(
        "--process-transport",
        choices=_PROCESS_TRANSPORT_CHOICES,
        default="shared_queue",
        help="Select process-mode input transport for benchmark runs",
    )
    parser.add_argument(
        "--metrics-port",
        type=int,
        default=9091,
        help="Expose Prometheus metrics on the host at this port during Pyrallel benchmark runs (default: 9091, use 0 to disable)",
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
