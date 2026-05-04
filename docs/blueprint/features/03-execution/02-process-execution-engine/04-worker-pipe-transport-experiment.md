# Worker Pipe Transport Experiment

This document is the canonical English blueprint for the worker-pipe transport
experiment. It is intentionally written as an implementation-facing experiment
plan that connects to the long-term direction of the process execution engine,
not as a throwaway experiment note. The team should be able to build the slice
first and later replace assumptions with measured outcomes. For the preserved
Korean source text, see
[04-worker-pipe-transport-experiment.ko.md](./04-worker-pipe-transport-experiment.ko.md).

## Why this document exists

This is not a production architecture commitment. This is a bounded experiment
plan for one concrete question:

> Is the shared `multiprocessing.Queue` input topology one of the fixed-cost
> bottlenecks that suppress ordered process-mode throughput?

The document should read like a blueprint before implementation and become a
measured contract after implementation. That means:

- implementation boundaries should be explicit up front,
- unsupported combinations should be rejected explicitly,
- success should be judged by benchmark and release-gate evidence rather than
  by intuition.

## Relationship to the long-term direction

This experiment is the smallest bounded slice of the broader process-engine
direction:

- `WorkManager` already owns ordered virtual queues and decides eligibility.
- the async engine already preserves that separation by spreading safe-to-run
  work immediately with `create_task()`.
- the process engine should move toward a worker-affine dispatch topology that
  avoids re-merging all safe-to-run work into one shared input queue.

## Current shape and experimental shape

Current process runtime:

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit()
  -> shared multiprocessing.Queue
  -> N process workers competing on the same input
  -> single completion queue
```

Experimental runtime:

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit() / submit_batch()
  -> transport router or RouteBatch route lease
  -> worker-specific input Pipe
  -> owner worker process sequential loop
  -> item completion or BatchCompletion envelope
  -> parent-side item completion surface
```

The first worker-pipe slice changed input transport. The route-batch slice also
changes the worker-to-parent IPC envelope for route batches, while preserving the
parent-facing item completion surface. Control-plane commit decisions and
worker function semantics stay where they already live today.

## Evidence behind the experiment

Current benchmark and py-spy evidence suggest that ordered partition workloads
are dominated by shared-input receive paths rather than by the actual worker
function. The worker-side hot path is concentrated in frames such as:

- `_receive_task_payload`
- `multiprocessing.Queue.get`
- `synchronize.__enter__`
- `connection.recv_bytes`

while `_io_worker_process` occupies a much smaller share. That is why this
experiment prioritizes input dispatch topology before completion aggregation.

## Experimental hypothesis

The first slice validates one narrow hypothesis:

> If ordered process-mode throughput is losing useful parallelism at the shared
> input queue boundary, replacing that shared queue with worker-specific input
> channels plus stable routing should improve throughput for partition-wide or
> key-wide workloads without breaking ordering, final lag, or release-gate
> correctness.

Benchmark evidence has promoted worker pipes from experiment to the live process
transport profile. `shared_queue` remains historical context only, not a runtime
selector or performance path to tune.

## Scope

Implement the experiment as the worker-pipe process transport profile:

```text
process_transport = worker_pipes
```

The live process transport is `worker_pipes`.

### Implemented worker-pipe scope

- worker-specific parent-to-worker unidirectional input pipes,
- stable routing from `WorkItem` identity to a worker channel,
- route-batch dispatch for same-route `WorkItem` groups when explicitly enabled,
- internal `RouteBatch` and `BatchCompletion` wire envelopes,
- parent-side expansion back to item-level `CompletionEvent` instances,
- reuse of the existing single completion queue,
- reuse of parent-side registry and in-flight accounting,
- benchmark support for selecting process `route_batch_size`,
- comparison against retained historical shared-queue artifacts when needed,
- explicit startup rejection for unsupported transport/config combinations.

### Excluded from the first slice

