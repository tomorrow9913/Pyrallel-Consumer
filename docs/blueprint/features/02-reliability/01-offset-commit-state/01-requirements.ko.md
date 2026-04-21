# Offset Commit State Requirements

## 1. 문서 목적

이 문서는 `offset-commit-state`의 책임과 요구사항을 정의한다.
이 subfeature는 병렬 completion을 contiguous-safe commit progress로 바꾸는 핵심 reliability layer다.

## 2. 책임

- partition별로 마지막 안전 커밋 지점(HWM)을 계산해야 한다.
- out-of-order completion에서도 gap 구간을 정확하게 유지해야 한다.
- true lag, gap count, blocking duration 계산에 필요한 상태를 제공해야 한다.
- 필요 시 sparse completed offset을 Kafka commit metadata에 encode/decode 할 수 있어야 한다.

## 3. 기능 요구사항

- completion은 offset 순서와 무관하게 들어와도 안전하게 누적돼야 한다.
- contiguous한 completion이 생기면 HWM이 전진해야 한다.
- HWM 이전 offset은 다시 추적하지 않아야 한다.
- gap head는 blocking offset으로 해석될 수 있어야 한다.
- metadata snapshot을 사용할 때는 size limit 초과 시 fail-closed로 축소할 수 있어야 한다.

## 4. 비기능 요구사항

- state calculation은 deterministic 해야 한다.
- gap 계산은 반복 호출에 대해 캐시 가능한 구조여야 한다.
- metadata encoding은 Kafka metadata budget 안에 들어가야 한다.

## 5. 입력/출력 경계

입력:

- fetched offset
- completed offset
- partition epoch
- optional committed metadata snapshot

출력:

- `last_committed_offset` / HWM
- `OffsetRange` 기반 gap 목록
- blocking duration lookup
- encoded commit metadata

## 6. MVP 경계

포함:

- `OffsetTracker` 기반 HWM/gap 관리
- RLE/bitset 동시 인코딩 후 짧은 결과 선택
- true lag / gap count 계산

제외:

- external durable state store
- infinite sparse completion retention
- exactly-once transaction log

## 7. acceptance 기준

- HWM이 단조 증가한다는 불변 조건이 문서에 명시돼야 한다.
- metadata snapshot이 committed offset 자체를 바꾸는 기능이 아니라 보조 복원 수단이라는 점이 드러나야 한다.
- `true lag`와 Kafka 기본 lag의 차이가 문서상 분명해야 한다.
