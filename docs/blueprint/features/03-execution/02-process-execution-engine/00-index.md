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
  appropriate worker execution channel at `submit(work_item)` or
  `submit_batch(work_items)` time.

The route identity is not a process-only scheduling hint. It is the same logical
queue identity that `WorkManager` already uses before async and process execution
diverge.

## Why this matters

The compatibility `shared_queue` process path places every submitted item into one
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
| [03-design.md](./03-design.md) | Config, routing identity, lifecycle, route batching, and runtime contract |
| [04-worker-pipe-transport-experiment.md](./04-worker-pipe-transport-experiment.md) | Worker-pipe transport and route-batch experiment contract |

## Key principles

- `worker_pipes` is the default ordering-preserving parallelism path.
- `shared_queue` remains an explicit legacy compatibility fallback.
- `WorkManager` and `BrokerPoller` stay transport-agnostic.
- `BaseExecutionEngine.submit(work_item)` stays unchanged, and
  `submit_batch(work_items)` is an execution-engine contract with item-semantics
  fallback.
- ordered route batching is enabled only when the engine explicitly advertises
  `supports_ordered_route_batch`.
- ordered modes prefer sticky routing and affinity preservation over stealing.

## Current route-batch status

The implemented route-batch path promotes `worker_pipes` as the default process
transport while keeping `shared_queue` available as an explicit legacy fallback.

- `ExecutionConfig.route_batch_size` defaults to `64`; ordered modes still use
  an effective size of `1` unless the execution engine advertises ordered
  route-batch capability.
- `WorkManager` may lease multiple items from one virtual queue, bounded by
  remaining in-flight capacity and poison/force-fail guards.
- `worker_pipes` can send one route-batch payload to the selected worker, which
  executes the items sequentially.
- Worker-to-parent route-batch completion is represented as one internal
  `BatchCompletion` envelope and expanded back into item-level
  `CompletionEvent` instances in the parent.
- Registry, retry, commit, and recovery accounting remain item-level; the batch
  is a transport envelope, not a commit or retry unit.
