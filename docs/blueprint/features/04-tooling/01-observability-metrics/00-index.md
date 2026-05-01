# Observability Metrics Index

This index is the canonical English entry for the `observability-metrics` subfeature.
For the preserved Korean source text, see [00-index.ko.md](./00-index.ko.md).

## Questions this subfeature answers

- Which runtime projections are canonical for operators: Prometheus metrics, pull-style runtime snapshots, or benchmark-only summaries?
- How do `BrokerPoller.get_metrics()`, `PyrallelConsumer.get_runtime_snapshot()`, and `PrometheusMetricsExporter` relate without becoming sources of truth themselves?
- Where do adaptive backpressure, adaptive concurrency, process-batch, and resource-signal surfaces belong in the blueprint tree?
- Which current benchmark/runtime exposures are production-facing versus benchmark-only conveniences?

## Document map

| Document | Role |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | Scope, responsibilities, and acceptance criteria for runtime observability surfaces |
| [02-architecture.md](./02-architecture.md) | Projection flow from control-plane state into metrics, runtime snapshots, and benchmark observers |
| [03-design.md](./03-design.md) | Canonical metric names, runtime-snapshot field boundaries, config keys, and interpretation rules |

## Quick reading guide

- Start with [03-design.md](./03-design.md) if you need the current canonical metric/runtime-snapshot surface.
- Read [02-architecture.md](./02-architecture.md) to understand how exporter wiring, pull snapshots, and benchmark observers consume the same runtime state.
- Read [01-requirements.md](./01-requirements.md) first if you need the feature boundary before editing docs or code.

## Current implementation anchors

- Runtime metric projection: `BrokerPoller.get_metrics()` and `BrokerRuntimeSupport.get_system_metrics()`
- Runtime snapshot projection: `BrokerRuntimeSupport.build_runtime_snapshot()` and `PyrallelConsumer.get_runtime_snapshot()`
- Prometheus exporter: `pyrallel_consumer/metrics_exporter.py`
- Operator guides: `docs/operations/guide.en.md`, `docs/operations/playbooks.md`
- Benchmark observer-side exposure: `benchmarks/pyrallel_consumer_test.py`, `benchmarks/stats.py`
