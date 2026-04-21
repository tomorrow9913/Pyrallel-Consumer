# Overview

이 문서는 `pyrallel-consumer`의 루트 `02/03/04` 문서를 빠르게 요약해 주는 안내 문서다.
처음 읽는 사람은 이 문서만으로도 이 프로젝트가 어떤 종류의 라이브러리인지 이해해야 하고, 이미 컨텍스트가 있는 참여자는 필요한 feature 문서로 바로 이동할 수 있어야 한다.

## 1. 한 줄 정의

`pyrallel-consumer`는 Python에서 Kafka 메시지를 고처리량으로 병렬 처리하되, 오프셋 정확성과 key/partition ordering 보장을 유지하려는 runtime-selectable parallel consumer 라이브러리다.

## 2. 왜 이 라이브러리가 필요한가

기본 Kafka consumer는 파티션 단위 순차 처리에는 강하지만, 다음 요구를 동시에 만족시키기 어렵다.

- 파티션 수보다 더 높은 병렬성
- key 기준 순서 보장
- 리밸런스 이후에도 안전한 오프셋 커밋
- I/O-bound와 CPU-bound workload에 다른 실행 모델 적용
- 운영자가 병렬 처리 병목을 관찰할 수 있는 지표

`pyrallel-consumer`는 바로 이 조합을 라이브러리 레벨에서 제공한다.

## 3. 이 라이브러리가 아닌 것

- Kafka broker 위에 분산 실행을 새로 얹는 orchestration system은 아니다.
- exactly-once side effect를 보장하는 트랜잭션 엔진은 아니다.
- worker business logic 자체를 추상화하거나 통일하는 프레임워크는 아니다.
- Prometheus exporter helper는 자동 wiring되지만, dashboard provisioning과 compose 운영까지 모두 내장한 완성형 운영 플랫폼은 아니다.

이 네 줄은 단순한 제외 범위 목록이 아니라, 이 라이브러리의 책임 경계를 고정하기 위한 설명이다.

- `분산 orchestration system이 아니다`
  `pyrallel-consumer`는 하나의 consumer runtime 안에서 병렬성을 높이는 라이브러리다. Kafka broker 위에 별도 스케줄러, 분산 coordinator, multi-node worker fabric을 올려서 작업을 분배하는 시스템을 만들려는 것이 아니다.
- `exactly-once side effect 엔진이 아니다`
  이 라이브러리는 contiguous-safe offset commit과 stale completion drop으로 at-least-once correctness를 지키지만, 외부 DB write나 HTTP call까지 exactly-once로 보장하지는 않는다. 따라서 downstream side effect는 여전히 idempotent하게 설계해야 한다.
- `worker framework가 아니다`
  이 라이브러리는 worker 실행 경계와 runtime contract를 제공할 뿐, 사용자 비즈니스 로직을 DSL로 감싸거나 공통 추상 타입 하나로 통일하려 하지 않는다. async worker와 process worker가 서로 다른 형태를 유지하는 것도 이 경계를 지키기 위해서다.
- `완성형 운영 플랫폼이 아니다`
  `PyrallelConsumer`는 `config.metrics.enabled=True`일 때 Prometheus exporter를 자동으로 띄우지만, dashboard provisioning, 운영 alert 정책, compose stack 수명주기까지 하나의 제품 surface로 모두 추상화하지는 않는다. runtime core와 운영 integration은 여전히 분리해서 다룬다.

## 4. 루트 문서 읽기 안내

- [02-requirements.md](02-requirements.ko.md)
  문제 정의, 서비스 정의, canonical 용어, 전역 요구사항, MVP 범위와 acceptance 기준을 다룬다.
- [03-architecture.md](03-architecture.ko.md)
  전체 레이어, Control Plane과 Execution Plane 경계, 현재 코드 구조와 target-state 문서 구조의 관계를 다룬다.
- [04-open-decisions.md](04-open-decisions.ko.md)
  metadata snapshot 기본값, metrics wiring, commit strategy, benchmark gate 같은 미결정 정책을 모은다.

## 5. 현재 릴리스 기준

현재 문서화 기준의 핵심은 아래 네 가지다.

- `Control Plane invariant`
  실행 엔진이 바뀌어도 Kafka consume, ordering, offset correctness 로직은 같은 경로를 유지해야 한다.
- `Dual execution model`
  `AsyncExecutionEngine`과 `ProcessExecutionEngine`을 같은 릴리스에서 제공한다.
- `Offset correctness first`
  높은 처리량보다 HWM, gap, epoch fencing, revoke safety가 우선한다.
- `Operational visibility`
  true lag, gap count, blocking duration, benchmark 결과를 운영자가 해석할 수 있어야 한다.

## 6. feature 그룹 한눈에 보기

| feature 그룹 | 핵심 역할 |
| --- | --- |
| `01-ingress` | Kafka ingest loop, ordering mode 해석, WorkManager scheduling |
| `02-reliability` | offset state, metadata snapshot, rebalance fencing, retry/DLQ |
| `03-execution` | async/process 실행 엔진 계약과 제약 |
| `04-tooling` | metrics/exporter, 운영 가이드, benchmark and profiling surface |

## 7. 읽기 순서

1. 루트 문서 `02/03/04`를 읽고 전체 구조와 경계를 이해한다.
2. `00-index.md`로 돌아가 필요한 feature/subfeature로 이동한다.
3. subfeature 안에서는 `00-index -> 01-requirements -> 02-architecture -> 03-design` 순으로 읽는다.
