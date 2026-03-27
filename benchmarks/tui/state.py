from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class BenchmarkTuiState:
    workloads: tuple[str, ...] = ("sleep",)
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
    metrics_port: int = 9091
    py_spy: bool = False
    py_spy_format: str = "flamegraph"
    py_spy_output: str = "benchmarks/results/pyspy"
    py_spy_rate: int = 100
    py_spy_native: bool = False
    py_spy_idle: bool = False
    py_spy_top: bool = False

    def to_argv(self) -> list[str]:
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
            str(self.worker_sleep_ms),
            "--worker-cpu-iterations",
            str(self.worker_cpu_iterations),
            "--worker-io-sleep-ms",
            str(self.worker_io_sleep_ms),
        ]

        if self.metrics_port > 0:
            argv.extend(["--metrics-port", str(self.metrics_port)])

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

        return argv
