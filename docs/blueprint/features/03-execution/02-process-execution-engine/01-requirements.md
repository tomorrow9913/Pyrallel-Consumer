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
- The current process engine still sends submitted work through one shared
  `multiprocessing.Queue`, so all workers compete on the same input source.
- Benchmark and py-spy evidence point at input dispatch topology as a higher
  priority improvement target than completion aggregation.

## Mandatory requirements

- `shared_queue` remains the compatibility/default path.
- `worker_pipes` becomes the ordering-preserving parallelism direction and a
  future default candidate.
- `WorkManager` and `BrokerPoller` remain transport-agnostic.
- `BaseExecutionEngine.submit(work_item)` remains unchanged.
- The process engine chooses a worker channel internally by route identity.
- Ordered modes favor sticky routing and affinity preservation over stealing.
- Completion aggregation remains a single aggregator in the first step.
- Config, batching, `wait_for_completion()`, shutdown, recycle, and metrics
  surface must be documented explicitly.
