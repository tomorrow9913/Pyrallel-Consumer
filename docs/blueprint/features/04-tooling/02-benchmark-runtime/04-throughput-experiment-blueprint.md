# Throughput Experiment Blueprint

This document captures the current research and implementation direction for
benchmark-driven throughput work. For the preserved Korean source text, see
[04-throughput-experiment-blueprint.ko.md](./04-throughput-experiment-blueprint.ko.md).

## Goal

Recent benchmark runs show that async and process runs can both lose to the
baseline under small-workload ordered scenarios. The first optimization slice
therefore targets the common control-plane path shared by all engines and
ordering modes, before adding engine-specific parallelism.

## Research frame

- **Span bound:** reduce the common critical path from completion readiness to
  the next useful refill. The immediate suspect is per-completion scheduling
  churn in `WorkManager` plus the post-drain scheduling pass in `BrokerPoller`.
- **Data-movement bound:** reduce queue hops, repeated metadata scans, and
  payload/envelope movement before adding more workers.
- **Queue variability bound:** evaluate p95/p99 completion-to-refill delay and
  queue age, not only mean TPS.
- **Scheduling/locality bound:** prefer dirty-ready or locality-preserving
  scheduling structures over repeated global scans once the batch release path
  is lean.
- **Pipeline semantics bound:** treat the runtime as a bounded dataflow graph
  with explicit buffer and handoff policy.

## Current first slice

The initial experiment is a minimal common-path change:

1. `WorkManager.poll_completed_events()` processes a completion batch and
   performs at most one internal refill scheduling pass for that batch.
2. `BrokerPoller._drain_completion_events_once()` keeps the immediate
   WorkManager refill overlap, then performs the existing broker-level
   post-processing and one refill pass. Full broker-side defer is available as
   an experiment knob but is not the default after the first benchmark showed
   lower partition-order throughput.
3. Locally generated completions, including blocking-timeout and poison/fail-fast
   events, must remain visible to the same WorkManager and BrokerPoller safety
   paths.
4. `WorkManager` remains limited to stale-epoch fencing, local offset-tracker
   bookkeeping, poison-message accounting, and in-flight/order-state release.
   DLQ ledger ordering and final offset completion remain in broker completion
   processing.

## Exclusions

- Do not add speculative windows in this slice. Some workloads care about
  execution order itself, not only output order.
- Do not add completion linger to form larger batches. The completion envelope
  should harvest what is already available on the next pass, up to a bounded
  budget.
- Do not leak async or process engine internals into the control plane.
- Do not move the only refill pass behind broker completion processing unless
  benchmark evidence shows the lost overlap is recovered elsewhere.

## Evidence to collect next

The first profiler pass changed the priority order. In async partition strict-on
profiling, commit work dominated completion-drain work: `_commit_ready_offsets`
and `_commit_offsets` were much larger than `WorkManager.poll_completed_events`
or `WorkManager.schedule`. The next implementation slice should therefore keep
completion release/refill immediate and move Kafka commit work to a dirty
partition and bounded-cadence policy.

- completion drain events per pass
- completion drain duration
- completion-to-refill duration
- schedule calls per completion
- schedule iterations per pass
- refill count per pass
- control-lock hold duration
- commit candidate count and commit interval
- stale completion drop count

## Revised next slice

The next experiment should separate readiness from commit transmission:

1. Workers report completions immediately.
2. `WorkManager` releases in-flight/order state and refills immediately.
3. Broker completion processing marks offsets complete after DLQ safety checks.
4. A commit planner records dirty partitions and commits the smallest
   committable offset on a bounded cadence, with immediate flush on revoke,
   shutdown, and explicit drain boundaries.

Success criteria:

- commit calls per message drop sharply in partition strict-on runs
- completion-to-refill latency does not regress
- rebalance and shutdown commit safety remain unchanged
- p99 processing latency stays within the current envelope

Implementation status:

- `BrokerPoller` now records dirty partitions after broker completion processing
  reports processed completions.
- Non-forced commit attempts run only after
  `commit_debounce_completion_threshold` processed completions or
  `commit_debounce_interval_ms` elapsed time.
- Revoke still uses the synchronous rebalance commit path, and graceful shutdown
  calls `_commit_ready_offsets(force=True)` so pending safe offsets are flushed
  immediately.
- `WorkManager` release/refill remains immediate; the debounce applies only to
  Kafka commit transmission.
- Benchmark final metrics are recorded after `BrokerPoller.stop()` so release
  gate summaries see the forced final commit state rather than the pre-stop
  debounce window.

Initial validation:

- `benchmarks/results/20260424T073444Z.json`: 8000-message async partition
  strict-on run improved to 1869.97 TPS versus 2115.71 TPS baseline, with
  final lag/gap `0/0`.
- `benchmarks/results/20260424T073509Z.json`: 8000-message process partition
  strict-on run reached 1006.73 TPS, with final lag/gap `0/0`.
- This confirms commit cadence was a major common-path fixed cost. Async still
  trails baseline, so the next experiment should profile the remaining
  backpressure/refill/commit overhead before moving to process-specific IPC
  work.

