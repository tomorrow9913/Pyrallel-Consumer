# Rebalance Retry DLQ Requirements

This document records the scope, responsibilities, and acceptance focus for the subfeature.
For the preserved Korean source text, see [01-requirements.ko.md](./01-requirements.ko.md).

## Subfeature summary

`rebalance-retry-dlq` covers epoch fencing, revoke and assign lifecycle, retry or backoff policy, and DLQ final handling. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Rebalance ownership boundaries and stale-completion fencing.
- Retry exhaustion flow and DLQ publish guarantees.
- Liveness-first revoke behavior under bounded grace periods.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
