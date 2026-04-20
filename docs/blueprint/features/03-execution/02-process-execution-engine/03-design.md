# Process Execution Engine Design

This document captures implementation-facing contracts and configuration or data-shape details for the subfeature.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## Subfeature summary

`process-execution-engine` covers picklable worker contracts, msgpack micro-batching, process lifecycle, and crash or timeout isolation. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Task and completion IPC boundaries between the control plane and worker processes.
- Micro-batching, serialization, and worker recycle policy.
- Crash handling, timeout recovery, and shutdown choreography.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