## py-spy findings from the manual run

The user-run macOS `py-spy` capture produced the expected artifacts:

- Process run:
  `benchmarks/results/pyspy/codex-commit-cadence/process-partition-strict.speedscope.json`
  and `benchmarks/results/20260424T070748Z.json`.
- Async run:
  `benchmarks/results/pyspy/codex-commit-cadence/async-partition-strict.speedscope.json`
  and `benchmarks/results/20260424T070807Z.json`.

The process partition strict-on result processed 8000 messages at 492.81 TPS,
with p99 processing latency 7.78 ms. The subprocess-aware profile shows the
dominant sampled path inside worker processes, not the broker commit path:
`ProcessExecutionEngine._worker_loop` accounts for about 60.8% of aggregate
sampled time, `multiprocessing.Queue.get` accounts for about 67.6%, and
`multiprocessing.Connection.recv_bytes` accounts for about 15.3%. The actual
benchmark worker function is only about 1.1%. Broker/control frames are small:
`BrokerPoller._drain_completion_events_once` about 0.30%,
`WorkManager.poll_completed_events` about 0.27%, `_commit_ready_offsets` about
0.23%, and `_commit_offsets` about 0.09%.

The async partition strict-on result processed 8000 messages at 1083.67 TPS,
with p99 processing latency 3.04 ms. `py-spy` mostly samples executor threads
used by blocking Kafka operations, so it is less useful than the yappi wall
profile for attributing async awaited commit time. The earlier yappi profile
therefore remains the stronger evidence for async: `_commit_ready_offsets` and
`_commit_offsets` dominate the common path there.

Updated interpretation:

- For async partition strict-on, run dirty-partition commit debounce first.
- For process partition strict-on, do not expect more processes or generic
  completion batching to help a single-partition strict-order workload. The
  effective concurrency is one item, while the process runtime pays queue/IPC
  overhead and leaves most workers blocked.
- Keep WorkManager release/refill immediate. The previous broker-side defer
  experiment already showed that losing refill overlap hurts throughput.
- Treat process strict-partition work as a separate second track: consider an
  effective-concurrency-aware async/inline fast path, single-worker affinity, or
  lower-overhead transport before adding more scheduling machinery.

## Post-compaction experiment plan

Use this section as the handoff after context compaction. The current evidence
does not justify more worker/process tuning first. It points to commit cadence
and control-plane fixed cost.

### Experiment 1: dirty-partition commit debounce

Hypothesis:

> Partition strict-on loses to the baseline primarily because completion-driven
> commit work runs almost once per completed item.

Implementation direction:

- Keep completion ingestion and WorkManager release/refill immediate.
- Add a dirty partition set when broker completion processing marks offsets
  complete.
- Commit dirty partitions on a bounded cadence, for example `N completions`,
  `M milliseconds`, revoke, shutdown, or explicit drain completion.
- Keep revoke and shutdown as immediate flush boundaries.
- Preserve `get_committable_high_water_mark(min_inflight)` as the source of
  commit safety.

Measurement:

- commit calls per message
- `_commit_ready_offsets` wall time
- `_commit_offsets` wall time
- completion-to-refill latency
- final lag/gap count
- ordering validation
- baseline / async / process TPS under the same partition strict-on workload

Expected outcome:

- async partition strict-on should improve first because yappi showed commit
  work dominating there.
- process partition strict-on may improve less if IPC remains dominant, but it
  should not regress if refill overlap is preserved.

### Experiment 1.1: caller-side commit/backpressure gating

Run this immediately after commit debounce. The profile after debounce still
showed tens of thousands of helper calls with far fewer real broker commits, so
the next low-risk slice is to skip unchanged control-plane checks before they
touch broker APIs.

Implementation direction:

- Call `_commit_ready_offsets()` only when dirty partitions exist and either the
  debounce cadence is open or idle force flush is safe.
- Preserve pending-DLQ as a force-commit veto.
- Keep shutdown and revoke force paths outside the caller-side gate.
- In `_check_backpressure()`, skip broker `assignment()/pause()/resume()` calls
  when the consumer is not paused, adaptive controllers are disabled, and the
  current load cannot cross a pause threshold.
- If the consumer is paused, always keep the resume path open.

Success criteria:

- fewer `_commit_ready_offsets` calls per item
- fewer Kafka assignment/pause/resume calls in unchanged unpaused loops
- no delayed timer commit, idle flush, DLQ, shutdown, or resume regression

### Experiment 1.2: process effective-width cap sweep

The process strict single-partition profile showed effective WIP close to one
while many workers waited on multiprocessing queues. Treat this first as an
explicit benchmark variable rather than a control-plane policy.

Implementation direction:

- Add a benchmark-only `--process-count` override.
- Sweep process counts `{1, 2, 4, 8}` across partition counts and active key
  counts.
- Keep `BrokerPoller` and `WorkManager` unaware of `process_count`; the override
  belongs to benchmark/runtime config before engine construction.

Success criteria:

- strict `partition` with one assigned partition should prefer `process_count`
  `1` or `2`.
