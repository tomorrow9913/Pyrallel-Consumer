# Requirements

This document is the canonical English summary of the global blueprint requirements.
For the preserved Korean source text, see [02-requirements.ko.md](./02-requirements.ko.md).

## Document purpose

This root document captures the cross-cutting requirements that do not belong to a single feature tree. It fixes the release scope, the canonical terminology, and the acceptance bar for the blueprint.

## Problem statement

In Python Kafka consumers, you usually end up giving up at least one of these properties:

- Key or partition ordering
- Offset correctness after rebalance or restart
- A workload-specific execution model

CPU-bound workloads make the tradeoff harder because the GIL limits async-only approaches, while process isolation often mixes execution concerns back into the Kafka control plane. `pyrallel-consumer` resolves that tension by keeping the planes separate.

## Service definition

`pyrallel-consumer` reads Kafka messages, normalizes them into `WorkItem`s, schedules them through an ordering-aware path, runs workers on a runtime-selectable execution engine, and commits only contiguous-safe offsets.

## Canonical terms

| Term | Meaning |
| --- | --- |
| `Control Plane` | The layer that owns Kafka consume, rebalance, offset commit, and scheduling state |
| `Execution Plane` | The layer that runs workers and returns completion events |
| `WorkItem` | The canonical unit of work submitted to the execution engine |
| `CompletionEvent` | The canonical completion signal returned to the control plane |
| `HWM` | The highest contiguous offset that is safe to commit |
| `gap` | Completed offsets that still cannot be committed because earlier offsets are unfinished |
| `blocking offset` | The lowest offset currently preventing HWM advancement |
| `epoch fencing` | Partition-generation validation used to drop stale completions after rebalance |
| `metadata_snapshot` | A strategy that stores sparse completed offsets in Kafka commit metadata |
| `DLQ` | The dead-letter topic used for terminal failures |

## Global requirements

- The control plane must not depend on execution-engine concrete types.
- `ExecutionEngine.submit()` is allowed to block, and both docs and code must preserve that expectation.
- Ordering modes stay canonical as `key_hash`, `partition`, and `unordered`.
- Offset commits use only contiguous-safe state; sparse completion may exist only as auxiliary metadata.
- Rebalance must drop stale completions through epoch mismatch checks.
- Retry and DLQ behavior must preserve offset correctness rather than trade it away.
- Metrics must expose true lag, gap state, blocking duration, and backpressure rather than Kafka lag alone.
- Benchmark tooling must keep baseline, async, and process runs comparable under the same workload assumptions.

## MVP scope

Included:

- Single-topic `PyrallelConsumer` facade
- Async and process execution engines in one release
- Ordering-aware scheduling with backpressure
- HWM and gap-based commit correctness
- Epoch fencing, revoke grace, retry, and DLQ handling
- Prometheus exporter support
- Baseline, async, and process benchmark tooling

Excluded:

- Multi-topic orchestration
- Exactly-once external side effects
- Distributed multi-node scheduling
- Storage-backed replay journals
- CI-enforced performance gates

## Acceptance focus

- Root and feature docs must use the same canonical terms for HWM, gap, blocking offset, and epoch fencing.
- Retry, DLQ, and offset correctness must read as one reliability chain.
- Metrics guidance must interpret true lag rather than default Kafka lag.
- Benchmark guidance must explain both throughput and measurement overhead.
