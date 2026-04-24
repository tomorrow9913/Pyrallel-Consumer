# Rebalance Retry DLQ Design

This document captures implementation-facing contracts and configuration or data-shape details for the subfeature.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## Subfeature summary

`rebalance-retry-dlq` covers epoch fencing, revoke and assign lifecycle, retry or backoff policy, and DLQ final handling. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Rebalance ownership boundaries and stale-completion fencing.
- `rebalance_state_strategy` contracts for assign or revoke state preservation.
- Retry exhaustion flow and DLQ publish guarantees.
- Liveness-first revoke behavior under bounded grace periods.

## 1. Core configuration keys

| Key | Meaning | Default |
| --- | --- | --- |
| `PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY` | Rebalance state-preservation mode used by assignment hydration and revoke-time commits | `contiguous_only` |
| `EXECUTION_MAX_REVOKE_GRACE_MS` | Best-effort revoke drain budget before liveness-first cleanup wins | `500` |
| `EXECUTION_MAX_RETRIES` | Worker retry ceiling before a failure becomes terminal | `3` |
| `EXECUTION_RETRY_BACKOFF_MS` | Base retry backoff | `1000` |
| `EXECUTION_MAX_RETRY_BACKOFF_MS` | Retry backoff cap | `30000` |
| `EXECUTION_RETRY_JITTER_MS` | Additional retry jitter budget | `200` |
| `KAFKA_DLQ_ENABLED` | Whether terminal failures publish to the dead-letter topic before commit-safe advancement | `true` |
| `KAFKA_DLQ_TOPIC_SUFFIX` | Topic suffix appended to the source topic for DLQ publication | `.dlq` |
| `KAFKA_DLQ_PAYLOAD_MODE` | Whether DLQ records keep raw payloads or degrade to metadata-only output | `full` |

## 2. Rebalance state strategy contract

- `contiguous_only` persists only the next contiguous restart offset during
  commits and revoke handling.
- `metadata_snapshot` keeps the same contiguous restart offset, but also encodes
  sparse completed offsets beyond that HWM into Kafka commit metadata so
  assignment can hydrate them later.
- Missing, blank, or undecodable metadata snapshot payloads degrade to the same
  behavior as `contiguous_only`.
- Strategy selection affects replay volume after rebalance; it must not change
  the commit-safety invariant that only contiguous offsets are committed.

## 3. Stale completion fencing

- A completion for an unassigned `(topic, partition)` is discarded.
- A completion whose `epoch` does not match the active tracker epoch is
  discarded.
- Discarded stale completions are observable as rebalance noise, but they must
  not advance commit state, clear blockers for the new owner, or re-open revoked
  scheduling paths.

## 4. Retry and DLQ contract

- Retry attempts are 1-based and stop once the worker succeeds or the configured
  retry ceiling is exhausted.
- Backoff may be linear or exponential with additive jitter, but the retry loop
  remains an execution-engine concern until a failure becomes terminal.
- Once a failure is terminal, the control plane interprets its commit meaning:
  DLQ success allows the offset to re-enter the normal commit-safe path, while a
  DLQ publish failure leaves the offset pending for later recovery rather than
  committing past it.

## 5. Revoke and liveness rules

- Revoke handling first stops new work for the partition, then attempts a final
  contiguous-safe commit.
- If the revoke grace window is exhausted, the runtime may abandon optional
  sparse metadata preservation to keep consumer-group liveness.
- Reprocessing after rebalance is acceptable; offset corruption is not.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
