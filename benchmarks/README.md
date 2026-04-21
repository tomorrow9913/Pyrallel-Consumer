# Benchmarks

This folder contains the benchmarking and profiling entrypoints for Pyrallel Consumer.

## Scripts
- `run_parallel_benchmark.py`: primary throughput/latency benchmark for baseline, async, and process modes (can optionally enable profiling).
- `profile_benchmark_yappi.py`: legacy profiler runner; equivalent behavior is available via `run_parallel_benchmark.py --profile`.

## Quick start
```bash
# Launch the Textual TUI (no CLI flags)
uv run python -m benchmarks.run_parallel_benchmark

# Standard benchmark (no profiling)
uv run python -m benchmarks.run_parallel_benchmark --num-messages 100000 --num-partitions 8

# Enable profiling for all modes (writes .prof files)
uv run python -m benchmarks.run_parallel_benchmark \
  --profile \
  --profile-dir benchmarks/results/profiles \
  --profile-clock wall \
  --profile-top-n 20

# Use CPU-bound workload
uv run python -m benchmarks.run_parallel_benchmark \
  --profile \
  --workloads cpu \
  --order key_hash \
  --worker-cpu-iterations 2000

# Use IO-bound workload (sleep-based wait)
uv run python -m benchmarks.run_parallel_benchmark \
  --profile \
  --workloads io \
  --order key_hash \
  --worker-io-sleep-ms 5
```

For benchmark development or local benchmark test runs, install the dev tooling set:

```bash
uv sync --group dev
```

## Key options
- No arguments: launches the Textual TUI so you can configure and start the benchmark interactively.
- General: `--bootstrap-servers`, `--num-messages`, `--num-keys`, `--num-partitions`, `--topic-prefix`, `--timeout-sec`, `--skip-{baseline,async,process}`, `--skip-reset`.
- Workload / ordering selection:
  - `--workloads sleep,cpu,io` (comma-separated subset; defaults to `sleep` when omitted).
  - `--order key_hash,partition,unordered` (comma-separated subset; defaults to `key_hash` when omitted).
  - `--strict-completion-monitor on,off` (comma-separated subset for benchmark comparison).
  - `--adaptive-concurrency off,on` (comma-separated subset for Pyrallel adaptive concurrency A/B comparison; defaults to `off`).
  - `--metrics-port`: expose Prometheus metrics on the host for the current benchmark process (defaults to `9091`; use `0` to disable).
  - `--worker-sleep-ms`: per-message sleep for `sleep` workload (default 0.5ms).
  - `--worker-cpu-iterations`: hash loop iterations for `cpu` workload (default 1000).
  - `--worker-io-sleep-ms`: per-message sleep for `io` workload (default 0.5ms).
  - `--process-batch-size`: override process-mode micro-batch size for benchmark runs only.
  - `--process-max-batch-wait-ms`: override process-mode micro-batch wait for benchmark runs only.
  - `--process-flush-policy`: override process-mode flush policy (`size_or_timer`, `demand`, `demand_min_residence`) for benchmark runs only.
  - `--process-demand-flush-min-residence-ms`: minimum residence time before demand flush is allowed when using `demand_min_residence`.
- Profiling (yappi):
  - `--profile`: enable profiling (baseline/async only; process mode profiling is disabled by default).
  - `--profile-dir`: directory to write `.prof` files (default `benchmarks/results/profiles`).
  - `--profile-clock {wall,cpu}`: yappi clock (default `wall`).
  - `--profile-top-n`: print top-N functions by total time (0 to skip).
  - `--profile-threads`, `--profile-greenlets`: include threads/async tasks.
  - `--profile-process-workers`: also profile process workers (off by default; may be unstable on some platforms).
- Profiling (py-spy — process mode):
  - `--py-spy`: enable py-spy profiling (wraps benchmark via self-relaunch with `--subprocesses` to capture worker processes).
  - `--py-spy-format {flamegraph,speedscope,raw,chrometrace}`: output format (default `flamegraph`).
  - `--py-spy-output DIR`: directory for py-spy output files (default `benchmarks/results/pyspy/`).
  - `--py-spy-rate HZ`: sampling rate in Hz (default 100).
  - `--py-spy-native`: include native C extension frames.
  - `--py-spy-idle`: include idle thread stacks.
  - `--py-spy-top`: use live `top` view instead of `record` mode.


## Outputs
- Benchmark results: console table + JSON summary (default `benchmarks/results/<timestamp>.json`).
- JSON summaries include `performance_improvements`, which reports TPS delta,
  percent delta, and ratio for adaptive on/off pairs plus the best Pyrallel run
  versus the matching baseline for each workload/order combination.
- Profiling: per-mode `.prof` files under `profile-dir` (e.g., `pyrallel-async.prof`, `pyrallel-process.prof`). Process mode also saves per-worker `.prof` files (suffix `-worker-<pid>.prof`) when profiling is enabled.
- py-spy: per-run output files under `--py-spy-output` directory (default `benchmarks/results/pyspy/`). File names include format and UTC timestamp (e.g., `pyspy-flamegraph-20260226T001500Z.svg`).

## Process Batch Advisor

`benchmarks.process_batch_advisor` is an advisor-only PoC for process batch / IPC
experiments. It reads an existing benchmark JSON summary with
`process_batch_metrics` and emits next-run benchmark flags only.

```bash
uv run python -m benchmarks.process_batch_advisor benchmarks/results/<summary>.json
uv run python -m benchmarks.process_batch_advisor benchmarks/results/<summary>.json --format json
```

