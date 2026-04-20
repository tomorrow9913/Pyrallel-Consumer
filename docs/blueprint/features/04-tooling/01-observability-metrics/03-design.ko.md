# Observability Metrics Design

## 1. 문서 역할

이 문서는 현재 canonical metric surface와 운영 해석 규칙을 고정한다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `METRICS_ENABLED` | exporter HTTP 서버 시작 여부 | `false` |
| `METRICS_PORT` | exporter 포트 | `9091` |
| `PARALLEL_CONSUMER_DIAG_LOG_EVERY` | 상태 로그 주기 | `1000` |
| `PARALLEL_CONSUMER_BLOCKING_WARN_SECONDS` | blocking warning 임계 | `5.0` |
| `PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS` | blocking duration hard limit 힌트 | `0` |

## 3. canonical metric surface

| Metric | Type | Labels | 의미 |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | completion 성공/실패 수 |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | submit부터 completion까지 지연 |
| `consumer_in_flight_count` | Gauge | 없음 | 전체 in-flight 수 |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | true lag |
| `consumer_gap_count` | Gauge | `topic`, `partition` | outstanding gap 수 |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | virtual queue backlog |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | blocking duration |
| `consumer_backpressure_active` | Gauge | 없음 | `1=paused` |
| `consumer_metadata_size_bytes` | Gauge | `topic` | commit metadata payload 크기 |

## 4. 운영 해석 규칙

- `consumer_parallel_lag` 상승: 실제 처리 backlog 증가
- `consumer_gap_count` 상승: out-of-order completion 비용 증가
- `consumer_oldest_task_duration_seconds` 상승: poison message 또는 hot key 의심
- `consumer_backpressure_active == 1`: ingress가 처리 용량을 초과함

## 5. alert 힌트

- backpressure active가 1분 이상 유지되면 alert
- lag/gap가 5분 이상 단조 증가하면 alert
- DLQ failure와 함께 blocking duration이 증가하면 poison path 우선 점검
