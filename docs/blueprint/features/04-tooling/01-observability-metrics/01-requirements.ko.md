# Observability Metrics Requirements

## 1. 문서 목적

이 문서는 `observability-metrics`의 책임과 요구사항을 정의한다.
이 subfeature는 병렬 처리 런타임의 실제 상태를 운영자가 해석할 수 있는 형태로 노출하는 계층이다.

## 2. 책임

- Kafka 기본 lag로는 보이지 않는 병렬 처리 병목을 드러내야 한다.
- `SystemMetrics`와 `PartitionMetrics`를 canonical projection으로 유지해야 한다.
- Prometheus exporter helper를 통해 counter/gauge/histogram을 노출할 수 있어야 한다.
- 운영 가이드가 metric 해석과 tuning 행동으로 이어져야 한다.

## 3. 기능 요구사항

- true lag, gap count, internal queue depth, oldest blocking duration, backpressure 상태를 노출해야 한다.
- completion 성공/실패 수와 end-to-end latency를 측정할 수 있어야 한다.
- metadata snapshot payload 크기를 metric으로 볼 수 있어야 한다.
- metrics HTTP exporter는 설정으로 켜질 수 있어야 한다.

## 4. 비기능 요구사항

- exporter는 core facade의 필수 의존성이 아니어야 한다.
- metric 이름과 label은 문서와 구현이 일치해야 한다.
- 운영 문서는 지표 정의만이 아니라 alert와 tuning 관점을 함께 제공해야 한다.

## 5. 입력/출력 경계

입력:

- `SystemMetrics`
- completion event와 duration
- metadata size
- `MetricsConfig`

출력:

- Prometheus counter/gauge/histogram
- 운영자용 해석 규칙
- alert/tuning guide

## 6. acceptance 기준

- `true lag`와 Kafka 기본 lag 차이가 문서상 분명해야 한다.
- exporter가 helper이며 current facade 자동 wiring은 아니라는 점이 드러나야 한다.
- metric 이름과 운영 의미가 one-to-one로 연결돼야 한다.
