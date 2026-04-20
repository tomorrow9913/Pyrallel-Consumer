# Ordered Work Scheduling Design

This document captures implementation-facing contracts and configuration or data-shape details for the subfeature.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## Subfeature summary

`ordered-work-scheduling` covers ordering modes, virtual queues, runnable selection, blocking-offset priority, and starvation protection. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Virtual partition and ordering-mode interpretation.
- Blocking-offset-first scheduling inside `WorkManager`.
- Backpressure-aware queue management without leaking engine internals.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
