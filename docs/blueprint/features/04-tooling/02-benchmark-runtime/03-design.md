# Benchmark Runtime Design

This document captures implementation-facing contracts and configuration or data-shape details for the subfeature.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## Subfeature summary

`benchmark-runtime` covers baseline vs async vs process benchmark tooling, profiling hooks, and reproducible result summaries. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- Comparable throughput and latency measurement across runtime modes.
- CLI and TUI benchmarking entry points.
- Profiling support, artifact handling, and result interpretation.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