- work stealing,
- dynamic load balancing,
- ownership migration after worker death,
- worker-specific completion queues,
- completion ingest threads,
- shared-memory ring buffers,
- broker-I/O ownership changes,
- broad retry, commit, or control-plane redesign.

## Control-plane invariants

The control plane must remain transport-agnostic.

The experiment must preserve these invariants:

- `BrokerPoller` and `WorkManager` do not know the process IPC topology; they
  only receive the resolved route-batch size and engine capability flags.
- `BaseExecutionEngine` public surface remains explicit and stable:
  - `submit(work_item)`
  - `submit_batch(work_items)`
  - `poll_completed_events(batch_limit=1000)`
  - `wait_for_completion(timeout_seconds=None)`
  - `get_in_flight_count()`
  - `get_runtime_metrics()`
  - `shutdown()`
- `WorkManager` still decides which `WorkItem` instances are safe to execute.
- completion aggregation and offset-commit decisions stay in the parent/control
  plane. `BatchCompletion` is an internal IPC envelope, not a public commit
  unit.
- minimum in-flight offset for commit clamping is computed from the
  control-plane `WorkManager` dispatch ledger; any engine-level
  `get_min_inflight_offset()` hook is compatibility-only private state.
- transport selection must not change ordering guarantees on its own; it only
  changes how already-safe work reaches a worker process.

## Routing contract

The transport reuses the existing logical identity already carried by
`WorkItem`:

```text
route_identity = (work_item.tp.topic, work_item.tp.partition, work_item.key)
```

This is not a new process-only hint. It is the same logical queue identity that
`WorkManager` uses before the async and process engines diverge. The async engine
can ignore it and call `create_task()` directly; the worker-pipe process
transport hashes it to select a worker input channel.

Routing rules:

- use a stable hash, not Python built-in `hash()`,
- the same identity must map to the same worker index while `process_count`
  stays constant,
- mapping may change when `process_count` changes,
- `unordered` mode may use a different policy, but the choice must be
  documented and benchmark interpretation must call it out explicitly,
- crash-time ownership migration is out of scope for the first slice.
- ordered modes prefer sticky routing and affinity preservation, not stealing.
- `PARTITION` route batches use `(topic, partition)`.
- `KEY_HASH` route batches use `(topic, partition, key)`.

## Configuration and CLI contract

Process transport is not user-selectable. `ProcessConfig` owns process-mode
route batching:

```python
route_batch_size: int = 64
```

Keep the generic execution route-batch default item-level:

```python
route_batch_size: int = 1
```

Configuration requirements:

- process-mode route-batch override follows the `PROCESS_ROUTE_BATCH_SIZE`
  environment naming pattern,
- invalid route-batch values fail at config validation time,
- existing keys keep their meaning:
  - `process_count`
  - `queue_size`
  - `require_picklable_worker`
  - `batch_size`
  - `max_batch_wait_ms`
  - `flush_policy`
  - `demand_flush_min_residence_ms`
  - `msgpack_max_bytes`
  - `max_tasks_per_child`
  - `recycle_jitter_ms`

Benchmark CLI should expose process route-batch sizing, not transport selection:

```bash
--process-route-batch-size 1|8|32|64|128
```

The propagation path should remain explicit:

```text
benchmark CLI
  -> benchmark config builder
  -> KafkaConfig.parallel_consumer.execution.process_config.route_batch_size
  -> resolve_work_manager_route_batch_size(config.parallel_consumer)
  -> ProcessExecutionEngine
```

## Unsupported matrix for the first slice

The experiment should prefer explicit rejection over silent fallback.

