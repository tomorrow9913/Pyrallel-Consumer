# Kafka Runtime Ingest Requirements

## 1. 문서 목적

이 문서는 `kafka-runtime-ingest`의 책임과 요구사항을 정의한다.
이 subfeature는 Kafka에서 메시지를 읽어 처리 가능한 `WorkItem` 입력으로 넘기는 entrypoint다.

## 2. 책임

- `PyrallelConsumer` facade가 config, worker, topic을 받아 core 컴포넌트를 조립해야 한다.
- `BrokerPoller`는 Kafka consumer loop의 단일 진입점이어야 한다.
- ingest runtime은 topic validation, consumer/producer/admin 초기화, main consume loop, completion monitor lifecycle을 관리해야 한다.
- DLQ `full` payload mode를 지원하기 위해 bounded raw payload cache를 유지해야 한다.

## 3. 기능 요구사항

- 단일 consume topic을 기준으로 Kafka consumer를 초기화할 수 있어야 한다.
- `KafkaConfig`와 nested config를 바탕으로 consumer/producer 설정을 생성할 수 있어야 한다.
- poll loop는 idle일 때와 backlog가 있을 때 다른 cadence로 동작해야 한다.
- backpressure가 발생하면 `pause`, 회복되면 `resume` 할 수 있어야 한다.
- topic 이름 검증 실패는 worker 실행 전에 즉시 surface 되어야 한다.
- facade `stop()`은 poller 정지와 execution engine shutdown을 함께 처리해야 한다.

## 4. 비기능 요구사항

- ingest runtime은 execution mode별 concrete implementation detail을 직접 품지 않아야 한다.
- Kafka client lifecycle은 예외 발생 시에도 leak 없이 종료 경로를 가져야 한다.
- payload cache는 bounded memory 정책을 가져야 하며, 예산 초과 시 오래된 항목부터 축출되어야 한다.

## 5. 입력/출력 경계

입력:

- `KafkaConfig`
- user worker function
- consume topic 이름
- Kafka broker에서 poll한 message key/value/metadata

출력:

- `WorkItem` 생성을 위한 message normalization 결과
- `WorkManager`로 전달되는 ingest workload
- DLQ publish를 위한 raw payload cache entry
- `SystemMetrics` projection에 필요한 fetch state

## 6. MVP 경계

포함:

- single-topic facade bootstrap
- poll loop + completion monitor
- bounded raw payload cache
- manual producer/admin 초기화

제외:

- multi-topic consumer orchestration
- consumer group discovery UI
- auto metrics exporter bootstrapping

## 7. acceptance 기준

- `PyrallelConsumer`가 `ExecutionEngine`, `WorkManager`, `BrokerPoller`를 조립하는 책임만 가진다는 점이 문서에 명확해야 한다.
- `BrokerPoller`가 Kafka entrypoint이자 commit coordinator라는 점이 드러나야 한다.
- raw payload cache가 DLQ `full` mode 보조 수단이지 무한 보존 계층이 아니라는 점이 명시돼야 한다.
