# Observability Metrics Architecture

This document explains the component boundaries and flow for the subfeature.
For the preserved Korean source text, see [02-architecture.ko.md](./02-architecture.ko.md).

## Subfeature summary

`observability-metrics` covers metrics DTOs, Prometheus export, operator dashboards, and alert-oriented interpretation. It belongs to the same blueprint family as the companion documents listed below.

## Focus areas

- `SystemMetrics` and `PartitionMetrics` as the canonical telemetry surface.
- Prometheus exporter behavior and opt-in runtime wiring.
- Operational interpretation of true lag, gaps, blockers, and process-mode health.

## Companion documents

- [00-index.md](./00-index.md)
- [01-requirements.md](./01-requirements.md)
- [02-architecture.md](./02-architecture.md)
- [03-design.md](./03-design.md)
