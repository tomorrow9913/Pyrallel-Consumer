# Requirements

## 1. 문서 목적

이 문서는 `pyrallel-consumer`의 전역 요구사항을 요약하는 문서다.
feature에 들어가기 어려운 공통 요구사항과 이번 릴리스의 범위를 고정하고, 세부 필드와 정책은 각 subfeature 문서로 안내한다.

## 2. 문제 정의

Python 환경에서 Kafka 메시지를 병렬 처리하려면 보통 아래 셋 중 하나를 포기하게 된다.

- key 또는 partition ordering
- rebalance/restart 이후의 오프셋 정확성
- workload 특성에 맞는 실행 모델 선택

특히 CPU-bound workload는 GIL 때문에 async만으로 해결되지 않고, process 분리로 가면 Kafka Control Plane과 worker 실행 경계가 쉽게 섞인다. `pyrallel-consumer`는 이 문제를 라이브러리 내부의 명확한 plane 분리로 해결해야 한다.

## 3. 서비스 정의

`pyrallel-consumer`는 Kafka 메시지를 읽어 `WorkItem`으로 정규화하고, ordering-aware scheduling을 거쳐, runtime-selectable execution engine에서 worker를 병렬 실행한 뒤, contiguous-safe offset만 커밋하는 병렬 consumer 라이브러리다.

이 라이브러리는 사용자에게 async/process worker 선택권을 주지만, Control Plane의 correctness 책임은 라이브러리 내부에서 일관되게 유지한다.

## 4. canonical 용어

| 용어 | 의미 |
| --- | --- |
| `Control Plane` | Kafka consume, rebalance, offset commit, scheduling state를 관리하는 계층 |
| `Execution Plane` | worker 실행과 completion 전달을 담당하는 계층 |
| `WorkItem` | worker에 제출되는 canonical 처리 단위 |
| `CompletionEvent` | worker 완료 결과를 Control Plane으로 되돌리는 canonical 이벤트 |
| `HWM` | contiguous-safe high-water mark, 즉 안전하게 커밋 가능한 최대 연속 오프셋 |
| `gap` | 완료됐지만 앞선 offset 미완료 때문에 커밋되지 못하는 구간 |
| `blocking offset` | 현재 HWM 전진을 막는 가장 낮은 오프셋 |
| `epoch fencing` | 리밸런스 이후 stale completion을 버리기 위한 partition generation 검증 |
| `metadata_snapshot` | sparse completed offset을 Kafka commit metadata에 인코딩하는 전략 |
| `DLQ` | 최종 실패 메시지를 보내는 dead-letter topic |

## 5. 전역 요구사항

- Control Plane은 현재 어떤 실행 엔진이 선택되었는지 구체 타입 수준에서 인지하지 않아야 한다.
- `ExecutionEngine.submit()`이 block 가능하다는 전제를 문서와 구현이 함께 유지해야 한다.
- ordering mode는 `key_hash`, `partition`, `unordered` 세 가지를 canonical surface로 유지해야 한다.
- 오프셋 커밋은 contiguous-safe state만 기준으로 해야 하며, sparse completion은 보조 메타데이터로만 다뤄야 한다.
- rebalance 이후 stale completion은 epoch mismatch로 버려져야 한다.
- retry와 DLQ는 worker 실패를 offset-loss 없이 처리하기 위한 reliability surface여야 한다.
- metrics는 Kafka 기본 lag 대신 true lag, gap, blocking duration, backpressure 상태를 드러내야 한다.
- benchmark surface는 baseline/async/process를 동일 workload 조건에서 비교 가능하게 유지해야 한다.

## 6. feature별 최소 보장 요약

