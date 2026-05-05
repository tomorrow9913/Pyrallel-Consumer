# Observability Metrics Design

## 1. 문서 역할

이 문서는 현재 canonical metric surface, runtime snapshot 경계, 운영 해석 규칙을 고정한다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `METRICS_ENABLED` | exporter HTTP 서버 시작 여부 | `false` |
| `METRICS_PORT` | exporter 포트 | `9091` |
| `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | 전체 in-flight work의 configured hard ceiling | `1000` |
| `PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__ENABLED` | adaptive backpressure policy telemetry/live limit 제어 활성화 | `false` |
| `PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED` | adaptive concurrency policy telemetry/live limit 제어 활성화 | `false` |
| `PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED` | poison-message runtime snapshot section 활성화 | `false` |
| `PARALLEL_CONSUMER_DIAG_LOG_EVERY` | 상태 로그 주기 | `1000` |
| `PARALLEL_CONSUMER_BLOCKING_WARN_SECONDS` | blocking warning 임계 | `5.0` |
| `PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS` | blocking duration hard limit 힌트 | `0` |

canonical Python config가 primary API다. env var는 같은 config object를 채우는
settings input이다.

| Python config field | Env key | 비고 |
| --- | --- | --- |
| `KafkaConfig.metrics.enabled` / `MetricsConfig.enabled` | `METRICS_ENABLED` | library Prometheus HTTP exporter 활성화 |
| `KafkaConfig.metrics.port` / `MetricsConfig.port` | `METRICS_PORT` | process 안 exporter cache가 사용하는 port |
| `ParallelConsumerConfig.execution.max_in_flight_messages` | `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | control-plane in-flight work의 static hard ceiling |
| `ParallelConsumerConfig.adaptive_backpressure.enabled` | `PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__ENABLED` | adaptive backpressure telemetry와 live limit control 활성화 |
| `ParallelConsumerConfig.adaptive_concurrency.enabled` | `PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED` | adaptive concurrency telemetry와 live limit control 활성화 |
| `ParallelConsumerConfig.poison_message.enabled` | `PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED` | poison-message runtime snapshot section 활성화 |

exporter instance는 process 안에서 metrics port별로 cache된다. 현재 metric surface에는
`consumer_id` / `instance` label이 없다. 따라서 같은 exporter port를 공유하는 여러
consumer는 registry를 공유한다. 같은 label을 가진 counter는 aggregate되고, 같은 label을
가진 gauge는 마지막 snapshot이 이긴다. topic/partition label과 no-label gauge를
process-level signal로 해석할 수 있을 때가 아니라면 consumer마다 metrics port를 분리한다.

## 3. canonical metric surface

### 3.1 Completion/queue/partition 상태

| Metric | Type | Labels | 의미 |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | completion 성공/실패 수 |
| `consumer_commit_failures_total` | Counter | `topic`, `partition`, `reason` | 최종 Kafka commit 실패 수 |
| `consumer_dlq_publish_failures_total` | Counter | `topic`, `partition` | terminal DLQ publish 실패 수 |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | submit부터 completion까지 지연 |
| `consumer_in_flight_count` | Gauge | 없음 | 전체 in-flight 수 |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | true lag |
| `consumer_gap_count` | Gauge | `topic`, `partition` | outstanding gap 수 |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | virtual queue backlog |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | blocking duration |
| `consumer_backpressure_active` | Gauge | 없음 | `1=paused`, `0=running` |
| `consumer_metadata_size_bytes` | Gauge | `topic` | commit metadata payload 크기 |

### 3.2 Resource signal / adaptive 상태

