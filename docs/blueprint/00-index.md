# Index

This document is the canonical English entry point for the `pyrallel-consumer` blueprint tree.
For the preserved Korean source text, see [00-index.ko.md](./00-index.ko.md).

## Root documents

| Document | Question answered |
| --- | --- |
| [01-overview.md](./01-overview.md) | Why does `pyrallel-consumer` exist and what kind of library is it? |
| [02-requirements.md](./02-requirements.md) | What are the global requirements, canonical terms, and MVP boundaries? |
| [03-architecture.md](./03-architecture.md) | How is the system split into layers and how does work flow through them? |
| [04-open-decisions.md](./04-open-decisions.md) | Which global policy values still need to be locked before implementation? |
| [99-context-restoration.md](./99-context-restoration.md) | What historical context explains the current blueprint layout and drift risks? |

## Feature groups

### `01-ingress`

- [01-kafka-runtime-ingest](./features/01-ingress/01-kafka-runtime-ingest/00-index.md)
  Facade bootstrap, Kafka client lifecycle, consume/pause/resume, and raw payload cache boundaries.
- [02-ordered-work-scheduling](./features/01-ingress/02-ordered-work-scheduling/00-index.md)
  Ordering modes, virtual partition queues, blocking-offset-first scheduling, and starvation protection.

### `02-reliability`

- [01-offset-commit-state](./features/02-reliability/01-offset-commit-state/00-index.md)
  HWM, gap handling, true lag, metadata snapshots, and commit-safe state tracking.
- [02-rebalance-retry-dlq](./features/02-reliability/02-rebalance-retry-dlq/00-index.md)
  Epoch fencing, revoke grace, retry/backoff, DLQ publish rules, and failure recovery.

### `03-execution`

- [01-async-execution-engine](./features/03-execution/01-async-execution-engine/00-index.md)
  `asyncio` task execution, semaphores, timeout handling, and graceful shutdown.
- [02-process-execution-engine](./features/03-execution/02-process-execution-engine/00-index.md)
  Multiprocessing workers, msgpack micro-batching, picklable workers, and worker recycle controls.

### `04-tooling`

- [01-observability-metrics](./features/04-tooling/01-observability-metrics/00-index.md)
  `SystemMetrics`, Prometheus export, alerting signals, and operator-facing telemetry.
- [02-benchmark-runtime](./features/04-tooling/02-benchmark-runtime/00-index.md)
  Baseline vs async vs process benchmarking, profiling hooks, and result interpretation.

## Reading guide

- Start with [01-overview.md](./01-overview.md) for the service definition and high-level picture.
- Read [02-requirements.md](./02-requirements.md) before editing contracts, scope, or canonical terms.
- Read [03-architecture.md](./03-architecture.md) before changing component boundaries.
- Read [04-open-decisions.md](./04-open-decisions.md) when a change depends on a still-open policy choice.

## Suggested order

1. Read the root documents `01/02/03/04`.
2. Move to the relevant feature group.
3. Within a subfeature, read `00-index -> 01-requirements -> 02-architecture -> 03-design`.
