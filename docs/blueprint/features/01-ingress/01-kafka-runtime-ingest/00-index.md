# Kafka Runtime Ingest Index

이 문서는 `kafka-runtime-ingest` subfeature의 목차다.
이 subfeature는 `PyrallelConsumer` facade에서 시작해 `BrokerPoller`가 Kafka message를 읽고 Control Plane으로 넘기는 구간을 다룬다.

## 이 subfeature가 답하는 질문

- 라이브러리는 어떤 입력을 받아 runtime을 초기화하는가
- Kafka consumer/producer/admin은 어디서 만들어지고 어떤 책임을 가지는가
- poll loop는 언제 pause/resume 되는가
- raw payload cache와 DLQ publish 준비는 어디서 이루어지는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | ingest runtime의 책임, 범위, acceptance 기준 |
| [02-architecture.md](./02-architecture.md) | facade, BrokerPoller, Kafka client, completion monitor 관계 |
| [03-design.md](./03-design.md) | config key, 입력/출력 계약, raw payload cache 규칙 |

## 빠른 읽기 분기

- facade constructor와 bootstrap 순서가 궁금하면 `02-architecture.md`
- Kafka 관련 설정 키와 runtime contract가 궁금하면 `03-design.md`
- 이 subfeature의 책임 범위가 궁금하면 `01-requirements.md`