| Metric | Type | Labels | 의미 |
| --- | --- | --- | --- |
| `consumer_resource_signal_status` | Gauge | `status` | resource signal one-hot availability 상태 |
| `consumer_resource_cpu_utilization_ratio` | Gauge | 없음 | 최신 CPU utilization ratio |
| `consumer_resource_memory_utilization_ratio` | Gauge | 없음 | 최신 memory utilization ratio |
| `consumer_adaptive_backpressure_configured_max_in_flight` | Gauge | 없음 | adaptive backpressure 설정 ceiling |
| `consumer_adaptive_backpressure_effective_max_in_flight` | Gauge | 없음 | adaptive backpressure 실시간 ceiling |
| `consumer_adaptive_backpressure_min_in_flight` | Gauge | 없음 | adaptive backpressure 최소 하한 |
| `consumer_adaptive_backpressure_scale_up_step` | Gauge | 없음 | adaptive backpressure 상승 step |
| `consumer_adaptive_backpressure_scale_down_step` | Gauge | 없음 | adaptive backpressure 하향 step |
| `consumer_adaptive_backpressure_cooldown_ms` | Gauge | 없음 | adaptive backpressure 쿨다운(ms) |
| `consumer_adaptive_backpressure_lag_scale_up_threshold` | Gauge | 없음 | adaptive backpressure scale-up을 유도하는 lag 임계값 |
| `consumer_adaptive_backpressure_low_latency_threshold_ms` | Gauge | 없음 | adaptive backpressure 저지연 임계값(ms) |
| `consumer_adaptive_backpressure_high_latency_threshold_ms` | Gauge | 없음 | adaptive backpressure 고지연 임계값(ms) |
| `consumer_adaptive_backpressure_avg_completion_latency_seconds` | Gauge | 없음 | 현재 adaptive backpressure 의사결정 입력값 |
| `consumer_adaptive_backpressure_last_decision` | Gauge | `decision` | 마지막 adaptive backpressure decision one-hot |
| `consumer_adaptive_concurrency_configured_max_in_flight` | Gauge | 없음 | adaptive concurrency 설정 ceiling |
| `consumer_adaptive_concurrency_effective_max_in_flight` | Gauge | 없음 | adaptive concurrency 실시간 ceiling |
| `consumer_adaptive_concurrency_min_in_flight` | Gauge | 없음 | adaptive concurrency 최소 하한 |
| `consumer_adaptive_concurrency_scale_up_step` | Gauge | 없음 | adaptive concurrency 상승 step |
| `consumer_adaptive_concurrency_scale_down_step` | Gauge | 없음 | adaptive concurrency 하향 step |
| `consumer_adaptive_concurrency_cooldown_ms` | Gauge | 없음 | adaptive concurrency 쿨다운(ms) |

### 3.3 Process-batch runtime 상태

| Metric | Type | Labels | 의미 |
| --- | --- | --- | --- |
| `consumer_process_batch_flush_count` | Gauge | `reason` | process engine snapshot 안의 cumulative flush count |
| `consumer_process_batch_avg_size` | Gauge | 없음 | 평균 process batch flush size |
| `consumer_process_batch_last_size` | Gauge | 없음 | 가장 최근 process batch size |
| `consumer_process_batch_last_wait_seconds` | Gauge | 없음 | 가장 최근 process batch wait time |
| `consumer_process_batch_buffered_items` | Gauge | 없음 | 현재 buffered process item 수 |
| `consumer_process_batch_buffered_age_seconds` | Gauge | 없음 | 현재 process batch buffer age |
| `consumer_process_batch_last_main_to_worker_ipc_seconds` | Gauge | 없음 | 최근 main-to-worker IPC time |
| `consumer_process_batch_avg_main_to_worker_ipc_seconds` | Gauge | 없음 | 평균 main-to-worker IPC time |
| `consumer_process_batch_last_worker_exec_seconds` | Gauge | 없음 | 최근 process worker execution time |
| `consumer_process_batch_avg_worker_exec_seconds` | Gauge | 없음 | 평균 process worker execution time |
| `consumer_process_batch_last_worker_to_main_ipc_seconds` | Gauge | 없음 | 최근 worker-to-main IPC time |
| `consumer_process_batch_avg_worker_to_main_ipc_seconds` | Gauge | 없음 | 평균 worker-to-main IPC time |
| `consumer_process_batch_transport_mode` | Gauge | `mode` | one-hot process execution transport diagnostic. 현재는 `worker_pipes`만 사용 |
| `consumer_process_batch_support_state` | Gauge | `state` | active process execution diagnostic의 one-hot support boundary state |
| `consumer_process_batch_timer_flush_supported` | Gauge | 없음 | active process execution path가 timer flush를 지원하면 `1` |
| `consumer_process_batch_demand_flush_supported` | Gauge | 없음 | active process execution path가 demand flush를 지원하면 `1` |
| `consumer_process_batch_recycle_supported` | Gauge | 없음 | active process execution path가 recycle setting을 지원하면 `1` |

