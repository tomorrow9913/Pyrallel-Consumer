# Offset Commit State Index

이 문서는 `offset-commit-state` subfeature의 목차다.
이 subfeature는 `OffsetTracker`와 `MetadataEncoder`가 contiguous-safe commit state를 어떻게 유지하는지 다룬다.

## 이 subfeature가 답하는 질문

- 병렬 처리 환경에서 안전한 commit progress는 어떻게 계산되는가
- `HWM`, `gap`, `blocking offset`, `true lag`는 어떤 관계인가
- sparse completed offset은 어디에 어떻게 저장되는가
- metadata snapshot은 왜 optional 전략인가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.md](01-requirements.ko.md) | commit-safe state machine의 책임과 acceptance 기준 |
| [02-architecture.md](02-architecture.ko.md) | `OffsetTracker`와 `MetadataEncoder`의 관계와 흐름 |
| [03-design.md](03-design.ko.md) | canonical 용어, metadata encoding 규칙, 핵심 field 계약 |

## 빠른 읽기 분기

- `HWM`/`gap` 정의를 먼저 잡고 싶으면 `03-design.md`
- state machine 흐름을 알고 싶으면 `02-architecture.md`
- 이 subfeature의 범위를 알고 싶으면 `01-requirements.md`