| Surface | Rule | Why |
| --- | --- | --- |
| `transport_mode=worker_pipes` + `route_batch_size=1` | supported | unbatched worker-affine path |
| `transport_mode=worker_pipes` + `route_batch_size>1` | supported for same-route leases when the engine advertises ordered batch capability | IPC amortization experiment |
| ordered mode + engine without `supports_ordered_route_batch` | effective route batch size `1` | fallback `submit_batch()` is not an ordered sequential executor |
| process micro-batch flags | keep existing meaning; do not reinterpret as route batching | keep `ProcessConfig.batch_size` distinct from `route_batch_size` |
| `worker_pipes` + recycle semantics not implemented | reject at startup | silent disable would invalidate benchmark interpretation |

If support widens later, the table should be updated rather than removed.

## Route-batch stance for the experiment

Route batching is deliberately separate from process micro-batching:

- `ProcessConfig.batch_size` controls the existing process payload accumulator
  semantics.
- `ProcessConfig.route_batch_size` controls how many same-route `WorkItem`
  instances `WorkManager` may lease for one process execution-engine call.
- `ProcessConfig.route_batch_size=64` is the default process profile used to amortize
  parent-to-worker and worker-to-parent IPC.
- ordered modes still resolve to an effective batch size of `1` unless the
  execution engine advertises ordered route-batch capability.

Do not quietly reinterpret:

- `flush_policy="size_or_timer"`
- `flush_policy="demand"`
- `flush_policy="demand_min_residence"`

as route-batch semantics.

## Worker lifecycle and shutdown contract

Observed lifecycle behavior should remain as close as possible to the current
process engine.

### Startup

- start `process_count` workers,
- track worker index and PID,
- keep worker logging setup compatible with current behavior,
- keep `require_picklable_worker` validation transport-independent.

### Runtime

- each worker blocks on its own input channel,
- each worker decodes the same payload envelope shape it needs to execute,
- route-batch workers execute batch items sequentially and stop at the first
  item failure,
- normal route-batch completions flow as one `BatchCompletion` envelope and are
  expanded by the parent,
- parent-side registry events remain meaningful for in-flight accounting.

### Shutdown

- flush buffered submissions before final sentinel delivery,
- send exactly one sentinel per worker input channel in `worker_pipes` mode,
- retain the existing join, terminate, and kill escalation policy,
- drain registry and completion queues before teardown,
- preserve `wait_for_completion()` expectations around prefetched completions
  and already-queued completions.

#### Shutdown completion-preservation contract

Shutdown completion preservation is a control-plane boundary contract, not a
transport-specific retry policy. During graceful shutdown, the process engine
may preserve already-visible real completions by moving them into the same
prefetched completion path that backs `wait_for_completion()` and
`poll_completed_events()`. `WorkManager` and `BrokerPoller` must consume those
events as normal completion events, including epoch/rebalance fencing, offset
commit advancement, and DLQ handling for failure completions.

Residual work without an already-visible real completion remains
diagnostic-only in this experiment. Shutdown cleanup must not synthesize failure
completion events, must not publish DLQ records, and must not make commit
decisions based only on teardown timing. Any future policy that turns shutdown
residuals into terminal outcomes must be designed as an explicit control-plane
contract change, not as a worker-pipe transport side effect.

Shutdown drain logs and metrics should be interpreted as diagnostic evidence
only. Pre-join and post-join drain counts explain how many registry and
completion events were reconciled before local cleanup; stable-empty post-join
passes explain that no immediately visible IPC remained within the bounded
window. They are not a retry ledger, an audit log for commit safety, or evidence
that residual work failed. Commit, DLQ, and rebalance outcomes continue to be
derived only from normal completion handling in the control plane.

### Crash, restart, and recycle guardrails

- the first slice may preserve current dead-worker recovery only when it is
  already implementable without ownership migration,
- unstarted route-batch tails must remain recoverable after a worker start
  event or live-worker failure,
- worker restart policy must be documented per transport,
- recycle semantics (`max_tasks_per_child`, `recycle_jitter_ms`) must either be
  preserved or rejected explicitly in `worker_pipes` mode,
- do not imply that crash behavior is “equivalent” unless the implementation
  proves it.

## Ordered versus unordered direction

