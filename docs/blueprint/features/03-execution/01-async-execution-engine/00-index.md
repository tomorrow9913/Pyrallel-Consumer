# Async Execution Engine Index

이 문서는 `async-execution-engine` subfeature의 목차다.
이 subfeature는 `AsyncExecutionEngine`이 coroutine worker를 실행하고 completion을 되돌리는 경로를 다룬다.

## 이 subfeature가 답하는 질문

- async mode에서 worker는 어떤 형태여야 하는가
- concurrency 제한은 어디서 걸리는가
- timeout/retry/shutdown은 어떻게 동작하는가
- completion queue는 어떤 의미를 가지는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | async engine의 책임과 acceptance 기준 |
| [02-architecture.md](./02-architecture.md) | semaphore, task lifecycle, completion flow |
| [03-design.md](./03-design.md) | config key, worker contract, completion payload 규칙 |

## 빠른 읽기 분기

- async worker 제약이 궁금하면 `03-design.md`
- task lifecycle과 shutdown 흐름이 궁금하면 `02-architecture.md`
- 이 엔진의 책임 범위를 알고 싶으면 `01-requirements.md`
