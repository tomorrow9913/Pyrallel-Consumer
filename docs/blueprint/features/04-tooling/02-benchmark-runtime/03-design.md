# Benchmark Runtime Design

This document fixes the concrete benchmark CLI/TUI contract, current workload semantics, and interpretation rules that should be kept aligned with the implementation.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## 1. Canonical CLI surface

| Option | Current behavior |
| --- | --- |
| `--bootstrap-servers` | broker address used for connectivity checks, reset helpers, producer setup, and consumer setup |
| `--num-messages` | target message count per round |
| `--num-keys` | key cardinality used by the producer when generating ordered test traffic |
| `--num-partitions` | partition count used for round topic creation |
| `--topic-prefix` | base prefix for all round-specific topic names |
| `--baseline-group` / `--async-group` / `--process-group` | base consumer-group prefixes for each execution family |
| `--json-output` | explicit JSON summary path; defaults to `benchmarks/results/<UTC timestamp>.json` |
| `--skip-baseline` / `--skip-async` / `--skip-process` | remove the selected execution family from the matrix |
| `--skip-reset` | skip topic/group deletion-recreation and rely on lazy topic creation during producer/consumer setup |
| `--timeout-sec` | per-Pyrallel-run timeout guard |
| `--log-level` | benchmark logger verbosity; `WARNING` is the default for cleaner measurements |
| `--workloads` | comma-separated subset of `sleep,cpu,io` |
| `--order` | comma-separated subset of `key_hash,partition,unordered` |
| `--strict-completion-monitor` | comma-separated subset of `on,off`; expands the Pyrallel run matrix |
| `--adaptive-concurrency` | comma-separated subset of `off,on`; expands the Pyrallel run matrix |
| `--worker-sleep-ms` | blocking sleep used by the `sleep` workload |
| `--worker-cpu-iterations` | hashing loop iterations used by the `cpu` workload |
| `--worker-io-sleep-ms` | async-style wait used by the `io` workload |
| `--process-batch-size` | benchmark-only override for process micro-batch size |
| `--process-max-batch-wait-ms` | benchmark-only override for process micro-batch wait |
| `--process-flush-policy` | benchmark-only override for process flush policy |
| `--process-demand-flush-min-residence-ms` | benchmark-only override for the demand-min-residence guard |
| `--metrics-port` | exposes Prometheus metrics for Pyrallel runs on the chosen host port; `0` disables benchmark-side exposure |
| `--profile` and related `--profile-*` flags | wrap each executed round in yappi profiling |
| `--py-spy` and related `--py-spy-*` flags | relaunch under py-spy to capture process-mode activity, including subprocesses |

## 2. Naming contract

For each workload/order combination, the runner synthesizes:

- a topic name: `<topic-prefix>-<workload>-<order>-<mode>` plus optional `-strict-on|off` and `-adaptive-on|off` suffixes for Pyrallel rounds;
- a run name: `<workload>-<order>-baseline` or `<workload>-<order>-pyrallel-async|process` with the same optional suffixes;
- a consumer group: the family-specific group prefix with the same workload/order and optional suffixes.

These names are part of the artifact contract because they appear in console output and JSON summaries.

## 3. Workload semantics

| Workload | Implementation path | What it simulates | Typical interpretation |
| --- | --- | --- | --- |
| `sleep` | `time.sleep()` in sync/process workers, `asyncio.sleep()` in async worker | externally blocked latency with little CPU work | highlights how much concurrency hides waiting time |
| `cpu` | repeated SHA-256 hashing loop | CPU-bound per-message work | shows when process workers can outperform async mode |
| `io` | `time.sleep()` for baseline/process and `asyncio.sleep()` for async | async-friendly I/O wait | highlights async mode's ability to overlap wait-heavy work |

The workload selector changes only the worker function cost model; it does not alter ordering or offset semantics.

## 4. Round execution details

### 4.1 Reset and topic creation

- With reset enabled, the runner recreates the topic before each round and passes `ensure_topic_exists=False` into downstream helpers.
- With `--skip-reset`, the producer helper may create the topic before sending messages, and the Pyrallel harness may also create it on the consumer side if needed.
- As a result, topic existence is validated lazily during round setup, not during argument parsing or TUI form submission.

### 4.2 Metrics exposure

- `--metrics-port` is normalized so values `<= 0` disable benchmark metrics exposure.
- Baseline runs ignore the metrics port entirely.
- Pyrallel runs pass the selected port into `run_pyrallel_consumer_test()`, which both enables `KafkaConfig.metrics` and acquires a reusable `PrometheusMetricsExporter` for the benchmark process.
- The benchmark harness records selected observability evidence (`consumer_parallel_lag`, `consumer_gap_count`) into JSON summaries, but it does not serialize the full `RuntimeSnapshot` API documented under `04-tooling/01-observability-metrics`.
- Therefore the benchmark harness provides a convenience default (`9091`), but exporter startup still depends on actually running a Pyrallel round.

### 4.3 Profiling modes

- `--profile` wraps each executed round in yappi and writes `<run-name>.prof` files.
- `--profile-process-workers` remains an explicit opt-in and is still documented as unstable.
- `--py-spy` is separate from yappi and works by self-relaunching the benchmark script under py-spy with subprocess capture.

## 5. Output contract

| Output | Contract |
| --- | --- |
| Console logs | producer progress, reset notices, optional profiling notices, and the final table |
| Final table | one row per executed run with run name, type, order, topic, processed message count, TPS, average ms, and p99 ms |
| JSON summary | structured benchmark results plus the raw option map used for the invocation |
| JSON release-gate evidence | the same JSON payload carries `metrics_observations`, final lag/gap fields, and `performance_improvements` so repeated artifacts can be evaluated by `benchmarks.release_gate` without scraping console output |
| yappi artifacts | `.prof` files under the configured profile directory |
| py-spy artifacts | format-specific files under the configured py-spy output directory |
| TUI output | live progress, parsed status/logs, and a rendered results summary for the common benchmark workflow; some advanced matrix/profiling flags remain CLI-only |

## 6. Interpretation rules

- **TPS / total runtime** answer how fast the whole batch completed.
- **Average / p99 processing ms** answer how long an individual message spent in the system from submission to completion.
- **Release-gate reading** should come from repeated JSON artifacts evaluated with `benchmarks.release_gate`; the console table is a human summary, not the release verdict source.
- A run can improve throughput while worsening per-message latency because more concurrency increases queueing/sojourn time.
- Process mode can win on CPU-heavy work yet still show worse per-message latency because IPC and scheduling overhead are part of the measured path.
- Profiled runs must not be compared directly with non-profiled TPS measurements.
- `strict-completion-monitor=on` is the primary release-oriented comparison slice; `off` remains a benchmarking comparison axis, not a substitute for strict-on release evidence.
- `adaptive-concurrency=on` changes the control-plane live in-flight limit for Pyrallel runs; it is a benchmark axis for runtime tuning behavior, not a process-count or semaphore-resize benchmark.
- Adaptive backpressure is currently a runtime observability/configuration surface, while `adaptive-concurrency=on|off` is a distinct benchmark matrix axis for the control-plane live-limit policy.
- Logging above `WARNING` can materially perturb throughput measurements and should be treated as a debugging mode, not a benchmark baseline.
- Tiny process + partition benchmarks can be dominated by default batching; compare benchmark-only overrides before drawing conclusions about library defaults.
