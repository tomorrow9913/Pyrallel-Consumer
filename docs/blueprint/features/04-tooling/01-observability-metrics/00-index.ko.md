# Observability Metrics Index

이 문서는 `observability-metrics` subfeature의 목차다.
이 subfeature는 `SystemMetrics`와 `PrometheusMetricsExporter`가 운영자에게 무엇을 보여줘야 하는지 다룬다.

## 이 subfeature가 답하는 질문

- Kafka 기본 lag 대신 어떤 지표를 봐야 하는가
- exporter는 현재 어디까지 자동화돼 있는가
- 어떤 metric이 어떤 병목을 설명하는가
- 운영자는 어떤 alert와 tuning 관점을 가져야 하는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](01-requirements.ko.md) | metrics surface의 책임과 acceptance 기준 |
| [02-architecture.md](02-architecture.ko.md) | `BrokerPoller.get_metrics()`와 exporter projection 관계 |
| [03-design.md](03-design.ko.md) | metric 이름, label, config key, 운영 해석 규칙 |

## 빠른 읽기 분기

- metric 이름과 의미를 바로 보려면 `03-design.md`
- exporter wiring 구조가 궁금하면 `02-architecture.md`
- 운영 surface의 범위를 알고 싶으면 `01-requirements.md`
