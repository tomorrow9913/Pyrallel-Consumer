# Observability Metrics Design

This document captures implementation-facing contracts and configuration or data-shape details for the subfeature.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

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
