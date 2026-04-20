# Context Restoration

## 1. 문서 역할

이 문서는 `pyrallel-consumer` 문서만으로는 완전히 복원되지 않는 서비스 전용 메타 컨텍스트를 남긴다.
요구사항, 아키텍처, 설계 본문은 각 `01/02/03` 문서가 authoritative source이고, 이 문서는 문서군 전체를 어떤 맥락으로 읽어야 하는지 설명하는 보조 루트 문서다.

## 2. canonical source history

현재 `pyrallel-consumer` 문서군은 아래 기존 원문과 current code를 feature-first 구조로 재배치한 결과다.

- `Pyrallel-Consumer/README.md`
- `Pyrallel-Consumer/README.ko.md`
- `Pyrallel-Consumer/prd_dev.ko.md`
- `Pyrallel-Consumer/prd.ko.md`
- `Pyrallel-Consumer/docs/operations/guide.ko.md`
- `Pyrallel-Consumer/docs/operations/playbooks.md`
- `Pyrallel-Consumer/benchmarks/README.md`
- `Pyrallel-Consumer/examples/README.md`
- `Pyrallel-Consumer/pyrallel_consumer/config.py`
- `Pyrallel-Consumer/pyrallel_consumer/control_plane/*`
- `Pyrallel-Consumer/pyrallel_consumer/execution_plane/*`

즉 현재 문서군은 원문을 그대로 보존한 아카이브가 아니라, root summary + feature detail 구조로 다시 조직한 target-state 문서군이다.

## 3. 이 서비스에서 특히 중요한 읽기 전제

- 이 프로젝트는 실행 모델 공존을 목표로 하지만, 핵심 정체성은 `Control Plane invariant`에 있다.
- README는 사용자 관점 요약이고, `prd_dev.ko.md`와 `prd.ko.md`는 설계 의도와 이유를 담은 보존용 한국어 내부 source에 가깝다.
- 현재 코드에는 이미 대부분의 core runtime이 존재하고, metrics wiring도 facade 자동 경로까지 닫혔다. 다만 release gate나 dashboard 운영 packaging 같은 상위 운영 surface는 여전히 완전히 닫힌 상태가 아니다.
- benchmark tooling은 라이브러리 value proposition을 설명하는 중요한 증거이지만, core runtime contract 자체는 아니다.

## 4. target-state vs current-state 혼동 지점

- `KafkaConfig.metrics`는 현재 `PyrallelConsumer`가 exporter를 자동 생성하는 canonical runtime surface다.
- `metadata_snapshot`은 옵션 전략으로 문서에 등장하지만, 기본 운영 경로는 여전히 `contiguous_only`다.
- process profiling은 README/benchmark 문서에 존재하지만 플랫폼별 안정성 제약이 남아 있다.
- `worker_pool_size` 같은 과거 호환 surface와 `process_config.process_count` 같은 현재 canonical surface를 혼동하지 말아야 한다.

## 5. naming history

- 코드 저장소 이름은 `Pyrallel-Consumer`지만, blueprint 서비스 루트는 다른 서비스들과 맞추기 위해 `pyrallel-consumer`를 canonical 이름으로 쓴다.
- 구현 코드에서는 `BrokerPoller`, `WorkManager`, `OffsetTracker`, `MetadataEncoder`, `AsyncExecutionEngine`, `ProcessExecutionEngine`가 핵심 명칭이다.
- 운영 문서에서는 `true lag`, `gap`, `blocking offset`, `backpressure`를 canonical 용어로 유지해야 한다.

## 6. 서비스 전용 drift risk

- README의 marketing-level 설명과 PRD의 strict contract를 같은 밀도로 읽으면 drift가 생기기 쉽다.
- `offset commit`, `metadata snapshot`, `DLQ publish`, `retry exhaustion`은 서로 분리된 기능이 아니라 하나의 reliability 체인으로 함께 움직인다.
- async/process 엔진 제약이 문서마다 다르게 표현되면 facade contract가 흔들린다.
- benchmark sample 수치는 시간이 지나며 바뀔 수 있으므로, blueprint에서는 구조와 해석 규칙을 우선하고 고정 수치 인용은 최소화해야 한다.

## 7. 언제 이 문서를 읽는가

- README와 PRD 사이의 역할 차이를 빠르게 알고 싶을 때
- 현재 코드 구조와 blueprint feature 구조의 대응 관계를 복원하고 싶을 때
- target-state와 current implementation을 혼동하기 쉬운 지점을 확인하고 싶을 때

이 문서는 구현 시작의 첫 문서가 아니다. 일반적인 읽기 순서는 `00-index -> 01-overview -> 02/03/04 -> feature`이고, 이 문서는 그 이후에 메타 컨텍스트가 필요할 때만 읽는다.
