# Rebalance Retry DLQ Index

이 문서는 `rebalance-retry-dlq` subfeature의 목차다.
이 subfeature는 partition 소유권 변화, worker failure recovery, 최종 DLQ publish까지 이어지는 reliability edge를 다룬다.

## 이 subfeature가 답하는 질문

- 리밸런스 이후 stale completion은 어떻게 버려지는가
- revoke 시 어디까지 기다리고 무엇을 포기할 수 있는가
- worker retry/backoff는 어떤 정책으로 동작하는가
- DLQ publish와 commit의 순서는 어떻게 연결되는가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](01-requirements.ko.md) | epoch fencing, retry, DLQ의 책임과 acceptance 기준 |
| [02-architecture.md](02-architecture.ko.md) | assign/revoke, completion validation, retry-to-DLQ 흐름 |
| [03-design.md](03-design.ko.md) | config key, DLQ header, failure state 계약 |

## 빠른 읽기 분기

- 리밸런스 안전성만 알고 싶으면 `02-architecture.md`
- retry/backoff와 DLQ 세부 계약이 궁금하면 `03-design.md`
- 이 subfeature의 범위를 먼저 알고 싶으면 `01-requirements.md`
