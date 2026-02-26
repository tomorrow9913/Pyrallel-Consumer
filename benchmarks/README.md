# Benchmarks

This folder contains the benchmarking and profiling entrypoints for Pyrallel Consumer.

## Scripts
- `run_parallel_benchmark.py`: primary throughput/latency benchmark for baseline, async, and process modes (can optionally enable profiling).
- `profile_benchmark_yappi.py`: legacy profiler runner; equivalent behavior is available via `run_parallel_benchmark.py --profile`.

## Quick start
```bash
# Standard benchmark (no profiling)
uv run python -m benchmarks.run_parallel_benchmark --num-messages 100000 --num-partitions 8

# Enable profiling for all modes (writes .prof files)
uv run python -m benchmarks.run_parallel_benchmark \
  --profile \
  --profile-dir benchmarks/results/profiles \
  --profile-clock wall \
  --profile-top-n 20

# Use CPU-bound workload
uv run python -m benchmarks.run_parallel_benchmark --profile --workload cpu --worker-cpu-iterations 2000

# Use IO-bound workload (sleep-based wait)
uv run python -m benchmarks.run_parallel_benchmark --profile --workload io --worker-io-sleep-ms 5
```

## Key options
- General: `--bootstrap-servers`, `--num-messages`, `--num-keys`, `--num-partitions`, `--topic-prefix`, `--timeout-sec`, `--skip-{baseline,async,process}`, `--skip-reset`.
- Workload selection (applies to baseline/async/process uniformly):
  - `--workload {sleep,cpu,io,all}` (default `sleep`; `all` runs sleep→cpu→io sequentially with suffixed topic/group names).
  - `--worker-sleep-ms`: per-message sleep for `sleep` workload (default 5ms).
  - `--worker-cpu-iterations`: hash loop iterations for `cpu` workload (default 1000).
  - `--worker-io-sleep-ms`: per-message sleep for `io` workload (default 5ms).
- Profiling (yappi):
  - `--profile`: enable profiling (baseline/async only; process mode profiling is disabled by default).
  - `--profile-dir`: directory to write `.prof` files (default `benchmarks/results/profiles`).
  - `--profile-clock {wall,cpu}`: yappi clock (default `wall`).
  - `--profile-top-n`: print top-N functions by total time (0 to skip).
  - `--profile-threads`, `--profile-greenlets`: include threads/async tasks.
  - `--profile-process-workers`: also profile process workers (off by default; may be unstable on some platforms).

## Outputs
- Benchmark results: console table + JSON summary (default `benchmarks/results/<timestamp>.json`).
- Profiling: per-mode `.prof` files under `profile-dir` (e.g., `pyrallel-async.prof`, `pyrallel-process.prof`). Process mode also saves per-worker `.prof` files (suffix `-worker-<pid>.prof`) when profiling is enabled.

## Recent sample results (no profiling, 4 partitions, 2000 msgs, 100 keys)
- Workload `sleep` (5ms): baseline 159.69 TPS; async 2206.35 TPS; process 910.04 TPS.
- Workload `cpu` (500 iterations): baseline 2598.79 TPS; async 1403.07 TPS; process 2072.26 TPS.
- Workload `io` (5ms): baseline 159.89 TPS; async 2797.18 TPS; process 916.60 TPS.

> Note: process mode ran without profiling for stability; profiles (when enabled) cover baseline/async only by default.

## Notes
- Profiling adds overhead; do not compare TPS from profiled runs to non-profiled runs.
- Process worker profiling is enabled when `--profile` is set; per-worker files are saved automatically. Use `uv run snakeviz <prof>` to inspect.
- For clean TPS measurements, keep logging low: `--log-level WARNING` (default). Using `DEBUG` can materially reduce throughput and should only be used for debugging, not performance comparisons.

## Interpreting TPS vs per-message latency

The JSON summary reports both wall-clock throughput (`throughput_tps`, `total_time_sec`) and per-message timings (`avg_processing_ms`, `p99_processing_ms`). A run can finish much faster (higher TPS, lower total_time_sec) while showing higher per-message latency because:

- **Concurrency vs sojourn time**: TPS reflects how many messages finish per second across all workers. `avg/p99_processing_ms` is the per-message sojourn (queueing + execution). More in-flight work shortens total wall time but lengthens individual wait times.
- **Async run (sample)**: 2788 TPS with ~0.7s total for 2000 msgs, but avg/p99 around 244/345 ms. The async engine holds many messages in flight; each message waits behind others even though the batch completes quickly.
- **Process run (sample)**: 767 TPS with ~2.6s total; per-message latency is higher (avg ~839 ms, p99 ~1620 ms) due to IPC (msgpack + queues), process scheduling, and smaller parallelism than async.
- **Baseline**: Single-threaded; lowest TPS but lowest per-message latency because there is no queueing/backpressure.

When comparing runs, use TPS for throughput and `avg/p99_processing_ms` for user-facing latency. Higher TPS usually means higher queueing latency unless you also reduce work per message.
