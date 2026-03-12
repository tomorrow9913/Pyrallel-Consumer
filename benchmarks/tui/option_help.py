from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class OptionHelp:
    label: str
    description: str
    browse: bool = False


OPTION_HELP = {
    "bootstrap-servers": OptionHelp(
        label="Bootstrap servers",
        description="Connect to the Kafka cluster",
    ),
    "num-messages": OptionHelp(
        label="Number of messages",
        description="Choose how many benchmark messages to produce and consume.",
    ),
    "num-keys": OptionHelp(
        label="Number of keys",
        description="Spread records across this many distinct message keys.",
    ),
    "num-partitions": OptionHelp(
        label="Number of partitions",
        description="Create benchmark topics with this partition count.",
    ),
    "timeout-sec": OptionHelp(
        label="Timeout (sec)",
        description="Stop waiting after this many seconds if a run stalls.",
    ),
    "workloads": OptionHelp(
        label="Workloads",
        description="Choose one or more workload shapes to benchmark: sleep, cpu, io.",
    ),
    "ordering-modes": OptionHelp(
        label="Ordering modes",
        description="Choose one or more ordering modes: key_hash, partition, unordered.",
    ),
    "worker-sleep-ms": OptionHelp(
        label="Worker sleep (ms)",
        description="Delay each sleep workload message by this many milliseconds.",
    ),
    "worker-cpu-iterations": OptionHelp(
        label="Worker CPU iterations",
        description="Repeat the CPU hash loop this many times per message.",
    ),
    "worker-io-sleep-ms": OptionHelp(
        label="Worker IO sleep (ms)",
        description="Delay each simulated IO workload by this many milliseconds.",
    ),
    "json-output": OptionHelp(
        label="JSON summary output",
        description="Save the benchmark summary JSON under this directory or file path.",
        browse=True,
    ),
    "skip-reset": OptionHelp(
        label="Skip topic/group reset",
        description="Reuse the existing topic and consumer groups instead of resetting them.",
    ),
    "profiling-enabled": OptionHelp(
        label="Profiling enabled",
        description="Turn every profiling option on or off without losing current values.",
    ),
    "profile": OptionHelp(
        label="Enable yappi profiling",
        description="Capture yappi profiles for supported benchmark runs.",
    ),
    "py-spy": OptionHelp(
        label="Enable py-spy",
        description="Wrap process benchmarks with py-spy profiling when enabled.",
    ),
    "topic-prefix": OptionHelp(
        label="Topic prefix",
        description="Prefix benchmark topics with this value before adding workload names.",
    ),
    "log-level": OptionHelp(
        label="Log level",
        description="Control benchmark script log verbosity.",
    ),
    "skip-baseline": OptionHelp(
        label="Skip baseline",
        description="Skip the single-threaded baseline consumer run.",
    ),
    "skip-async": OptionHelp(
        label="Skip async",
        description="Skip the asyncio execution engine benchmark run.",
    ),
    "skip-process": OptionHelp(
        label="Skip process",
        description="Skip the multiprocessing execution engine benchmark run.",
    ),
    "profile-dir": OptionHelp(
        label="Profile output directory",
        description="Write yappi .prof files under this directory.",
        browse=True,
    ),
    "profile-top-n": OptionHelp(
        label="Profile top N",
        description="Print this many top functions after each profiled run; use 0 to hide it.",
    ),
    "py-spy-format": OptionHelp(
        label="py-spy format",
        description="Choose which py-spy output format to save.",
    ),
    "py-spy-output": OptionHelp(
        label="py-spy output directory",
        description="Write py-spy recordings under this directory.",
        browse=True,
    ),
    "py-spy-native": OptionHelp(
        label="Include native frames",
        description="Include native C extension frames in py-spy output.",
    ),
    "py-spy-idle": OptionHelp(
        label="Include idle frames",
        description="Include idle thread stacks in py-spy output.",
    ),
}


PROFILING_CONTROL_IDS = frozenset(
    {
        "profile",
        "py-spy",
        "profile-dir",
        "browse-profile-dir",
        "profile-top-n",
        "py-spy-format",
        "py-spy-output",
        "browse-py-spy-output",
        "py-spy-native",
        "py-spy-idle",
    }
)