This experiment is about ordered-parallelism preservation. That means:

- ordered modes use sticky routing and affinity preservation as the default
  design rule,
- work stealing and dynamic balancing belong to unordered mode or later hybrid
  research,
- the experiment should not be expanded into a stealing implementation plan.

## Observability and evidence contract

The experiment is incomplete without evidence.

At minimum, implementation and evaluation should preserve or produce:

- benchmark comparisons against retained historical shared-queue artifacts when
  a migration decision needs that evidence,
- final lag and final gap evidence,
- ordering-validation evidence,
- release-gate evidence that still treats the run as GO/NO-GO on the same
  final correctness criteria,
- benchmark metadata that records the observed process transport as
  `worker_pipes`,
- route-batch metadata and IPC ratios (`route_batch_size`,
  `items_per_input_ipc`, `items_per_completion_ipc`,
  `route_batch_size_avg`, `route_batch_size_max`),
- enough runtime metrics or logs to explain rejected/unsupported combinations.
- release-gate summaries that surface the observed `process_transport_mode`
  values so retained artifact comparisons remain interpretable.

The benchmark report should make these questions easy to answer:

1. Did `worker_pipes` improve partition-wide throughput?
2. Did `worker_pipes` improve key-wide throughput?
3. Did narrow workloads regress beyond acceptable limits?
4. Did final lag or final gap regress from `0/0`?
5. Were any route-batch/config combinations skipped or rejected explicitly?

## Success criteria

The experiment succeeds only if both performance and correctness stay visible.

### Correctness gates

- ordering validation continues to pass,
- final lag remains `0`,
- final gap remains `0`,
- release-gate verdict remains trustworthy rather than using weakened checks,
- shutdown semantics do not hide incomplete in-flight work.

### Performance gates

- `partition` workloads should remain at least comparable to the baseline
  sequential consumer and ideally exceed it through fixed-cost amortization,
- `key_hash` workloads with active-key width above process count should exceed
  the baseline sequential consumer by a large margin,
- narrow workloads such as `p=1` or `k=1` do not regress severely,
- improvement claims cite benchmark artifacts rather than anecdotal logs.

## Suggested implementation slices

### Slice 1 — worker-pipe process transport

- make `worker_pipes` the process transport,
- remove live `shared_queue` config and benchmark CLI plumbing,
- add worker-pipe startup and item-level routing for the unbatched path,
- keep single completion queue,
- reject unsupported combinations explicitly.

### Slice 2 — route-batch contract and lease

- add `submit_batch()` and ordered batch capability gates,
- lease same-route items from WorkManager when safe,
- keep ordered modes at effective batch size `1` unless the engine is capable.

### Slice 3 — worker-pipes route-batch dispatch

- send one route-batch payload over the selected worker pipe,
- keep pending tails recoverable,
- run batch items sequentially in the worker.

### Slice 4 — batch completion envelope

- emit `BatchCompletion` for the executed prefix,
- expand to item completions in the parent,
- dedupe legacy and batch completion overlap with a bounded cache.

### Slice 5 — benchmark and metric evidence

- add `--process-route-batch-size`,
- expose nullable route-batch IPC metrics in benchmark JSON,
- keep performance claims tied to stored benchmark artifacts.

## Non-goals

This document does not approve:

- changing the default process transport,
- solving hot-key skew in the same slice,
- redesigning commit logic,
- adding worker stealing or migration,
- claiming parity for unsupported lifecycle combinations,
- widening scope from “input topology experiment” to “full process-engine
  rewrite”.

## Follow-up questions after implementation

When the experiment lands, revise this document with measured answers to these
questions:

1. Which workload shapes improved, and by how much?
2. Which unsupported combinations should remain rejected?
3. Did shutdown, crash recovery, or restart semantics diverge by transport?
4. Does the evidence justify a second-slice investment?
5. Should `worker_pipes` stay experimental, graduate, or be abandoned?
