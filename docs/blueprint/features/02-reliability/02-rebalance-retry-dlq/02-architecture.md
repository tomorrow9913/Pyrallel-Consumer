# Rebalance Retry DLQ Architecture

This document explains the component boundaries and flow for the subfeature.
For the preserved Korean source text, see [02-architecture.ko.md](./02-architecture.ko.md).

## Subfeature summary

`rebalance-retry-dlq` covers epoch fencing, revoke and assign lifecycle, retry or backoff policy, and DLQ final handling. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Rebalance ownership boundaries and stale-completion fencing.
- Assignment or revoke state hydration through `rebalance_state_strategy`.
- Retry exhaustion flow and DLQ publish guarantees.
- Liveness-first revoke behavior under bounded grace periods.

## 1. Component roles

| Component | Responsibility |
| --- | --- |
| `BrokerRebalanceSupport` | Builds assignment state from committed offsets plus optional metadata snapshots and performs revoke-time final commits |
| `OffsetTracker` | Holds partition epoch, contiguous commit state, and sparse completion state for the current owner generation |
| `WorkManager` | Accepts hydrated tracker assignments, drops revoked queues, and releases stale in-flight ownership |
| `BrokerPoller` | Interprets completion events, fences stale epochs, and drives retry or DLQ completion handling |

## 2. Processing flow

1. `on_partitions_assigned()` asks `BrokerRebalanceSupport.build_assignments()`
   to fetch committed offsets and hydrate `OffsetTracker` state.
2. `rebalance_state_strategy=contiguous_only` restores only the next contiguous
   restart position; `metadata_snapshot` additionally decodes sparse completed
   offsets from commit metadata when present.
3. Each hydrated tracker increments its partition epoch before `WorkManager`
   receives the assignment, so new `WorkItem` submissions carry the current
   owner generation.
4. When completions return, `BrokerPoller` accepts only events whose
   `(topic, partition, epoch)` still match the active assignment and drops the
   rest as stale rebalance fallout.
5. `on_partitions_revoked()` stops new scheduling for the revoked partitions,
   clears cached payloads and poison-circuit state for those partitions, and
   advances each tracker to its current contiguous-safe high-water mark.
6. Revoke-time commit uses the same strategy split: `contiguous_only` commits
   only the next restart offset, while `metadata_snapshot` may attach sparse
   completion metadata for later hydration.
7. Retry exhaustion and DLQ publishing happen after epoch fencing; a terminal
   failure reaches commit-safe advancement only after the DLQ path succeeds or
   the policy explicitly degrades.

## 3. Boundary rules

- Rebalance strategy changes how sparse completion state is preserved across
  ownership changes, but it never changes the contiguous committed offset rule.
- Stale completion drops are expected after rebalance and must not mutate
  tracker state for the new epoch.
- Revoke-time liveness rules may discard optional metadata preservation, but
  they must not commit beyond the contiguous-safe position.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
