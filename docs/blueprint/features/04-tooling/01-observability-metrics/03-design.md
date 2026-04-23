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

## Canonical metric surface

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | completion success/failure count |
| `consumer_commit_failures_total` | Counter | `topic`, `partition`, `reason` | final offset commit failures grouped by fixed reason labels |
| `consumer_dlq_publish_failures_total` | Counter | `topic`, `partition` | terminal DLQ publish failures that leave offsets pending retry |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | submit to completion latency |
| `consumer_in_flight_count` | Gauge | none | total in-flight count |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | true lag |
| `consumer_gap_count` | Gauge | `topic`, `partition` | outstanding gap count |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | virtual queue backlog |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | blocking duration |
| `consumer_backpressure_active` | Gauge | none | `1=paused` |
| `consumer_metadata_size_bytes` | Gauge | `topic` | commit metadata payload size |
| `consumer_adaptive_backpressure_configured_max_in_flight` | Gauge | none | configured adaptive backpressure ceiling |
| `consumer_adaptive_backpressure_effective_max_in_flight` | Gauge | none | live adaptive backpressure limit |
| `consumer_adaptive_backpressure_min_in_flight` | Gauge | none | adaptive backpressure minimum floor |
| `consumer_adaptive_backpressure_scale_up_step` | Gauge | none | adaptive backpressure scale-up step |
| `consumer_adaptive_backpressure_scale_down_step` | Gauge | none | adaptive backpressure scale-down step |
| `consumer_adaptive_backpressure_cooldown_ms` | Gauge | none | adaptive backpressure cooldown (ms) |
| `consumer_adaptive_backpressure_lag_scale_up_threshold` | Gauge | none | lag threshold that triggers backpressure scale-up |
| `consumer_adaptive_backpressure_low_latency_threshold_ms` | Gauge | none | adaptive backpressure low-latency threshold (ms) |
| `consumer_adaptive_backpressure_high_latency_threshold_ms` | Gauge | none | adaptive backpressure high-latency threshold (ms) |
| `consumer_adaptive_backpressure_avg_completion_latency_seconds` | Gauge | none | current adaptive backpressure decision input |
| `consumer_adaptive_backpressure_last_decision` | Gauge | `decision` | last adaptive backpressure decision one-hot |
| `consumer_adaptive_concurrency_configured_max_in_flight` | Gauge | none | configured adaptive concurrency ceiling |
| `consumer_adaptive_concurrency_effective_max_in_flight` | Gauge | none | live adaptive concurrency limit |
| `consumer_adaptive_concurrency_min_in_flight` | Gauge | none | adaptive concurrency minimum floor |
| `consumer_adaptive_concurrency_scale_up_step` | Gauge | none | adaptive concurrency scale-up step |
| `consumer_adaptive_concurrency_scale_down_step` | Gauge | none | adaptive concurrency scale-down step |
| `consumer_adaptive_concurrency_cooldown_ms` | Gauge | none | adaptive concurrency cooldown (ms) |

Failure alerting should key off the labeled counters above:
`consumer_commit_failures_total{reason="kafka_exception"}` for final Kafka commit
failures and `consumer_dlq_publish_failures_total` for terminal DLQ publish
failures.
