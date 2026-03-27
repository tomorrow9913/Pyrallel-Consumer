# Index

이 문서는 `pyrallel-consumer` blueprint의 전체 목차다.
루트 문서는 전역 합의와 읽기 안내를 담당하고, 실제 구현 계약은 `features/` 아래 subfeature 문서가 담당한다.

## 루트 공통 문서

| 문서 | 이 문서가 답하는 질문 |
| --- | --- |
| [01-overview.md](./01-overview.md) | `pyrallel-consumer`는 왜 필요한가, 어떤 종류의 라이브러리인가 |
| [02-requirements.md](./02-requirements.md) | 전역 요구사항, canonical 용어, MVP 경계는 무엇인가 |
| [03-architecture.md](./03-architecture.md) | 전체 구조는 어떻게 나뉘고 어떤 흐름으로 동작하는가 |
| [04-open-decisions.md](./04-open-decisions.md) | 구현 전에 잠가야 하는 정책값과 운영 기준은 무엇인가 |
| [99-context-restoration.md](./99-context-restoration.md) | 기존 README/PRD/current code 사이의 drift와 문서 재배치 맥락은 무엇인가 |

## feature / subfeature 문서

### `01-ingress`

- [01-kafka-runtime-ingest](./features/01-ingress/01-kafka-runtime-ingest/00-index.md)
  Kafka poll loop, topic validation, consume/pause/resume, facade bootstrap 경계
- [02-ordered-work-scheduling](./features/01-ingress/02-ordered-work-scheduling/00-index.md)
  ordering mode, virtual partition queue, blocking offset 우선 스케줄링

### `02-reliability`

- [01-offset-commit-state](./features/02-reliability/01-offset-commit-state/00-index.md)
  HWM, gap, true lag, metadata snapshot, commit-safe state 추적
- [02-rebalance-retry-dlq](./features/02-reliability/02-rebalance-retry-dlq/00-index.md)
  epoch fencing, revoke grace, retry/backoff, DLQ publish와 failure recovery

### `03-execution`

- [01-async-execution-engine](./features/03-execution/01-async-execution-engine/00-index.md)
  `asyncio` task 기반 실행, semaphore, timeout, graceful shutdown
- [02-process-execution-engine](./features/03-execution/02-process-execution-engine/00-index.md)
  multiprocessing, msgpack micro-batch, picklable worker, worker recycle

### `04-tooling`

- [01-observability-metrics](./features/04-tooling/01-observability-metrics/00-index.md)
  `SystemMetrics`, Prometheus exporter, alert/tuning 관측 surface
- [02-benchmark-runtime](./features/04-tooling/02-benchmark-runtime/00-index.md)
  baseline/async/process 비교 벤치마크, TUI, profiling, 결과 해석

## 빠른 읽기 가이드

- 서비스 정의와 큰 그림부터 보려면 `01-overview.md`
- 전역 요구사항과 포함/제외 범위를 먼저 보려면 `02-requirements.md`
- 레이어와 처리 흐름을 먼저 보려면 `03-architecture.md`
- Kafka ingest와 facade 경계만 보려면 `features/01-ingress/01-kafka-runtime-ingest`
- ordering과 scheduling 정책만 보려면 `features/01-ingress/02-ordered-work-scheduling`
- offset correctness와 commit semantics만 보려면 `features/02-reliability`
- async/process 실행 엔진 차이만 보려면 `features/03-execution`
- 운영 지표와 벤치마크 surface만 보려면 `features/04-tooling`

## 읽는 순서

1. 루트 문서 `01/02/03/04`를 읽는다.
2. 필요한 feature 그룹으로 이동한다.
3. subfeature 안에서는 `00-index -> 01-requirements -> 02-architecture -> 03-design` 순으로 읽는다.
