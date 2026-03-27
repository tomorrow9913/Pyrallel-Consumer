# Process Execution Engine Index

이 문서는 `process-execution-engine` subfeature의 목차다.
이 subfeature는 `ProcessExecutionEngine`이 sync worker를 별도 프로세스에서 실행하고 completion을 되돌리는 경로를 다룬다.

## 이 subfeature가 답하는 질문

- process mode에서 worker는 어떤 제약을 가져야 하는가
- micro-batching과 msgpack은 왜 필요한가
- timeout/crash/recycle은 어떻게 처리되는가
- logging과 completion은 어떤 IPC로 전달되는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | process engine의 책임과 acceptance 기준 |
| [02-architecture.md](./02-architecture.md) | worker process, IPC queue, batch accumulator, registry flow |
| [03-design.md](./03-design.md) | config key, worker 제약, IPC payload 계약 |

## 빠른 읽기 분기

- picklable worker와 CPU-bound 사용 조건이 궁금하면 `03-design.md`
- queue와 worker loop 구조가 궁금하면 `02-architecture.md`
- 이 엔진의 책임 범위를 먼저 알고 싶으면 `01-requirements.md`
