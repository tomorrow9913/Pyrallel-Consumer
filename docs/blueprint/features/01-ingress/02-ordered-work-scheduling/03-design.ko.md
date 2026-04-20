# Ordered Work Scheduling Design

## 1. 문서 역할

이 문서는 ordering mode와 queue topology에 대한 구현 직전 계약을 고정한다.
ordering semantics를 바꾸거나 queue state를 리팩터링할 때 먼저 읽는 문서다.

## 2. ordering mode 계약

| 모드 | 동시 실행 제약 | 대표 사용처 |
| --- | --- | --- |
| `key_hash` | 같은 key는 동시 실행 금지 | key별 side effect가 있는 일반 Kafka workload |
| `partition` | 같은 partition은 동시 실행 금지 | Kafka partition ordering 자체를 그대로 유지해야 할 때 |
| `unordered` | ordering fence 없음 | 순서보다 처리량이 더 중요한 순수 병렬 처리 |

## 3. `WorkItem`에서 scheduling에 중요한 필드

| 필드 | 용도 |
| --- | --- |
| `id` | in-flight 추적과 completion correlation |
| `tp` | partition 단위 queue / metrics / rebalance 경계 |
| `offset` | queue head, blocking 판단, commit progress |
| `epoch` | stale completion 방지 |
| `key` | `key_hash` mode의 concurrency fence |

## 4. 내부 상태 shape

| 상태 | 설명 |
| --- | --- |
| `virtual_partition_queues[tp][key]` | queue head가 runnable candidate가 되는 backlog |
| `runnable_queue_keys` | 스케줄 가능한 queue key의 순환 목록 |
| `head_offsets[(tp,key)]` | 각 queue의 현재 head offset |
| `in_flight_work_items[id]` | 실행 중 work item canonical registry |
| `work_item_ids_by_tp_offset[(tp,offset)]` | force-fail/recovery용 역인덱스 |

## 5. scheduling 규칙

- `blocking offset`이 queue head와 일치하면 그 queue가 가장 우선이다.
- 같은 key 또는 같은 partition이 이미 in-flight라면 해당 queue는 runnable이어도 즉시 제출하지 않는다.
- completion 이후 queue가 비면 queue key를 비활성화한다.
- completion 이후 queue에 backlog가 남아 있으면 새 head offset으로 다시 활성화한다.

## 6. metrics와의 연결

- `total_queued_messages`는 backpressure 판단과 운영 지표에 사용된다.
- `current_in_flight_count`는 scheduler가 소유하는 canonical count다.
- engine의 `get_in_flight_count()`는 debug/metrics 참고용이며 scheduling source of truth가 아니다.
