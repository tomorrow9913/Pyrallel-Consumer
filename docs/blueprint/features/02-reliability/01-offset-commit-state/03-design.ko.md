# Offset Commit State Design

## 1. 문서 역할

이 문서는 commit-safe state에 필요한 핵심 용어와 encoding 계약을 고정한다.
offset correctness 관련 리팩터링이나 운영 설명을 할 때 먼저 참조하는 문서다.

## 2. canonical 상태 용어

| 용어 | 정의 |
| --- | --- |
| `last_committed_offset` | 현재까지 안전하게 전진한 contiguous HWM |
| `last_fetched_offset` | Kafka에서 마지막으로 가져온 offset |
| `completed_offsets` | HWM 뒤에서 완료됐지만 아직 contiguous하지 않은 offset 집합 |
| `gap` | `last_committed_offset + 1`부터 `last_fetched_offset` 사이의 미완료 구간 |
| `blocking offset` | 각 gap의 시작 offset |

## 3. 핵심 DTO / state shape

| 이름 | 역할 |
| --- | --- |
| `OffsetRange(start, end)` | gap range 표현 |
| `PartitionMetrics.true_lag` | `last_fetched_offset - last_committed_offset` 기반 lag |
| `PartitionMetrics.gap_count` | outstanding gap 개수 |
| `PartitionMetrics.blocking_duration_sec` | gap head가 막고 있는 시간 |

## 4. metadata encoding 규칙

| 인코딩 | 설명 |
| --- | --- |
| `R...` | RLE payload |
| `B...` | bitset payload |
| `O` | overflow 또는 snapshot 포기 |

규칙:

- 같은 offset 집합에 대해 RLE와 bitset을 동시에 계산한다.
- 더 짧고 metadata budget 안에 드는 결과를 선택한다.
- 둘 다 budget을 넘기면 `O`를 사용해 snapshot 포기를 명시한다.

## 5. state 불변 조건

- HWM은 절대 감소하지 않는다.
- HWM 이전 offset은 `completed_offsets`에 남지 않는다.
- gap이 없으면 blocking timestamp도 남지 않는다.
- snapshot은 contiguous commit을 대체하지 못한다.

## 6. 운영상 해석 규칙

- `true lag`가 커지면 실제 backlog가 증가하는 것이다.
- `gap_count`가 커지면 out-of-order completion 비용이 커진 것이다.
- `blocking_duration_sec`가 길어지면 특정 offset 또는 key가 병목이다.
