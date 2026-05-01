# Process Execution Engine Requirements

This document is the canonical English requirements summary for the
process-execution-engine subfeature. For the preserved Korean source text, see
[01-requirements.ko.md](./01-requirements.ko.md).

## Directional requirement

The process execution engine must evolve beyond a generic shared-queue process
pool. Its long-term direction is to preserve the ordered virtual-queue identity
that `WorkManager` already computes before work crosses the process boundary.

## Required background

- `WorkManager` already owns partition/key virtual queues and decides ordering
  plus eligibility.
- The async engine spreads safe-to-run work immediately via `create_task()`
  instead of re-merging all work into one input queue.
- The compatibility `shared_queue` process path sends submitted work through one
  shared `multiprocessing.Queue`, so all workers compete on the same input
  source.
- Benchmark and py-spy evidence point at input dispatch topology as a higher
  priority improvement target than completion aggregation.

## Mandatory requirements

- `shared_queue` remains the compatibility/default path.
- `worker_pipes` becomes the ordering-preserving parallelism direction and a
  future default candidate.
- `WorkManager` and `BrokerPoller` remain transport-agnostic.
- `BaseExecutionEngine.submit(work_item)` remains unchanged.
- `BaseExecutionEngine.submit_batch(work_items)` is part of the engine contract.
  The fallback must preserve item semantics by calling `submit()` in order.
- Partial batch acceptance must be explicit: an engine either accepts the whole
  batch, accepts nothing for a generic exception, or raises `BatchSubmitError`
  with the accepted prefix count.
- Ordered route batching is allowed only for engines that advertise
  `supports_ordered_route_batch=True`; otherwise `KEY_HASH` and `PARTITION`
  modes use an effective route batch size of `1`.
- `ExecutionConfig.route_batch_size` defaults to `1` and is an explicit
  experiment/benchmark knob rather than a production default change.
- The process engine chooses a worker channel internally by route identity.
- Ordered modes favor sticky routing and affinity preservation over stealing.
- Completion aggregation remains parent-owned. `worker_pipes` may reduce
  worker-to-parent IPC with internal `BatchCompletion` envelopes, but the public
  completion surface remains item-level `CompletionEvent` instances.
- Registry, retry, DLQ, commit, and recovery accounting remain item-level; route
  batches are transport envelopes, not correctness units.
- Config, batching, `wait_for_completion()`, shutdown, recycle, and metrics
  surface must be documented explicitly.

## Route-batch acceptance requirements

For the explicit `worker_pipes` route-batch path:

- same-route batches must not mix route identities;
- `PARTITION` routing groups by `(topic, partition)`;
- `KEY_HASH` routing groups by `(topic, partition, key)`;
- workers execute a route batch sequentially and stop after the first item
  failure;
- unstarted tail items must be surfaced for recovery or requeue, never silently
  dropped;
- fatal worker exits must flush the completed prefix before the worker exits;
- duplicate item completions from mixed legacy/batch paths must be suppressed by
  a bounded parent-side cache;
- malformed or oversized msgpack payloads must fail visibly before they can be
  interpreted as empty work.