| subfeature | 최소 보장 |
| --- | --- |
| `01-ingress/01-kafka-runtime-ingest` | single topic ingest, pause/resume, facade bootstrap, payload cache |
| `01-ingress/02-ordered-work-scheduling` | ordering-aware virtual queue, blocking-first scheduling, starvation 방지 |
| `02-reliability/01-offset-commit-state` | HWM/gap 계산, metadata encoding, true lag 계산 |
| `02-reliability/02-rebalance-retry-dlq` | epoch fencing, revoke grace, retry/backoff, DLQ final handling |
| `03-execution/01-async-execution-engine` | coroutine worker contract, timeout/retry, graceful shutdown |
| `03-execution/02-process-execution-engine` | picklable sync worker, micro-batching, crash/timeout isolation |
| `04-tooling/01-observability-metrics` | `SystemMetrics`, Prometheus exporter, 운영 alert 관점 |
| `04-tooling/02-benchmark-runtime` | CLI/TUI benchmark, profiling hooks, reproducible JSON summary |

## 7. 입력/출력 경계

입력:

- Kafka topic에서 읽은 message key/value/offset/partition
- 사용자 정의 worker function (`async def` 또는 picklable sync callable)
- `KafkaConfig`와 nested execution/process/metrics 설정
- 운영자가 제공하는 monitoring stack, benchmark CLI 옵션

출력:

- worker completion success/failure 이벤트
- contiguous-safe offset commit와 optional metadata snapshot
- DLQ publish payload 및 headers
- `SystemMetrics` / `PartitionMetrics`
- benchmark JSON summary와 profiling artifacts

## 8. MVP 범위

포함 범위:

- single topic 기준 `PyrallelConsumer` facade
- async/process dual execution model
- ordering-aware scheduling과 backpressure
- HWM/gap 기반 commit correctness
- epoch fencing과 revoke graceful commit
- retry/backoff와 DLQ publish
- Prometheus exporter helper
- baseline/async/process 비교 benchmark CLI/TUI

제외 범위:

- multi-topic subscription orchestration
- exactly-once external side effect 보장
- distributed multi-node scheduling
- facade 레벨의 자동 metrics HTTP wiring
- storage-backed work replay journal
- benchmark 결과를 release gate로 강제하는 CI 자동화

## 9. acceptance 기준

- async/process 전환이 있어도 Control Plane의 canonical 용어와 책임 경계가 루트와 feature 문서에서 일관돼야 한다.
- `HWM`, `gap`, `blocking offset`, `epoch fencing` 정의가 문서 전체에서 흔들리지 않아야 한다.
- retry/DLQ와 offset correctness의 관계가 명확해야 하며, “DLQ publish 실패 시 commit하지 않는다”는 원칙이 문서에 반영돼야 한다.
- metrics 문서는 Kafka 기본 lag 대신 true lag 해석을 기준으로 삼아야 한다.
- benchmark 문서는 profiling overhead와 TPS/latency 해석 차이를 함께 설명해야 한다.

## 10. 읽기 안내

### `01-ingress`

- Kafka poll loop와 facade bootstrap
- ordering mode와 가상 큐 스케줄링
- WorkManager와 BrokerPoller의 역할 분리

상세:

- [01-ingress/01-kafka-runtime-ingest](features/01-ingress/01-kafka-runtime-ingest/00-index.ko.md)
- [01-ingress/02-ordered-work-scheduling](features/01-ingress/02-ordered-work-scheduling/00-index.ko.md)

### `02-reliability`

- HWM, gap, metadata snapshot
- epoch fencing과 final graceful commit
- retry/backoff와 DLQ 경계

상세:

- [02-reliability/01-offset-commit-state](features/02-reliability/01-offset-commit-state/00-index.ko.md)
- [02-reliability/02-rebalance-retry-dlq](features/02-reliability/02-rebalance-retry-dlq/00-index.ko.md)

### `03-execution`

- async/process worker 계약
- timeout, shutdown, isolation 제약

상세:

- [03-execution/01-async-execution-engine](features/03-execution/01-async-execution-engine/00-index.ko.md)
- [03-execution/02-process-execution-engine](features/03-execution/02-process-execution-engine/00-index.ko.md)

### `04-tooling`

- metrics/exporter와 운영 가이드
- benchmark/profiling과 결과 해석

상세:

- [04-tooling/01-observability-metrics](features/04-tooling/01-observability-metrics/00-index.ko.md)
- [04-tooling/02-benchmark-runtime](features/04-tooling/02-benchmark-runtime/00-index.ko.md)