`consumer_process_batch_flush_count`는 process-engine snapshot 안에서는 cumulative 값이지만
현재 exporter에서는 Gauge다. exporter가 `ProcessBatchMetrics` snapshot을 그대로 투영하고,
process metrics가 없거나 runtime/process가 재시작되면 series를 `0`으로 되돌리기 때문이다.
이 값을 snapshot gauge로 해석하고, `rate()` / `increase()`는 reset 가능성을 고려해서 사용한다.

### 3.4 Internal pipeline diagnostics sidecar

| Metric | Type | Labels | 의미 |
| --- | --- | --- | --- |
| `pyrallel_pipeline_stage_messages` | Gauge | `stage`, `engine_type` | support되는 bounded pipeline stage별 sidecar message count |
| `pyrallel_pipeline_blocked_messages` | Gauge | `reason`, `engine_type` | support되는 bounded blocker reason별 sidecar blocked count |
| `pyrallel_pipeline_dispatch_capacity_blocked_messages` | Gauge | `reason`, `engine_type` | `max_in_flight` 같은 bounded reason의 dispatch-capacity pressure |
| `pyrallel_pipeline_section_support_state` | Gauge | `section`, `state`, `engine_type` | sidecar section별 one-hot support state |
| `pyrallel_pipeline_worker_capacity_units` | Gauge | `state`, `engine_type` | worker diagnostics가 support될 때 `total`, `executing`, `admitted` aggregate worker capacity count |

Pipeline metrics는 `PyrallelConsumer.get_pipeline_diagnostics()` /
`BrokerPoller.get_pipeline_diagnostics()` sidecar를 bounded Prometheus projection으로
투영한 것이다. 새로운 source of truth를 만들지 않으며 `RuntimeSnapshot` v1을 바꾸지
않는다. exporter는 support state가 `supported`인 section/stage의 observed count gauge만
내보낸다. `not_implemented`와 `unavailable` section은
`pyrallel_pipeline_section_support_state`로 표현하고, observed count gauge는 `0`으로
export하지 않고 absent로 둔다.

`workers.top_k_loads`, `subqueues.top_k_depths`, stage/blocker/settlement age,
raw topic, partition, key, route, worker id, subqueue id, offset, exception text는
snapshot/debug 전용이며 Prometheus label로 내보내면 안 된다. Worker occupancy는
aggregate `pyrallel_pipeline_worker_capacity_units`로만 노출한다.

### 3.5 label value contract

label 값은 bounded해야 한다. 현재 canonical allowed value는 다음과 같다.

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
| `pyrallel_pipeline_section_support_state.section` | `stages`, `blocked`, `subqueues`, `dispatch_capacity`, `admission`, `workers`, `settlement` |
| `pyrallel_pipeline_section_support_state.state` | `supported`, `unavailable`, `not_implemented` |
| `pyrallel_pipeline_worker_capacity_units.state` | `total`, `executing`, `admitted` |

`first_sample_pending`은 warm-up과 failure를 구분할 수 있는 custom provider를 위한 public
resource-signal enum 값이다. built-in null provider는 `unavailable`을 보고한다.

### 3.6 metric ownership

| Metric group | 계산 계층 | 투영 계층 | 경계 |
| --- | --- | --- | --- |
| completion counter와 processing latency | `WorkManager` completion ledger | `PrometheusMetricsExporter` | latency는 WorkManager dispatch timestamp에서 시작해 completion 처리 시점에 끝난다. |
| queue depth, in-flight, lag/gap/blocking duration | control-plane state를 읽는 `BrokerPoller` / `BrokerRuntimeSupport` | `PrometheusMetricsExporter` | `consumer_internal_queue_depth`는 partition-level virtual queue backlog다. |
| metadata size | commit metadata encoding path | `PrometheusMetricsExporter.update_metadata_size()` | topic별 가장 최근 offset-commit metadata payload size다. |
| adaptive/resource-signal gauge | adaptive controller와 resource-signal provider | `PrometheusMetricsExporter` | adaptive section이 disabled/absent면 zero-valued gauge와 backpressure `decision="disabled"`로 투영한다. |
| process-batch / IPC gauge | process execution engine의 `ProcessBatchMetrics` | `SystemMetrics.process_batch_metrics`와 `PrometheusMetricsExporter` | control plane은 process-engine internals를 보지 않고 DTO projection만 운반한다. |
| pipeline diagnostics sidecar gauge | `WorkManager`, execution engine diagnostics, `BrokerPoller` sidecar composition | `PrometheusMetricsExporter.update_pipeline_diagnostics()` | exporter는 support되는 bounded aggregate field만 투영하고 pipeline state를 계산하지 않는다. |

