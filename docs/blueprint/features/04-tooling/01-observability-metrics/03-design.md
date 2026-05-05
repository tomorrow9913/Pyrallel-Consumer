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

Canonical Python configuration remains the primary API. Environment variables
are settings inputs that hydrate the same config objects.

| Python config field | Environment key | Notes |
| --- | --- | --- |
| `KafkaConfig.metrics.enabled` / `MetricsConfig.enabled` | `METRICS_ENABLED` | Enables the library Prometheus HTTP exporter. |
| `KafkaConfig.metrics.port` / `MetricsConfig.port` | `METRICS_PORT` | Exporter port used by the per-process exporter cache. |
| `ParallelConsumerConfig.execution.max_in_flight_messages` | `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | Static hard ceiling for control-plane in-flight work. |
| `ParallelConsumerConfig.adaptive_backpressure.enabled` | `PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__ENABLED` | Enables adaptive backpressure telemetry and live limit control. |
| `ParallelConsumerConfig.adaptive_concurrency.enabled` | `PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED` | Enables adaptive concurrency telemetry and live limit control. |
| `ParallelConsumerConfig.poison_message.enabled` | `PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED` | Enables poison-message runtime snapshot section. |

Exporter instances are cached per metrics port inside the process. The current
metric surface has no `consumer_id`/`instance` label. Multiple consumers sharing
one exporter port therefore share one registry: counters with identical labels
aggregate, and gauges with identical labels are last-snapshot-wins. Prefer one
consumer per metrics port unless topics/partitions and no-label gauges are
operationally understood as process-level signals.

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
| `consumer_process_batch_transport_mode` | Gauge | `mode` | one-hot process execution transport diagnostic; currently `worker_pipes` only |
| `consumer_process_batch_support_state` | Gauge | `state` | one-hot support boundary state for the active process execution diagnostic |
| `consumer_process_batch_timer_flush_supported` | Gauge | none | `1` when timer flush is supported by the active process execution path |
| `consumer_process_batch_demand_flush_supported` | Gauge | none | `1` when demand flush is supported by the active process execution path |
| `consumer_process_batch_recycle_supported` | Gauge | none | `1` when recycle settings are supported by the active process execution path |

`consumer_process_batch_flush_count` is intentionally a Gauge in the current
exporter even though the value is cumulative inside a process-engine snapshot.
The exporter mirrors `ProcessBatchMetrics` snapshots and resets the series to
`0` when process metrics are absent or after process/runtime restart. Treat it
as a snapshot gauge; use `rate()` / `increase()` only with reset awareness.

### 2.4 Stable pipeline diagnostics sidecar

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `pyrallel_pipeline_stage_messages` | Gauge | `stage`, `engine_type` | supported sidecar message counts by bounded pipeline stage |
| `pyrallel_pipeline_blocked_messages` | Gauge | `reason`, `engine_type` | supported sidecar blocked counts by bounded blocker reason |
| `pyrallel_pipeline_dispatch_capacity_blocked_messages` | Gauge | `reason`, `engine_type` | dispatch-capacity pressure for bounded reasons such as `max_in_flight` |
| `pyrallel_pipeline_section_support_state` | Gauge | `section`, `state`, `engine_type` | one-hot support state for each sidecar section |
| `pyrallel_pipeline_worker_capacity_units` | Gauge | `state`, `engine_type` | aggregate worker capacity counts for `total`, `executing`, and `admitted` when worker diagnostics are supported |
| `pyrallel_pipeline_subqueue_items` | Gauge | `state`, `engine_type` | aggregate queued, eligible, and blocked item counts across bounded scheduling subqueues |
| `pyrallel_pipeline_subqueues` | Gauge | `state`, `engine_type` | aggregate total, queued, eligible, and blocked subqueue counts |
| `pyrallel_pipeline_settlement_blocker_state` | Gauge | `reason`, `engine_type` | one-hot current primary settlement blocker reason when settlement diagnostics are supported |
| `pyrallel_pipeline_poll_records_total` | Counter | `broker_kind`, `engine_type` | delta-safe broker poll record count from supported poll diagnostics |
| `pyrallel_pipeline_poll_events_total` | Counter | `event`, `broker_kind`, `engine_type` | delta-safe bounded broker poll event counts |
| `pyrallel_pipeline_completion_to_commit_latency_seconds` | Histogram | `engine_type` | broker-owned pipeline event metric emitted alongside the sidecar projection for completion-to-successful-commit settlement latency |

Most pipeline metrics are a bounded Prometheus projection of the official sidecar
returned by `PyrallelConsumer.get_pipeline_diagnostics()` /
`BrokerPoller.get_pipeline_diagnostics()`. Completion-to-commit latency is a
broker-owned pipeline event metric emitted alongside the sidecar projection, not
a sidecar DTO field. They do not merge into or change
`RuntimeSnapshot` v1. The exporter emits observed count gauges only for sections
and stages whose support state is `supported`. `not_implemented` and
`unavailable` sections are part of the public support-state contract and are represented through
`pyrallel_pipeline_section_support_state`; their observed count gauges stay
absent rather than being exported as zero.

`workers.top_k_loads`, `subqueues.top_k_depths`, stage/blocker/settlement ages,
and raw topic, partition, key, route, worker id, subqueue id, offset, or exception
text remain snapshot/debug-only and must not be Prometheus labels. Worker
occupancy is exposed only as aggregate `pyrallel_pipeline_worker_capacity_units`.

### 2.5 Label value contract

Labels must stay bounded. The current canonical label values are:

| Label surface | Allowed values |
| --- | --- |
| `consumer_processed_total.status` | `success`, `failure` |
| `consumer_commit_failures_total.reason` | `kafka_exception` |
| `consumer_adaptive_backpressure_last_decision.decision` | `disabled`, `hold`, `scale_up`, `scale_down`, `cooldown` |
| `consumer_process_batch_flush_count.reason` | `size`, `timer`, `close`, `demand` |
| `consumer_process_batch_transport_mode.mode` | `worker_pipes` |
| `consumer_process_batch_support_state.state` | `full`, `bounded` |
| `consumer_resource_signal_status.status` | `available`, `unavailable`, `stale`, `first_sample_pending` |
| `pyrallel_pipeline_stage_messages.stage` | `acquired`, `buffered`, `queued`, `dispatched`, `executing`, `completed_unsettled`, `failed`, `dlq` |
| `pyrallel_pipeline_blocked_messages.reason` | `ordering_lock`, `route_lock`, `retry_delay`, `frontier_deferred`, `poison_guard`, `rebalancing`, `shutdown` |
| `pyrallel_pipeline_dispatch_capacity_blocked_messages.reason` | `max_in_flight`, `adaptive_limit` |
| `pyrallel_pipeline_section_support_state.section` | `stages`, `blocked`, `subqueues`, `dispatch_capacity`, `admission`, `workers`, `settlement`, `poll` |
| `pyrallel_pipeline_section_support_state.state` | `supported`, `unavailable`, `not_implemented` |
| `pyrallel_pipeline_worker_capacity_units.state` | `total`, `executing`, `admitted` |
| `pyrallel_pipeline_subqueue_items.state` | `queued`, `eligible`, `blocked` |
| `pyrallel_pipeline_subqueues.state` | `total`, `queued`, `eligible`, `blocked` |
| `pyrallel_pipeline_settlement_blocker_state.reason` | `commit_pending`, `dlq_publish_pending`, `ordered_cursor_gap`, `ack_pending`, `delete_pending`, `archive_pending`, `unknown` |
| `pyrallel_pipeline_poll_records_total.broker_kind` | `kafka`, `unknown` |
| `pyrallel_pipeline_poll_events_total.event` | `nonempty`, `empty`, `error` |
| `pyrallel_pipeline_poll_events_total.broker_kind` | `kafka`, `unknown` |
| `pyrallel_pipeline_completion_to_commit_latency_seconds.engine_type` | `async`, `process` |

`first_sample_pending` is part of the public resource-signal enum for custom
providers that can distinguish warm-up from failure. The built-in null provider
reports `unavailable`.

### 2.6 Metric ownership

| Metric group | Computed by | Projected by | Boundary |
| --- | --- | --- | --- |
| completion counters and processing latency | `WorkManager` completion ledger | `PrometheusMetricsExporter` | Latency starts at WorkManager dispatch timestamp and ends when completion is processed. |
| queue depth, in-flight, lag/gap/blocking duration | `BrokerPoller` / `BrokerRuntimeSupport` from control-plane state | `PrometheusMetricsExporter` | `consumer_internal_queue_depth` is partition-level virtual queue backlog. |
| metadata size | commit metadata encoding path | `PrometheusMetricsExporter.update_metadata_size()` | Gauge is the most recent offset-commit metadata payload size per topic. |
| adaptive/resource-signal gauges | adaptive controllers and resource-signal provider | `PrometheusMetricsExporter` | Disabled/absent adaptive sections are exported as zero-valued gauges plus `decision="disabled"` for backpressure. |
| process-batch and IPC gauges | process execution engine via `ProcessBatchMetrics` | `SystemMetrics.process_batch_metrics` then `PrometheusMetricsExporter` | Control plane does not inspect process-engine internals; it only carries the DTO projection. |
| pipeline diagnostics sidecar gauges | `WorkManager`, execution engine diagnostics, and `BrokerPoller` sidecar composition | `PrometheusMetricsExporter.update_pipeline_diagnostics()` | Exporter projects official sidecar DTO fields only; it does not inspect or compute pipeline state from private internals. |

### 2.7 Triage-first metric model

The triage-first metric model assigns each internal bottleneck signal to the
runtime owner that can observe it without guessing:

| Operational question | Source | Metric direction |
| --- | --- | --- |
| How many records are poll loops acquiring? | BrokerPoller/control-plane diagnostics | poll/acquire rate via bounded poll event counters |
| How many messages are waiting inside Pyrallel? | WorkManager-owned scheduling state | queued and eligible gauges |
| How many messages were accepted for dispatch? | WorkManager-owned submit handoff | dispatched is WorkManager-owned accepted submit accounting |
| How many capacity units are occupied by execution? | ExecutionEngine diagnostics | executing/admitted are engine-owned worker capacity diagnostics |
| How many terminal-path items cannot settle? | BrokerPoller settlement diagnostics | completed-unsettled and DLQ pending gauges |
| Which terminal-path blocker is currently active? | BrokerPoller settlement diagnostics | bounded settlement blocker state as one-hot current reason |
| How long does completed work wait before commit? | BrokerPoller settlement diagnostics | completion-to-commit latency as a settlement-path diagnostic |

completion-to-commit latency must not use Kafka broker timestamp as a substitute;
it measures process-local internal transition time between completion handling and
successful settlement/commit.

When process mode is inactive, process-batch metrics are exported as zero-valued
gauges rather than omitted. This keeps dashboards stable, but operators should
interpret those zeros as "not active" unless process mode is in use.

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
