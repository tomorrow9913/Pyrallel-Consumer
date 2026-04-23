# Observability Metrics Design

This document captures the canonical metric surface, runtime-snapshot boundary, and interpretation rules for `observability-metrics`.
For the preserved Korean source text, see [03-design.ko.md](./03-design.ko.md).

## 1. Core configuration keys

| Key | Meaning | Default |
| --- | --- | --- |
| `METRICS_ENABLED` | start the Prometheus HTTP exporter for library runtime processes | `false` |
| `METRICS_PORT` | exporter port when metrics are enabled | `9091` |
| `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | configured hard ceiling for total in-flight work | `1000` |
| `PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__ENABLED` | enable adaptive backpressure policy telemetry and live limit control | `false` |
| `PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED` | enable adaptive concurrency policy telemetry and live limit control | `false` |
| `PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED` | enable poison-message circuit-breaker runtime snapshot section | `false` |
| `PARALLEL_CONSUMER_DIAG_LOG_EVERY` | periodic runtime diagnostics logging cadence | `1000` |
| `PARALLEL_CONSUMER_BLOCKING_WARN_SECONDS` | warning threshold for oldest blocking offset | `5.0` |
| `PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS` | optional hard timeout hint for blocking offsets | `0` |

## 2. Canonical metric surface

### 2.1 Completion, queue, and partition state

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | completion success/failure count |
| `consumer_commit_failures_total` | Counter | `topic`, `partition`, `reason` | final Kafka commit failures |
| `consumer_dlq_publish_failures_total` | Counter | `topic`, `partition` | terminal DLQ publish failures |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | submit-to-completion latency |
| `consumer_in_flight_count` | Gauge | none | total in-flight count |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | true lag (`last_fetched_offset - last_committed_offset`) |
| `consumer_gap_count` | Gauge | `topic`, `partition` | outstanding gap count |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | virtual queue backlog |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | duration of the current blocking offset |
| `consumer_backpressure_active` | Gauge | none | `1=paused`, `0=running` |
| `consumer_metadata_size_bytes` | Gauge | `topic` | offset-commit metadata payload size |

### 2.2 Resource-signal and adaptive control state

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `consumer_resource_signal_status` | Gauge | `status` | one-hot resource-signal availability state: `available`, `unavailable`, `stale`, `first_sample_pending` |
| `consumer_resource_cpu_utilization_ratio` | Gauge | none | latest host CPU utilization ratio, or `0` when unavailable/fail-open |
| `consumer_resource_memory_utilization_ratio` | Gauge | none | latest host memory utilization ratio, or `0` when unavailable/fail-open |
| `consumer_adaptive_backpressure_configured_max_in_flight` | Gauge | none | configured adaptive backpressure ceiling |
| `consumer_adaptive_backpressure_effective_max_in_flight` | Gauge | none | live adaptive backpressure limit |
| `consumer_adaptive_backpressure_min_in_flight` | Gauge | none | adaptive backpressure minimum floor |
| `consumer_adaptive_backpressure_scale_up_step` | Gauge | none | adaptive backpressure scale-up step |
| `consumer_adaptive_backpressure_scale_down_step` | Gauge | none | adaptive backpressure scale-down step |
| `consumer_adaptive_backpressure_cooldown_ms` | Gauge | none | adaptive backpressure cooldown (ms) |
| `consumer_adaptive_backpressure_lag_scale_up_threshold` | Gauge | none | lag threshold that triggers adaptive backpressure scale-up |
| `consumer_adaptive_backpressure_low_latency_threshold_ms` | Gauge | none | low-latency threshold for adaptive backpressure decisions |
| `consumer_adaptive_backpressure_high_latency_threshold_ms` | Gauge | none | high-latency threshold for adaptive backpressure decisions |
| `consumer_adaptive_backpressure_avg_completion_latency_seconds` | Gauge | none | current adaptive backpressure decision input |
| `consumer_adaptive_backpressure_last_decision` | Gauge | `decision` | one-hot latest adaptive backpressure decision |
| `consumer_adaptive_concurrency_configured_max_in_flight` | Gauge | none | configured adaptive concurrency ceiling |
| `consumer_adaptive_concurrency_effective_max_in_flight` | Gauge | none | live adaptive concurrency limit |
| `consumer_adaptive_concurrency_min_in_flight` | Gauge | none | adaptive concurrency minimum floor |
| `consumer_adaptive_concurrency_scale_up_step` | Gauge | none | adaptive concurrency scale-up step |
| `consumer_adaptive_concurrency_scale_down_step` | Gauge | none | adaptive concurrency scale-down step |
| `consumer_adaptive_concurrency_cooldown_ms` | Gauge | none | adaptive concurrency cooldown (ms) |

### 2.3 Process-batch runtime state

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `consumer_process_batch_flush_count` | Gauge | `reason` | cumulative process batch flush count by reason |
| `consumer_process_batch_avg_size` | Gauge | none | average process batch flush size |
| `consumer_process_batch_last_size` | Gauge | none | most recent process batch size |
| `consumer_process_batch_last_wait_seconds` | Gauge | none | wait time of the most recent process batch |
| `consumer_process_batch_buffered_items` | Gauge | none | currently buffered process items |
| `consumer_process_batch_buffered_age_seconds` | Gauge | none | age of the current process batch buffer |
| `consumer_process_batch_last_main_to_worker_ipc_seconds` | Gauge | none | most recent main-to-worker IPC time |
| `consumer_process_batch_avg_main_to_worker_ipc_seconds` | Gauge | none | average main-to-worker IPC time |
| `consumer_process_batch_last_worker_exec_seconds` | Gauge | none | most recent process worker execution time |
| `consumer_process_batch_avg_worker_exec_seconds` | Gauge | none | average process worker execution time |
| `consumer_process_batch_last_worker_to_main_ipc_seconds` | Gauge | none | most recent worker-to-main IPC time |
| `consumer_process_batch_avg_worker_to_main_ipc_seconds` | Gauge | none | average worker-to-main IPC time |

## 3. Runtime snapshot API boundary

`PyrallelConsumer.get_runtime_snapshot()` returns a read-only `RuntimeSnapshot` projection.
The stable documented sections are:

- `queue`: `total_in_flight`, `total_queued`, live `max_in_flight`, `configured_max_in_flight`, `is_paused`, `is_rebalancing`, `ordering_mode`
- `retry`: `max_retries`, `retry_backoff_ms`, `exponential_backoff`, `max_retry_backoff_ms`, `retry_jitter_ms`
- `dlq`: `enabled`, `topic`, `payload_mode`, `message_cache_size_bytes`, `message_cache_entry_count`
- `partitions[]`: `tp`, `current_epoch`, `last_committed_offset`, `last_fetched_offset`, `true_lag`, `gaps`, `blocking_offset`, `blocking_duration_sec`, `queued_count`, `in_flight_count`, `min_in_flight_offset`
- optional `adaptive_backpressure`: configured ceiling, effective live limit, guardrails, latest decision, average completion latency input
- optional `adaptive_concurrency`: configured ceiling, effective live limit, and scaling guardrails
- optional `process_batch_metrics`: current process micro-batch runtime counters/timings
- optional `poison_message`: enablement, threshold, cooldown, and open-circuit count

Interpretation rules:

- `queue.max_in_flight` is the current live control-plane limit.
- `queue.configured_max_in_flight` is the static configured ceiling.
- When adaptive concurrency/backpressure is disabled, the optional adaptive sections may be absent even though queue state still exists.
- The runtime snapshot is a diagnostics surface. It is not an audit log, retry ledger, DLQ history, or payload dump.
- The runtime snapshot must not expose secure Kafka transport fields, SASL/TLS secrets, usernames, or certificate/key paths; those remain ingress config inputs, not observability outputs.

## 4. Benchmark/runtime exposure boundary

- Benchmark JSON summaries carry selected observability evidence (`metrics_observations`, `final_lag`, `final_gap_count`) plus benchmark result rows.
- Those JSON artifacts do **not** serialize the full runtime snapshot API.
- Benchmark-side `--metrics-port` exposure is a Pyrallel-only harness convenience. Baseline runs ignore it.
- Production runtime exporter startup still requires `KafkaConfig.metrics.enabled = True`.

## 5. Operational interpretation rules

- Rising `consumer_parallel_lag` means real processing backlog is increasing.
- Rising `consumer_gap_count` means out-of-order completion is delaying commit progress.
- Rising `consumer_oldest_task_duration_seconds` points to a hot key, poison path, or blocked downstream dependency.
- `consumer_backpressure_active == 1` means fetch intake is paused because control-plane load exceeded the current limit.
- A large difference between configured and effective adaptive limits means the live controller is actively tuning throughput/latency trade-offs.
- `consumer_resource_signal_status{status="available"} == 0` means host telemetry is unavailable/stale and adaptive logic should be interpreted as fail-open.
- Process-batch gauges are only meaningful for process mode; zero values outside process mode are not a fault by themselves.

## 6. Alert and tuning hints

- Alert when `consumer_backpressure_active` stays at `1` for a sustained window rather than on a single scrape.
- Alert when lag and gap counts grow monotonically for multiple minutes.
- Prioritize poison-message/DLQ investigation when blocking duration grows together with failures.
- Use adaptive gauges plus runtime snapshot queue fields together when tuning `max_in_flight`; do not infer process counts or semaphore sizes from those metrics alone.
