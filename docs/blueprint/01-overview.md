# Overview

This document is the canonical English overview for the `pyrallel-consumer` blueprint root.
For the preserved Korean source text, see [01-overview.ko.md](./01-overview.ko.md).

## One-line definition

`pyrallel-consumer` is a runtime-selectable parallel Kafka consumer library for Python that raises throughput while preserving offset correctness and key or partition ordering.

## Why this library exists

A basic Kafka consumer is good at sequential processing per partition, but it is difficult to get all of the following at once:

- Parallelism above raw partition count
- Stable key-aware ordering
- Safe offset commits after rebalance or restart
- Different execution models for I/O-bound and CPU-bound workloads
- Operational telemetry that exposes real parallel-processing bottlenecks

`pyrallel-consumer` provides that combination at the library layer.

## What this library is not

- It is not a distributed orchestration system layered on top of Kafka brokers.
- It is not a transaction engine that guarantees exactly-once side effects outside Kafka.
- It is not a worker-framework DSL that tries to erase the differences between async and process workers.
- It is not a fully bundled operations platform that owns dashboards, deployment packaging, and infrastructure lifecycle.

## Root document map

- [02-requirements.md](./02-requirements.md) describes the global requirements, canonical terms, MVP scope, and acceptance criteria.
- [03-architecture.md](./03-architecture.md) explains the layers, data flow, and the mapping between code layout and blueprint structure.
- [04-open-decisions.md](./04-open-decisions.md) lists the remaining global policy decisions that still affect implementation work.

## Current release posture

The current blueprint is organized around four stable ideas:

- `Control Plane invariant`: Kafka consume, ordering, and offset correctness stay on a single path even when execution engines change.
- `Dual execution model`: `AsyncExecutionEngine` and `ProcessExecutionEngine` ship in the same release.
- `Offset correctness first`: HWM, gap handling, epoch fencing, and revoke safety take priority over peak throughput.
- `Operational visibility`: true lag, blocking duration, and benchmark evidence must be readable by operators.

## Feature groups at a glance

| Feature group | Core role |
| --- | --- |
| `01-ingress` | Kafka ingest loop, ordering mode interpretation, and WorkManager scheduling |
| `02-reliability` | Offset state, metadata snapshots, rebalance fencing, retry, and DLQ behavior |
| `03-execution` | Async and process execution-engine contracts and constraints |
| `04-tooling` | Metrics/exporter surfaces and benchmark or profiling workflows |

## Reading order

1. Read the root documents `02/03/04` to understand the whole system.
2. Return to [00-index.md](./00-index.md) and choose the feature or subfeature you need.
3. Within a subfeature, follow `00-index -> 01-requirements -> 02-architecture -> 03-design`.