The advisor may recommend benchmark-only follow-up flags such as
`--process-batch-size`, `--process-max-batch-wait-ms`, `--process-flush-policy`,
and `--process-demand-flush-min-residence-ms`. It must not recommend or mutate
runtime `process_count` or `queue_size`; those remain research-review / CTO
approval items.

For the release-review reference that ties the benchmark baseline policy to the
current soak evidence package, see
[`docs/operations/stable-operations-evidence.md`](../docs/operations/stable-operations-evidence.md).


## Recent sample results (no profiling, 4 partitions, 2000 msgs, 100 keys)
- Workload `sleep` (5ms): baseline 159.69 TPS; async 2206.35 TPS; process 910.04 TPS.
- Workload `cpu` (500 iterations): baseline 2598.79 TPS; async 1403.07 TPS; process 2072.26 TPS.
- Workload `io` (5ms): baseline 159.89 TPS; async 2797.18 TPS; process 916.60 TPS.

> Note: process mode ran without profiling for stability; profiles (when enabled) cover baseline/async only by default.

## Notes
- Profiling adds overhead; do not compare TPS from profiled runs to non-profiled runs.
- Process worker profiling is enabled when `--profile` is set; per-worker files are saved automatically. Use `uv run snakeviz <prof>` to inspect.
- For clean TPS measurements, keep logging low: `--log-level WARNING` (default). Using `DEBUG` can materially reduce throughput and should only be used for debugging, not performance comparisons.
- For tiny `sleep` workloads in process + `partition` ordering, default batching may dominate throughput. Compare against `--process-batch-size 1 --process-max-batch-wait-ms 0` before changing library defaults.
- Experimental demand-flush policies are exposed through `--process-flush-policy`; start with `demand` and `demand_min_residence --process-demand-flush-min-residence-ms 1` when reproducing issue #14.
- If Prometheus/Grafana is running from `.github/e2e.compose.yml`, the benchmark now exposes metrics on `9091` by default so the `pyrallel-consumer` target comes up automatically.
- Use `--metrics-port 0` when you want to disable benchmark-side Prometheus exposure.
- `kafka-exporter` should recover automatically once Kafka is ready; if `pyrallel-consumer` stays `down`, make sure the benchmark process is still running with `--metrics-port 9091`.

## Soak / Long-Running Stability Notes

For release-readiness work, keep a lightweight note for any long-running benchmark or soak pass rather than treating the console output as disposable.

Recommended note fields:
- benchmark command
- start/end time or approximate duration
- workloads / ordering modes exercised
- whether strict completion monitor was on or off
- resulting JSON/profiler artifact paths
- notable lag, gap, backpressure, DLQ, rebalance, or restart observations

Example soak-oriented run:

```bash
uv run python -m benchmarks.run_parallel_benchmark \
  --skip-baseline \
  --workloads sleep,io \
  --order key_hash,partition \
  --num-messages 50000 \
  --num-partitions 8 \
  --strict-completion-monitor on,off \
  --adaptive-concurrency off,on \
  --metrics-port 9091
```

After a long run, re-check recovery semantics with:

```bash
uv run pytest tests/e2e/test_process_recovery.py -q
```

These notes are the minimum evidence trail for the remaining soak / long-running stability checklist item in release-readiness tracking.

For the fixed pass/fail gate and the latest template-aligned evidence package,
use [`docs/operations/soak-restart-evidence.md`](../docs/operations/soak-restart-evidence.md).

## Interpreting TPS vs per-message latency

The JSON summary reports both wall-clock throughput (`throughput_tps`, `total_time_sec`) and per-message timings (`avg_processing_ms`, `p99_processing_ms`). A run can finish much faster (higher TPS, lower total_time_sec) while showing higher per-message latency because:

- **Concurrency vs sojourn time**: TPS reflects how many messages finish per second across all workers. `avg/p99_processing_ms` is the per-message sojourn (queueing + execution). More in-flight work shortens total wall time but lengthens individual wait times.
- **Async run (sample)**: 2788 TPS with ~0.7s total for 2000 msgs, but avg/p99 around 244/345 ms. The async engine holds many messages in flight; each message waits behind others even though the batch completes quickly.
- **Process run (sample)**: 767 TPS with ~2.6s total; per-message latency is higher (avg ~839 ms, p99 ~1620 ms) due to IPC (msgpack + queues), process scheduling, and smaller parallelism than async.
- **Baseline**: Single-threaded; lowest TPS but lowest per-message latency because there is no queueing/backpressure.

When comparing runs, use TPS for throughput and `avg/p99_processing_ms` for user-facing latency. Higher TPS usually means higher queueing latency unless you also reduce work per message.

## py-spy profiling (process mode)

py-spy profiles the process execution engine including all child worker processes.
It uses a self-relaunch pattern: the script re-executes itself under `py-spy record --subprocesses`.

```bash
# Flamegraph (default) — process mode only
uv run python -m benchmarks.run_parallel_benchmark \
  --py-spy --skip-baseline --skip-async

# Speedscope format for interactive exploration
uv run python -m benchmarks.run_parallel_benchmark \
  --py-spy --py-spy-format speedscope --skip-baseline --skip-async

# Chrome trace format (load in chrome://tracing)
uv run python -m benchmarks.run_parallel_benchmark \
  --py-spy --py-spy-format chrometrace --skip-baseline --skip-async

# High sampling rate with native frames
uv run python -m benchmarks.run_parallel_benchmark \
  --py-spy --py-spy-rate 250 --py-spy-native --skip-baseline --skip-async

# Live top view (interactive)
uv run python -m benchmarks.run_parallel_benchmark \
  --py-spy --py-spy-top --skip-baseline --skip-async
```

> **macOS note**: py-spy may require `sudo` due to System Integrity Protection.
> Homebrew/pyenv Python installs typically work without `sudo`.
