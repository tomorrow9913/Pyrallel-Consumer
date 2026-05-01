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
| `KAFKA_SECURITY_PROTOCOL` | consumer/producer/admin에 공통 적용되는 allowlisted transport/auth mode | unset |
| `KAFKA_SASL_MECHANISMS` | 모든 Kafka client에 전달되는 optional SASL mechanism | unset |
| `KAFKA_SASL_USERNAME` | secure Kafka 배포용 optional SASL principal | unset |
| `KAFKA_SASL_PASSWORD` | `SecretStr`로 보관되고 redacted dump에서 제외되는 optional SASL secret | unset |
| `KAFKA_SSL_CA_LOCATION` | TLS broker 검증용 optional CA bundle 경로 | unset |
| `KAFKA_SSL_CERTIFICATE_LOCATION` | mTLS용 optional client certificate 경로 | unset |
| `KAFKA_SSL_KEY_LOCATION` | mTLS용 optional client private key 경로 | unset |
| `KAFKA_SSL_KEY_PASSWORD` | `SecretStr`로 보관되는 optional encrypted private-key secret | unset |
| `PARALLEL_CONSUMER_POLL_BATCH_SIZE` | poll batch 크기 | `1000` |
| `PARALLEL_CONSUMER_QUEUE_MAX_MESSAGES` | internal queue 상한 | `5000` |
| `PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES` | raw DLQ payload cache 상한 | `67108864` |
| `PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED` | `BrokerPoller`가 dedicated completion-monitor task를 만들지, consume loop inline drain만 쓸지 결정 | `true` |

secure Kafka config는 의도적으로 allowlist만 노출한다. `KafkaConfig`는
문서화된 TLS/SASL 필드만 받아서 consumer, producer, admin helper에 같은
`librdkafka` key로 번역한다. 임의의 generic passthrough client config는
이 ingress contract에 포함되지 않는다.

secure 필드가 비어 있거나 unset이면 runtime은 blank 값을 전달하지 않고
그 키를 생략해야 한다. password 계열 필드는 `SecretStr`로 유지하고,
redacted config snapshot에서는 `sasl.password`와 `ssl.key.password`를
제외해야 한다. 운영 문서는 secure mode가 켜졌다는 coarse state까지만
언급할 수 있고, principal/path/secret 값 자체를 예제·metrics·runtime
snapshot에 노출해서는 안 된다.

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

`start()`는 poller와 optional metrics loop를 시작한다.
`stop()`은 poller를 먼저 멈추고 final metrics snapshot을 publish한 뒤 execution engine을 종료한다.
`wait_closed()`는 fatal broker-loop error를 수동으로 surface 한다.
`get_runtime_snapshot()`은 poller가 만든 read-only diagnostics snapshot을 그대로 노출하며, Kafka client secret을 포함하지 않는다.

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

- `BrokerPoller.start()`가 Kafka client 생성과 task startup을 소유한다.
- `strict_completion_monitor_enabled=true`면 dedicated completion-monitor task를 시작한다.
- `strict_completion_monitor_enabled=false`면 별도 completion task는 만들지 않고, consume loop/shutdown drain 경로에서 completion을 비운다.
- consume loop는 idle 시 짧은 poll cadence를 쓰고, backlog가 있으면 즉시 drain 쪽으로 기운다.
- backpressure는 current load가 한계를 넘으면 pause하고 70% hysteresis 아래로 내려오면 resume한다.
- `stop()`은 facade/poller 양쪽에서 idempotent해야 한다.
- `wait_closed()`는 정상 종료 필수 단계가 아니라 diagnostics/fatal-error surfacing API다.
