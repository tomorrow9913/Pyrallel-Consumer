# Benchmark Runtime Requirements

This document defines the scope and acceptance criteria for `benchmark-runtime`.
The subfeature exists to make baseline, async, and process benchmark runs comparable without changing the core library runtime semantics.
For the preserved Korean source text, see [01-requirements.ko.md](./01-requirements.ko.md).

## 1. Responsibilities

The benchmark runtime must:

- compare the baseline consumer and Pyrallel async/process modes under the same workload, ordering, partition-count, and message-count inputs;
- support both non-interactive CLI execution and an interactive Textual TUI that
  covers the common benchmark workflow without claiming parity with every
  advanced CLI-only matrix or profiling flag;
- persist machine-readable benchmark summaries and optional profiling artifacts;
- document how throughput, total runtime, and per-message latency should be read together;
- keep benchmark-only conveniences such as topic reset, profiling, and metrics exposure outside the production control-plane contract.

## 2. Functional requirements

### 2.1 Benchmark matrix

- The harness must support `sleep`, `cpu`, and `io` workloads.
- The harness must support `key_hash`, `partition`, and `unordered` ordering modes.
- The baseline round, Pyrallel async round, and Pyrallel process round must be individually skippable.
- Pyrallel rounds must support strict-completion-monitor A/B runs (`on`, `off`).
- Pyrallel rounds must support adaptive-concurrency A/B runs (`off`, `on`).

### 2.2 Environment preparation

- Kafka reachability must be checked before the benchmark matrix starts.
- When `--skip-reset` is **not** used, each round must delete/recreate its topic and delete its consumer group before producing messages.
- When `--skip-reset` **is** used, topic existence must be handled lazily by the producer/consumer setup path (`create_topic_if_not_exists` / `ensure_topic_exists`) rather than by a separate up-front validation phase.
- Topic and consumer-group names must remain round-specific so matrix runs do not collide.

### 2.3 Profiling and metrics

- `--profile` must enable yappi session profiling around each executed round.
- `--py-spy` must relaunch the benchmark under `py-spy` for process-mode profiling.
- Process-worker yappi profiling must remain opt-in and documented as unstable.
- Benchmark-side Prometheus exposure must be controlled by `--metrics-port`; `0` disables it.
- Metrics exposure must apply only to Pyrallel rounds. Baseline runs do not boot exporter state.

### 2.4 Outputs

- Every run must emit a console results table.
- Every benchmark invocation must emit a JSON summary, defaulting to `benchmarks/results/<UTC timestamp>.json` when `--json-output` is omitted.
- JSON summaries must preserve the machine-readable fields consumed by the current release-gate evaluator, including per-run metrics observations and derived performance-improvement sections when those are available.
- Optional profiler outputs must be written beneath the configured profile directory.
- The TUI must preserve discoverability of the same runtime options instead of inventing a separate benchmark contract.

## 3. Non-functional requirements

- Benchmark docs must distinguish benchmark-only knobs from production runtime configuration.
- Benchmark docs must distinguish the benchmark harness contract from higher-level operational release policy, while still acknowledging that the emitted JSON artifacts are the inputs to the current release-gate workflow described in `docs/operations/playbooks.md`.
- Docs must warn that profiling overhead invalidates direct TPS comparison with non-profiled runs.
- Docs must warn that higher TPS can coexist with higher average/p99 per-message latency because the harness records per-message sojourn time, not only execution time.
- Docs must avoid stale claims about eager topic validation or automatic exporter startup that are not true in the current code path.

## 4. Input and output boundaries

### Inputs

- Kafka bootstrap servers
- workload/order matrix selections
- message count, key count, partition count, timeout, and skip flags
- profiling and optional metrics-port settings
- benchmark-only process micro-batch override flags

### Outputs

- console status and summary table
- JSON benchmark summary
- machine-readable release-gate evidence embedded in the JSON summary (`metrics_observations`, run-level lag/gap snapshots, and comparison sections such as `performance_improvements`)
- yappi `.prof` files and optional merged worker profiles
- py-spy artifacts (`svg`, `json`, or `txt`, depending on format)
- TUI progress, logs, and results rendering

## 5. Acceptance criteria

This subfeature is acceptable only if:

- the docs make it explicit that baseline, async, and process runs share one harness and one result format;
- reset vs lazy topic creation timing is described correctly for `--skip-reset` on/off behavior;
- metrics-port behavior is described correctly as an opt-out benchmark convenience for Pyrallel runs, not as a baseline-wide side effect;
- the docs describe release-gate evidence as an output consumer of benchmark JSON rather than as ad-hoc console interpretation;
- TPS, total runtime, average latency, and p99 latency are explained as different but complementary signals;
- profiling outputs and non-profiled throughput comparisons are clearly separated.
