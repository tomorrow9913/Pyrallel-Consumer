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
  -> ProcessExecutionEngine.submit()
  -> transport router
  -> worker-specific input Pipe
  -> owner worker process
  -> single completion queue
```

Only the input transport changes in the first slice. Completion aggregation,
control-plane commit decisions, and worker function execution stay where they
already live today.

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

The experiment does **not** claim that worker pipes are the long-term
production default. It determines whether `worker_pipes` deserves deeper
investment as the long-term ordered-parallelism candidate while `shared_queue`
continues to exist as the compatibility/default path.

## Scope

Implement the experiment behind an explicit transport option:

```text
process_transport = shared_queue | worker_pipes
```

The default must remain `shared_queue`.

### Included in the first slice

- worker-specific parent-to-worker unidirectional input pipes,
- stable routing from `WorkItem` identity to a worker channel,
- reuse of the existing single completion queue,
- reuse of parent-side registry and in-flight accounting,
- benchmark support for selecting the transport,
- matrix comparison against the current shared-queue path,
- explicit startup rejection for unsupported transport/config combinations.

### Excluded from the first slice

- work stealing,
- dynamic load balancing,
- ownership migration after worker death,
- worker-specific completion queues,
- completion ingest threads,
- shared-memory ring buffers,
- broker-I/O ownership changes,
- changing the production default,
- broad retry, commit, or control-plane redesign.

## Control-plane invariants

The control plane must remain transport-agnostic.

The experiment must preserve these invariants:

- `BrokerPoller` and `WorkManager` do not know whether process mode uses
  `shared_queue` or `worker_pipes`.
- `BaseExecutionEngine` public surface stays unchanged:
  - `submit(work_item)`
  - `poll_completed_events(batch_limit=1000)`
  - `wait_for_completion(timeout_seconds=None)`
  - `get_in_flight_count()`
  - `get_min_inflight_offset()`
  - `get_runtime_metrics()`
  - `shutdown()`
- `WorkManager` still decides which `WorkItem` instances are safe to execute.
- completion aggregation and offset-commit decisions stay in the parent/control
  plane.
- transport selection must not change ordering guarantees on its own; it only
  changes how already-safe work reaches a worker process.

## Routing contract

The first prototype should reuse the existing logical identity already carried
by `WorkItem`:

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

## Configuration and CLI contract

Add a transport selector to `ProcessConfig`:

```python
transport_mode: Literal["shared_queue", "worker_pipes"] = "shared_queue"
```

Configuration requirements:

- default remains `shared_queue`,
- environment override follows the existing `PROCESS_` naming pattern,
- invalid values fail at config validation time,
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

Benchmark CLI should expose the same choice:

```bash
--process-transport shared_queue|worker_pipes
```

The propagation path should remain explicit:

```text
benchmark CLI
  -> benchmark config builder
  -> KafkaConfig.parallel_consumer.execution.process_config.transport_mode
  -> ProcessExecutionEngine
```

## Unsupported matrix for the first slice

The experiment should prefer explicit rejection over silent fallback.

| Surface | First-slice rule | Why |
| --- | --- | --- |
| `transport_mode=shared_queue` | fully supported | control baseline |
| `transport_mode=worker_pipes` + `batch_size=1` | supported | smallest topology-only experiment |
| `worker_pipes` + timer/demand batching | reject at startup unless fully implemented | avoid mixing batching redesign with transport validation |
| `worker_pipes` + recycle semantics not implemented | reject at startup | silent disable would invalidate benchmark interpretation |
| invalid transport value | config validation failure | keep experiment bounded and observable |

If support widens later, the table should be updated rather than removed.

## Batching stance for the experiment

The experiment is about input topology first, not batching sophistication.

The recommended first slice is:

```text
batch_size = 1
max_batch_wait_ms = 0
```

That choice keeps the transport question isolated from timer-flush and
residence-policy complexity.

If worker pipes later need richer batching, document it as a second slice. Do
not quietly reinterpret:

- `flush_policy="size_or_timer"`
- `flush_policy="demand"`
- `flush_policy="demand_min_residence"`

unless the implementation clearly preserves their existing meaning.

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
- completion events still flow to the existing completion queue,
- parent-side registry events remain meaningful for in-flight accounting.

### Shutdown

- flush buffered submissions before final sentinel delivery,
- send exactly one sentinel per worker input channel in `worker_pipes` mode,
- retain the existing join, terminate, and kill escalation policy,
- drain registry and completion queues before teardown,
- preserve `wait_for_completion()` expectations around prefetched completions
  and already-queued completions.

### Crash, restart, and recycle guardrails

- the first slice may preserve current dead-worker recovery only when it is
  already implementable without ownership migration,
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

- benchmark matrices comparing `shared_queue` vs `worker_pipes`,
- final lag and final gap evidence,
- ordering-validation evidence,
- release-gate evidence that still treats the run as GO/NO-GO on the same
  final correctness criteria,
- transport-specific benchmark metadata so results can be grouped by transport,
- enough runtime metrics or logs to explain rejected/unsupported combinations.
- release-gate summaries that surface the observed `process_transport_mode`
  values so artifact comparisons remain interpretable.

The benchmark report should make these questions easy to answer:

1. Did `worker_pipes` improve partition-wide throughput?
2. Did `worker_pipes` improve key-wide throughput?
3. Did narrow workloads regress beyond acceptable limits?
4. Did final lag or final gap regress from `0/0`?
5. Were any transport/config combinations skipped or rejected explicitly?

## Success criteria

The experiment succeeds only if both performance and correctness stay visible.

### Correctness gates

- ordering validation continues to pass,
- final lag remains `0`,
- final gap remains `0`,
- release-gate verdict remains trustworthy rather than using weakened checks,
- shutdown semantics do not hide incomplete in-flight work.

### Performance gates

- `partition` workloads with width at least `process_count` improve versus
  `shared_queue`,
- `key_hash` workloads with active-key width at least `process_count` improve
  versus `shared_queue`,
- narrow workloads such as `p=1` or `k=1` do not regress severely,
- improvement claims cite benchmark artifacts rather than anecdotal logs.

## Suggested implementation slices

### Slice 1 — bounded transport toggle

- add `transport_mode` config and benchmark CLI plumbing,
- keep `shared_queue` path unchanged,
- add worker-pipe startup and routing for `batch_size=1`,
- keep single completion queue,
- reject unsupported combinations explicitly.

### Slice 2 — evidence and operational hardening

- add benchmark/report metadata for transport selection,
- ensure release-gate and benchmark summaries surface transport mode,
- document any restart/recycle limitations that remain.

### Slice 3 — optional expansion

- only after evidence shows value, evaluate richer batching support, recycle
  parity, or more advanced routing strategies.

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
