# Rebalance Retry DLQ Design

## 1. 문서 역할

이 문서는 리밸런스/재시도/DLQ 경계의 구현 직전 계약을 고정한다.
reliability policy를 조정하거나 운영 가이드를 바꿀 때 먼저 읽는 문서다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY` | revoke/assign state 보존 전략 | `contiguous_only` |
| `EXECUTION_MAX_REVOKE_GRACE_MS` | revoke graceful commit 마감 시간 | `500` |
| `EXECUTION_MAX_RETRIES` | worker 최대 재시도 횟수 | `3` |
| `EXECUTION_RETRY_BACKOFF_MS` | 기본 backoff | `1000` |
| `EXECUTION_MAX_RETRY_BACKOFF_MS` | backoff 상한 | `30000` |
| `EXECUTION_RETRY_JITTER_MS` | jitter 범위 | `200` |
| `KAFKA_DLQ_ENABLED` | DLQ 사용 여부 | `true` |
| `KAFKA_DLQ_TOPIC_SUFFIX` | DLQ suffix | `.dlq` |
| `KAFKA_DLQ_PAYLOAD_MODE` | `full` 또는 `metadata_only` | `full` |

## 3. stale completion 규칙

- completion의 `(topic, partition)`가 현재 assignment에 없으면 버린다.
- completion의 `epoch`이 현재 partition epoch과 다르면 버린다.
- stale completion drop은 warning/debug 수준의 관측 대상이지만 commit state를 바꾸면 안 된다.

## 4. retry/backoff 규칙

- attempt는 `1`부터 시작하는 1-based count다.
- 재시도는 성공하거나 `max_retries`를 소진할 때까지 반복된다.
- exponential mode에서는 `retry_backoff_ms * 2^(attempt-1)`을 사용하고 상한을 적용한다.
- jitter는 추가 지연만 더하며, 기본 backoff를 줄이지 않는다.

## 5. DLQ header 계약

최종 실패 메시지는 최소 아래 헤더를 가져야 한다.

| 헤더 | 의미 |
| --- | --- |
| `x-error-reason` | 최종 오류 문자열 |
| `x-retry-attempt` | 최종 시도 횟수 |
| `source-topic` | 원본 topic |
| `partition` | 원본 partition |
| `offset` | 원본 offset |
| `epoch` | 처리 시점의 partition epoch |

## 6. liveness 우선 규칙

- revoke deadline을 넘기면 contiguous-safe HWM만 우선 커밋한다.
- sparse metadata snapshot은 deadline 초과 시 포기될 수 있다.
- 이 동작은 재처리를 허용하지만 offset corruption은 허용하지 않는다.
