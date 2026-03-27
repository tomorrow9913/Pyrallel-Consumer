# Ordered Work Scheduling Index

이 문서는 `ordered-work-scheduling` subfeature의 목차다.
이 subfeature는 `WorkManager`가 ordering mode를 해석하고 virtual queue를 운영하며, blocking offset 우선 스케줄링을 수행하는 구간을 다룬다.

## 이 subfeature가 답하는 질문

- key/partition/unordered 세 모드는 정확히 어떻게 다른가
- 왜 virtual partition queue가 필요한가
- 어떤 기준으로 runnable queue를 고르고 starvation을 막는가
- WorkManager는 어디까지 알고, execution engine 내부는 어디서부터 모르는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](./01-requirements.md) | ordering-aware scheduling의 책임과 acceptance 기준 |
| [02-architecture.md](./02-architecture.md) | queue topology, runnable selection, blocking-first policy |
| [03-design.md](./03-design.md) | ordering mode별 계약, `WorkItem` 사용 규칙, queue state shape |

## 빠른 읽기 분기

- ordering mode의 사용자-facing 의미가 궁금하면 `03-design.md`
- `WorkManager` 내부 queue topology가 궁금하면 `02-architecture.md`
- scheduling 책임 범위가 궁금하면 `01-requirements.md`
