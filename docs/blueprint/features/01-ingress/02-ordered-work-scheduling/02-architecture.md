# Ordered Work Scheduling Architecture

This document explains the component boundaries and flow for the subfeature.
For the preserved Korean source text, see [02-architecture.ko.md](./02-architecture.ko.md).

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
