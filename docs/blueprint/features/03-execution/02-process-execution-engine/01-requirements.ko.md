# Process Execution Engine Requirements

## 1. 문서 목적

이 문서는 `process-execution-engine`의 책임과 요구사항을 정의한다.
이 subfeature는 CPU-bound workload를 위해 worker 실행을 별도 프로세스로 격리하는 엔진이다.

## 2. 책임

- sync worker를 별도 프로세스에서 실행해야 한다.
- IPC 오버헤드를 줄이기 위해 micro-batching을 지원해야 한다.
- worker crash/timeout을 `CompletionEvent.FAILURE`로 surface 해야 한다.
- shutdown 시 sentinel, join, terminate fallback 순서를 가져야 한다.
- worker logging을 메인 프로세스로 집계할 수 있어야 한다.

## 3. 기능 요구사항

- process mode에서는 coroutine worker를 허용하지 않아야 한다.
- picklable worker 검증이 설정으로 강제될 수 있어야 한다.
- task queue와 completion queue는 batch payload를 전달할 수 있어야 한다.
- task timeout은 worker process 안에서 enforced 되어야 한다.
- optional worker recycle config를 지원해야 한다.

## 4. 비기능 요구사항

- msgpack payload는 size guard를 가져야 한다.
- 프로세스 누수가 없어야 한다.
- crash/timeout이 consumer 전체 crash로 번지지 않아야 한다.

## 5. 입력/출력 경계

입력:

- `WorkItem`
- sync picklable worker
- process nested config

출력:

- batch IPC payload
- `CompletionEvent`
- registry event(start/timeout/finish)
- worker process lifecycle state

## 6. MVP 경계

포함:

- multiprocessing queue 기반 worker pool
- batch accumulator
- timeout + retry/backoff
- sentinel shutdown

제외:

- remote worker farm
- shared-memory zero-copy transport
- autoscaling process manager

## 7. acceptance 기준

- process mode가 picklable sync worker를 요구한다는 점이 문서에 명확해야 한다.
- micro-batching이 correctness가 아니라 IPC 비용 절감을 위한 것이라는 점이 드러나야 한다.
- crash/timeout 격리가 consumer 전체 crash로 이어지지 않는다는 점이 설명돼야 한다.
