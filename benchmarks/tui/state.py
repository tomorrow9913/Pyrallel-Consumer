from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def default_workloads() -> tuple[str, ...]:
    """Return the registry-derived default TUI workload selection."""
    from benchmarks.workloads import available_names

    names = available_names()
    return names[:1]


@dataclass(slots=True)
class BenchmarkTuiState:
    """Represent benchmark tui state data used by state."""

    workloads: tuple[str, ...] = field(default_factory=default_workloads)
    workload_options: dict[str, dict[str, object]] = field(default_factory=dict)
    ordering_modes: tuple[str, ...] = ("key_hash",)
    bootstrap_servers: str = "localhost:9092"
    num_messages: int = 100_000
    num_keys: int = 100
    num_partitions: int = 8
    topic_prefix: str = "pyrallel-benchmark"
    baseline_group: str = "baseline-benchmark-group"
    async_group: str = "async-benchmark-group"
    process_group: str = "process-benchmark-group"
    json_output: str = ""
    skip_baseline: bool = False
    skip_async: bool = False
    skip_process: bool = False
    skip_reset: bool = False
    timeout_sec: int = 60
    log_level: str = "WARNING"
    profiling_enabled: bool = False
    profile: bool = False
    profile_dir: str = "benchmarks/results/profiles"
    profile_clock: str = "wall"
    profile_top_n: int = 0
    profile_threads: bool = False
    profile_greenlets: bool = False
    profile_process_workers: bool = False
    worker_sleep_ms: float = 0.5
    worker_cpu_iterations: int = 1000
    worker_io_sleep_ms: float = 0.5
    process_count: int | None = 4
    process_transport: str = "worker_pipes"
    process_batch_size: int | None = 1
    process_max_batch_wait_ms: int | None = 0
    route_batch_size: int = 64
    metrics_port: int = 9091
    py_spy: bool = False
    py_spy_format: str = "flamegraph"
    py_spy_output: str = "benchmarks/results/pyspy"
    py_spy_rate: int = 100
    py_spy_native: bool = False
    py_spy_idle: bool = False
    py_spy_top: bool = False

    def to_argv(self) -> list[str]:
        """Handle to argv within state."""
        argv = [
            "--bootstrap-servers",
            self.bootstrap_servers,
            "--num-messages",
            str(self.num_messages),
            "--num-keys",
            str(self.num_keys),
            "--num-partitions",
            str(self.num_partitions),
            "--topic-prefix",
            self.topic_prefix,
            "--baseline-group",
            self.baseline_group,
            "--async-group",
            self.async_group,
            "--process-group",
            self.process_group,
            "--timeout-sec",
            str(self.timeout_sec),
            "--log-level",
            self.log_level,
            "--workloads",
            ",".join(self.workloads),
            "--order",
            ",".join(self.ordering_modes),
            "--worker-sleep-ms",
            self._format_option_value(
                self._workload_option_value("sleep", "sleep_ms", self.worker_sleep_ms)
            ),
            "--worker-cpu-iterations",
            self._format_option_value(
                self._workload_option_value(
                    "cpu", "iterations", self.worker_cpu_iterations
                )
            ),
            "--worker-io-sleep-ms",
            self._format_option_value(
                self._workload_option_value("io", "sleep_ms", self.worker_io_sleep_ms)
            ),
            "--process-transport",
            self.process_transport,
            "--route-batch-size",
            str(self.route_batch_size),
        ]

        argv.extend(["--metrics-port", str(self.metrics_port)])

        for value, flag in (
            (self.process_count, "--process-count"),
            (self.process_batch_size, "--process-batch-size"),
            (self.process_max_batch_wait_ms, "--process-max-batch-wait-ms"),
        ):
            if value is not None:
                argv.extend([flag, str(value)])

        if self.profiling_enabled:
            argv.extend(
                [
                    "--profile-dir",
                    self.profile_dir,
                    "--profile-clock",
                    self.profile_clock,
                    "--profile-top-n",
                    str(self.profile_top_n),
                    "--py-spy-format",
                    self.py_spy_format,
                    "--py-spy-output",
                    self.py_spy_output,
                    "--py-spy-rate",
                    str(self.py_spy_rate),
                ]
            )

        if self.json_output:
            argv.extend(["--json-output", self.json_output])

        for enabled, flag in (
            (self.skip_baseline, "--skip-baseline"),
            (self.skip_async, "--skip-async"),
            (self.skip_process, "--skip-process"),
            (self.skip_reset, "--skip-reset"),
            (self.profiling_enabled and self.profile, "--profile"),
            (self.profiling_enabled and self.profile_threads, "--profile-threads"),
            (self.profiling_enabled and self.profile_greenlets, "--profile-greenlets"),
            (
                self.profiling_enabled and self.profile_process_workers,
                "--profile-process-workers",
            ),
            (self.profiling_enabled and self.py_spy, "--py-spy"),
            (self.profiling_enabled and self.py_spy_native, "--py-spy-native"),
            (self.profiling_enabled and self.py_spy_idle, "--py-spy-idle"),
            (self.profiling_enabled and self.py_spy_top, "--py-spy-top"),
        ):
            if enabled:
                argv.append(flag)

        self._append_generic_workload_options(argv)

        return argv

    def _workload_option_value(
        self, workload: str, option_name: str, default: object
    ) -> object:
        """Return the selected workload option value or its legacy default."""
        if workload not in self.workloads:
            return default
        return self.workload_options.get(workload, {}).get(option_name, default)

    def _append_generic_workload_options(self, argv: list[str]) -> None:
        """Append generic workload option flags for non-legacy options."""
        legacy_options = {
            ("sleep", "sleep_ms"),
            ("cpu", "iterations"),
            ("io", "sleep_ms"),
        }
        for workload in self.workloads:
            for option_name, value in self.workload_options.get(workload, {}).items():
                if (workload, option_name) in legacy_options:
                    continue
                argv.extend(
                    [
                        "--workload-option",
                        "%s.%s=%s"
                        % (workload, option_name, self._format_option_value(value)),
                    ]
                )

    @staticmethod
    def _format_option_value(value: Any) -> str:
        """Format a workload option value for argv emission."""
        if isinstance(value, bool):
            return str(value).lower()
        return str(value)
