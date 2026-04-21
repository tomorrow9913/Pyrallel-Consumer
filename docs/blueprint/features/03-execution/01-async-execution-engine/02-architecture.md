# Async Execution Engine Architecture

This document explains the component boundaries and flow for the subfeature.
For the preserved Korean source text, see [02-architecture.ko.md](./02-architecture.ko.md).

## Subfeature summary

`async-execution-engine` covers coroutine worker contract, semaphore-limited concurrency, timeout handling, and graceful shutdown. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Async worker submission and completion delivery.
- Semaphore-based concurrency control and timeout or retry behavior.
- Shutdown semantics that preserve the shared engine contract.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