process mode가 비활성일 때 process-batch metrics는 omit하지 않고 zero-valued gauge로
export된다. dashboard 안정성을 위한 선택이며, 운영자는 process mode가 아닐 때의 zero를
"not active"로 해석해야 한다.

## 4. runtime snapshot 경계

`PyrallelConsumer.get_runtime_snapshot()`은 read-only `RuntimeSnapshot` projection을 반환한다.
문서화된 stable section은 다음과 같다.

- `queue`: `total_in_flight`, `total_queued`, live `max_in_flight`, `configured_max_in_flight`, `is_paused`, `is_rebalancing`, `ordering_mode`
- `retry`: `max_retries`, `retry_backoff_ms`, `exponential_backoff`, `max_retry_backoff_ms`, `retry_jitter_ms`
- `dlq`: `enabled`, `topic`, `payload_mode`, `message_cache_size_bytes`, `message_cache_entry_count`
- `partitions[]`: `tp`, `current_epoch`, `last_committed_offset`, `last_fetched_offset`, `true_lag`, `gaps`, `blocking_offset`, `blocking_duration_sec`, `queued_count`, `in_flight_count`, `min_in_flight_offset`
- optional `adaptive_backpressure`: configured ceiling, effective live limit, guardrails, latest decision, average completion latency 입력
- optional `adaptive_concurrency`: configured ceiling, effective live limit, scaling guardrails
- optional `process_batch_metrics`: process micro-batch runtime counter/timing
- optional `poison_message`: enablement, threshold, cooldown, open-circuit count

해석 규칙:

- `queue.max_in_flight`는 현재 live control-plane limit이다.
- `queue.configured_max_in_flight`는 정적 configured ceiling이다.
- adaptive concurrency/backpressure가 꺼져 있으면 optional section은 없어질 수 있다.
- runtime snapshot은 diagnostics surface이지 audit log, retry ledger, DLQ history, payload dump가 아니다.
- secure Kafka transport field, SASL/TLS secret, username, certificate/key path는 runtime snapshot에 포함하면 안 된다.

## 5. benchmark/runtime 노출 경계

- benchmark JSON summary는 선택된 observability evidence(`metrics_observations`, `final_lag`, `final_gap_count`)와 benchmark result row만 담는다.
- 이 JSON artifact는 full runtime snapshot API를 serialize하지 않는다.
- benchmark의 `--metrics-port` 노출은 Pyrallel harness convenience일 뿐이고 baseline run은 무시한다.
- production runtime exporter startup은 여전히 `KafkaConfig.metrics.enabled = True`가 필요하다.

## 6. 운영 해석 규칙

- `consumer_parallel_lag` 상승: 실제 처리 backlog 증가
- `consumer_gap_count` 상승: out-of-order completion 비용 증가
- `consumer_oldest_task_duration_seconds` 상승: poison path, hot key, downstream dependency blockage 의심
- `consumer_backpressure_active == 1`: ingress가 현재 live limit을 초과해 fetch intake가 pause됨
- configured/effective adaptive limit 차이가 크면 live controller가 throughput/latency tradeoff를 조정 중이라는 뜻이다.
- `consumer_resource_signal_status{status=\"available\"} == 0`이면 host telemetry가 unavailable/stale이므로 adaptive 로직은 fail-open으로 해석해야 한다.

## 7. alert 힌트

- backpressure active가 sustained window 동안 계속 1이면 alert
- lag/gap가 여러 분 동안 단조 증가하면 alert
- DLQ failure와 함께 blocking duration이 증가하면 poison path를 우선 점검
- `max_in_flight` 튜닝 시 adaptive gauge와 runtime snapshot queue field를 함께 보되, process count/semaphore size를 역산하려고 하지 않는다.
