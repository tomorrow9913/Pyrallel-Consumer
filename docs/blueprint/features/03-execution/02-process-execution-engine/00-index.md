# Process Execution Engine Index

This index is the canonical English entry for the process-execution-engine
subfeature. For the preserved Korean source text, see
[00-index.ko.md](./00-index.ko.md).

## Long-term direction

The process execution engine is not only a “process worker pool” document set.
Its long-term direction is to preserve the ordered virtual-queue identity that
`WorkManager` already computes before work crosses the process boundary.

The intended model is:

- `WorkManager` decides ordering and eligibility with partition/key virtual
  queues,
- the async engine spreads safe-to-run work immediately via `create_task()`
  without re-merging it into one input queue,
- the process engine should move toward the same shape by selecting the
  appropriate worker execution channel at `submit(work_item)` time.

The route identity is not a process-only scheduling hint. It is the same logical
queue identity that `WorkManager` already uses before async and process execution
diverge.

## Why this matters

The current `shared_queue` process path places every submitted item into one
shared `multiprocessing.Queue`, so all workers compete on the same input queue.
That compatibility path remains important, but benchmark and py-spy evidence
suggest it is a bottleneck for ordered partition workloads.

The evidence direction captured in these docs is:

- worker-side time is dominated by
  `_receive_task_payload -> multiprocessing.Queue.get -> synchronize.__enter__ -> connection.recv_bytes`,
- `_io_worker_process` time is comparatively small,
- therefore input dispatch topology is a higher-priority improvement target
  than completion aggregation.

## Document set

| Document | Role |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | Responsibilities, transport modes, and acceptance criteria |
| [02-architecture.md](./02-architecture.md) | Current shared-queue topology vs target worker-affine topology |
| [03-design.md](./03-design.md) | Config, routing identity, lifecycle, batching, and runtime contract |
| [04-worker-pipe-transport-experiment.md](./04-worker-pipe-transport-experiment.md) | Bounded experiment blueprint for the worker-pipe direction |

## Key principles

- `shared_queue` remains the compatibility/default path.
- `worker_pipes` is the ordering-preserving parallelism direction and an
  eventual default candidate, not a mandated immediate default.
- `WorkManager` and `BrokerPoller` stay transport-agnostic.
- `BaseExecutionEngine.submit(work_item)` stays unchanged.
- ordered modes prefer sticky routing and affinity preservation over stealing.