- `key_hash` should scale only as active keys and control-plane overhead allow.
- the result should justify, or reject, a later automatic advisor/policy.

### Experiment 1.3: dedicated broker I/O bridge design

Design this before implementing. A bridge can remove repeated `asyncio.to_thread`
submissions, but it also becomes the owner of Kafka callback and rebalance
threading semantics.

Design constraints:

- Add a `BrokerConsumerIO`-style boundary for consume, commit, committed,
  assignment snapshots, pause, resume, and close.
- The broker thread may own Kafka calls, but event-loop state such as
  `OffsetTracker`, `WorkManager`, dirty partitions, and message cache must remain
  event-loop owned.
- Rebalance callbacks must marshal control-plane mutations back to the event
  loop; do not mutate state from the broker thread.
- Do not enqueue bridge work from a rebalance callback and wait on the same
  bridge thread.
- Check synchronous commit per-partition errors.
- Keep DLQ producer flush off the consumer bridge unless it is deliberately
  modeled as a separate stage.

### Experiment 2: completion ingest stage

Run this only after Experiment 1 or if profiler evidence shows completion
transport dominates. The SEDA interpretation is a dedicated completion ingest
stage between engine transport and scheduler-side release.

Hypothesis:

> A dedicated completion ingest stage reduces p99 completion turnaround by
> isolating foreign transport reads from the main scheduling loop.

Constraints:

- Do not add sender-side waiting to form completion batches.
- Drain already available completions opportunistically.
- Preserve local completion events such as blocking-timeout and poison/fail-fast.
- Preserve the WorkManager/BrokerCompletionSupport safety boundary.

### Experiment 3: edge-triggered scheduler

Run this after commit cadence is measured. The scheduler should respond to
frontier changes instead of every completion.

Triggers:

- ready count changes from `0` to `>0`
- idle capacity changes from `0` to `>0`
- imbalance score exceeds a threshold
- failsafe periodic tick

Success criteria:

- lower schedule calls per item
- no completion-to-refill regression
- no ordering, rebalance, or DLQ regression

## py-spy capture recipe

On macOS, `py-spy` needs root privileges. Run the following from a normal
terminal, not from Codex, then share the generated files under
`benchmarks/results/pyspy/codex-commit-cadence/`.

### Process partition strict-on subprocess profile

```bash
cd /Users/mqueue/Desktop/project/Pyrallel-Consumer
mkdir -p benchmarks/results/pyspy/codex-commit-cadence
sudo .venv/bin/py-spy record \
  --subprocesses \
  --format speedscope \
  --output benchmarks/results/pyspy/codex-commit-cadence/process-partition-strict.speedscope.json \
  --rate 200 \
  -- \
  .venv/bin/python -m benchmarks.run_parallel_benchmark \
    --num-messages 8000 \
    --num-keys 8000 \
    --num-partitions 1 \
    --workloads io \
    --order partition \
    --strict-completion-monitor on \
    --adaptive-concurrency off \
    --worker-io-sleep-ms 0.3 \
    --process-batch-size 1 \
    --process-max-batch-wait-ms 0 \
    --metrics-port 0 \
    --timeout-sec 180 \
    --log-level WARNING \
    --topic-prefix codex-pyspy-process-partition \
    --skip-baseline \
    --skip-async
```

### Async partition strict-on control profile

```bash
cd /Users/mqueue/Desktop/project/Pyrallel-Consumer
mkdir -p benchmarks/results/pyspy/codex-commit-cadence
sudo .venv/bin/py-spy record \
  --format speedscope \
  --output benchmarks/results/pyspy/codex-commit-cadence/async-partition-strict.speedscope.json \
  --rate 200 \
  -- \
  .venv/bin/python -m benchmarks.run_parallel_benchmark \
    --num-messages 8000 \
    --num-keys 8000 \
    --num-partitions 1 \
    --workloads io \
    --order partition \
    --strict-completion-monitor on \
    --adaptive-concurrency off \
    --worker-io-sleep-ms 0.3 \
    --process-batch-size 1 \
    --process-max-batch-wait-ms 0 \
    --metrics-port 0 \
    --timeout-sec 150 \
    --log-level WARNING \
    --topic-prefix codex-pyspy-async-partition \
    --skip-baseline \
    --skip-process
```

After running, also keep the benchmark JSON files printed by the runner. Codex
should analyze both the speedscope files and the matching JSON summaries.

## Foundational sources

- Brent, "The Parallel Evaluation of General Arithmetic Expressions",
  Communications of the ACM, 1974.
- Blumofe and Leiserson, "Scheduling Multithreaded Computations by Work
  Stealing", Journal of the ACM, 1999.
- Williams, Waterman, and Patterson, "Roofline: An Insightful Visual Performance
  Model for Multicore Architectures", Communications of the ACM, 2009.
- Little, "A Proof for the Queuing Formula: L = lambda W", Operations Research,
  1961.
- Dean and Barroso, "The Tail at Scale", Communications of the ACM, 2013.
- Lee and Messerschmitt, "Synchronous Data Flow", Proceedings of the IEEE, 1987.
