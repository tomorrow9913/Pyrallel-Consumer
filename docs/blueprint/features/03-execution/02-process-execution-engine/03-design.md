# Process Execution Engine Design

This document is the canonical English design summary for the
process-execution-engine subfeature. For the preserved Korean source text, see
[03-design.ko.md](./03-design.ko.md).

## Design contract

The process engine must document both:

- `shared_queue` as the compatibility/default path
- `worker_pipes` as the worker-affine direction

while keeping `BaseExecutionEngine.submit(work_item)` unchanged and keeping the
control plane transport-agnostic. Batched submission extends, rather than
replaces, the item-level contract.

## Key design surfaces

- `ProcessConfig.transport_mode`
- `ExecutionConfig.route_batch_size`
- `BaseExecutionEngine.submit_batch(work_items)`
- `BaseExecutionEngine.supports_ordered_route_batch`
- route identity based on `(topic, partition, key)`
- transport-specific input dispatch
- internal `RouteBatch` and `BatchCompletion` worker-pipe envelopes
- parent-side expansion back to item-level `CompletionEvent` instances
- single completion aggregation remains parent-owned even when worker-pipe
  route batches amortize worker-to-parent IPC
- batching semantics and explicit unsupported combinations
- `wait_for_completion()` parity
- shutdown, recycle, restart, and runtime metrics semantics

The route identity is not a new process-only scheduling hint. It reuses the same
logical queue identity that `WorkManager` already uses to select safe-to-run
work. Async execution ignores it because there is no IPC route; worker-pipe
process execution hashes it to select the worker input channel.

## Route-batch design contract

Route batching is an explicit process-transport optimization:

- `route_batch_size=1` is the default and preserves item-submission behavior.
- `route_batch_size>1` may lease multiple items from one WorkManager virtual
  queue only when the execution engine advertises
  `supports_ordered_route_batch=True` for ordered modes.
- The base `submit_batch()` fallback calls `submit()` in order. It is
  item-semantics compatible but does not make ordered same-route execution
  sequential by itself.
- Engines that partially accept a batch must raise `BatchSubmitError` with the
  accepted prefix count. A generic exception means zero accepted items.
- `worker_pipes` sends route batches as one worker-pipe payload and chooses the
  worker once from the batch route identity.
- Worker execution inside a route batch is sequential. The worker stops after
  the first item failure and reports the unstarted tail for recovery.
- The normal worker-to-parent path sends one `BatchCompletion` envelope for the
  executed prefix. Parent polling expands that envelope into item-level
  completions and keeps `poll_completed_events(batch_limit)` item-count based.
- Batch payloads are internal wire DTOs. Public retry, DLQ, commit, and registry
  accounting stay item-level.

## Route-batch safety requirements

- Poison/force-fail checks must truncate a batch before the first force-fail
  candidate.
- Worker death before item start must keep the pending tail recoverable.
- Live-worker `not_started` tails must requeue or otherwise recover; they are
  not diagnostic-only.
- Fatal timeout/exit must flush any completed prefix before process exit.
- Duplicate suppression must be bounded to avoid unbounded memory growth during
  long-running completion polling.
- Msgpack completion bytes must be size-checked before unpacking, and malformed
  batch payloads must be rejected instead of decoded as empty work.

## Route-batch runtime metrics

Process runtime metrics should expose enough signal to distinguish item IPC from
route-batch IPC:

- `items_per_input_ipc`
- `items_per_completion_ipc`
- `route_batch_count`
- `route_batch_item_count`
- `route_batch_size_avg`
- `route_batch_size_max`
- `completion_item_payload_count`
- `completion_batch_payload_count`
