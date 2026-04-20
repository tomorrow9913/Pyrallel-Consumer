# Architecture

This document is the canonical English summary of the blueprint architecture.
For the preserved Korean source text, see [03-architecture.ko.md](./03-architecture.ko.md).

## Document purpose

The root architecture document fixes the major layers and the main work flow without repeating every detailed contract. Worker-specific details, configuration keys, and encoding rules stay in feature documents.

## Global architecture principles

- `PyrallelConsumer` is a thin facade over `BrokerPoller`, `WorkManager`, `OffsetTracker`, and the execution engine.
- The control plane must operate without branching on a specific execution-mode implementation.
- Ordering and offset correctness take priority over raw worker throughput.
- Rebalance is a first-class design input, not an edge case.
- Metrics and benchmark surfaces explain the runtime but do not become the consume path itself.
- Process-only behavior must surface through the engine contract or optional capabilities rather than control-plane type checks.

## Layer map

| Layer | Primary feature tree |
| --- | --- |
| Kafka ingest facade/runtime | `01-ingress/01-kafka-runtime-ingest` |
| Ordering-aware scheduler | `01-ingress/02-ordered-work-scheduling` |
| Commit-safe state machine | `02-reliability/01-offset-commit-state` |
| Rebalance and failure recovery | `02-reliability/02-rebalance-retry-dlq` |
| Async execution engine | `03-execution/01-async-execution-engine` |
| Process execution engine | `03-execution/02-process-execution-engine` |
| Observability surface | `04-tooling/01-observability-metrics` |
| Benchmark and profiling surface | `04-tooling/02-benchmark-runtime` |

## End-to-end flow

1. `PyrallelConsumer` receives config, worker, and topic inputs.
2. `BrokerPoller` reads Kafka messages and normalizes them into control-plane inputs.
3. `WorkManager` chooses an ordering queue and decides what becomes runnable.
4. The selected execution engine runs the worker and returns `CompletionEvent`s.
5. `OffsetTracker` updates HWM and gap state, and `MetadataEncoder` prepares optional snapshots.
6. Retry and DLQ handling deal with terminal failures without violating commit safety.
7. Metrics and benchmark tooling project the runtime state for operators.

## Repository-to-blueprint mapping

- Core runtime entry points live under `pyrallel_consumer/consumer.py` and related control-plane modules.
- Execution-engine implementations live under `pyrallel_consumer/execution_plane/*`.
- Observability and operations material is split between runtime code and `docs/operations/*`.
- Benchmark tooling lives under `benchmarks/*` and `examples/*`.

The code tree is organized by implementation files, while this blueprint is organized by work units and contracts.
