# Kafka Runtime Ingest Design

## 1. 문서 역할

이 문서는 facade/bootstrap과 Kafka ingest runtime이 노출하는 구현 직전 계약을 고정한다.
runtime lifecycle을 수정하거나 Kafka 설정 surface를 바꿀 때 먼저 읽는 문서다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `KAFKA_BOOTSTRAP_SERVERS` | Kafka broker 목록 | `localhost:9092` |
| `KAFKA_CONSUMER_GROUP` | consumer group id | `pyrallel-consumer-group` |
| `KAFKA_AUTO_OFFSET_RESET` | consume 시작 위치 | `earliest` |
| `KAFKA_ENABLE_AUTO_COMMIT` | Kafka auto commit 사용 여부 | `false` |
| `KAFKA_SESSION_TIMEOUT_MS` | session timeout | `60000` |
| `PARALLEL_CONSUMER_POLL_BATCH_SIZE` | poll batch 크기 | `1000` |
| `PARALLEL_CONSUMER_QUEUE_MAX_MESSAGES` | internal queue 상한 | `5000` |
| `PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES` | raw DLQ payload cache 상한 | `67108864` |

## 3. facade 계약

`PyrallelConsumer` constructor는 최소 아래 입력을 받는다.

| 입력 | 설명 |
| --- | --- |
| `config` | `KafkaConfig` 전체 설정 |
| `worker` | async 또는 sync worker |
| `topic` | single consume topic |

facade는 아래 순서로 core를 조립한다.

1. execution engine 생성
2. `WorkManager` 생성
3. `BrokerPoller` 생성

## 4. ingest 출력 계약

Kafka message에서 아래 정보는 loss 없이 Control Plane으로 넘어가야 한다.

| 필드 | 의미 |
| --- | --- |
| `topic` | source topic |
| `partition` | Kafka partition |
| `offset` | Kafka offset |
| `key` | ordering key 또는 partition-only ordering 판단 재료 |
| `payload` | worker에 전달할 실제 value |

## 5. raw payload cache 규칙

- cache key는 `(topic-partition, offset)` 조합이다.
- `dlq_enabled=true`이면서 `dlq_payload_mode=full`일 때만 raw payload를 보존한다.
- cache budget을 넘기면 가장 오래된 항목부터 제거한다.
- payload 하나가 전체 budget보다 크면 skip하고 warning만 남긴다.
- cache miss가 발생하더라도 commit correctness보다 payload preservation을 우선하지 않는다.

## 6. lifecycle 규칙

- `start()`는 poller loop를 기동한다.
- `stop()`은 poller stop 후 execution engine shutdown을 보장한다.
- `wait_closed()`는 background consume loop fatal error를 외부로 surface 하는 보조 API다.
