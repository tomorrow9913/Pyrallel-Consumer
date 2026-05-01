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
