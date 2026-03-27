# Ordered Work Scheduling Requirements

## 1. 문서 목적

이 문서는 `ordered-work-scheduling`의 책임과 요구사항을 정의한다.
이 subfeature는 ingest된 메시지를 바로 worker에 보내지 않고, ordering 보장과 commit progress를 동시에 만족하도록 스케줄링하는 계층이다.

## 2. 책임

- ordering mode별로 독립된 실행 가능 단위를 계산해야 한다.
- `blocking offset`을 막고 있는 작업이 있으면 해당 queue를 우선 제출해야 한다.
- 일반 workload도 starvation 되지 않도록 runnable queue를 순환시켜야 한다.
- in-flight, queued, runnable state를 Control Plane 내부에서 독립적으로 유지해야 한다.

## 3. 기능 요구사항

- `key_hash` 모드에서는 같은 `(topic-partition, key)`에 대한 동시 실행을 막아야 한다.
- `partition` 모드에서는 같은 partition의 동시 실행을 막아야 한다.
- `unordered` 모드에서는 ordering 제약 없이 최대 병렬성을 허용해야 한다.
- execution engine submit 전에 capacity check를 수행해야 한다.
- completion이 도착하면 in-flight state를 정리하고 다음 runnable queue를 활성화해야 한다.

## 4. 비기능 요구사항

- scheduling 판단은 engine internal counter에 의존하지 않아야 한다.
- queue topology는 backpressure와 metrics 계산에 필요한 최소 상태를 제공해야 한다.
- scheduling은 특정 partition이나 key에 영구적으로 편향되지 않아야 한다.

## 5. 입력/출력 경계

입력:

- `WorkItem`
- ordering mode 설정
- `OffsetTracker`가 제공하는 blocking gap 정보
- completion event

출력:

- execution engine에 제출될 다음 `WorkItem`
- queued/in-flight/runnable state 업데이트
- total queued count와 total in-flight count

## 6. MVP 경계

포함:

- virtual queue
- blocking-first selection
- round-robin runnable selection
- completion 후 queue 재활성화

제외:

- priority queue based weighted scheduling
- multi-tenant fairness policy
- persisted queue recovery

## 7. acceptance 기준

- ordering mode 세 가지의 실행 제약 차이가 문서상 분명해야 한다.
- `WorkManager`가 단순 dispatcher가 아니라 commit progress를 최대화하는 scheduler라는 점이 드러나야 한다.
- engine internal state와 Control Plane scheduling state의 분리가 문서에 명시돼야 한다.
