# Process Execution Engine Design

## 1. 문서 역할

이 문서는 process engine의 구현 직전 계약과 제약을 고정한다.
IPC 포맷, worker validation, recycle policy를 조정할 때 먼저 읽는 문서다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `EXECUTION_MODE` | 실행 모드 | `process` |
| `PROCESS_PROCESS_COUNT` | worker process 수 | `8` |
| `PROCESS_QUEUE_SIZE` | task queue 크기 | `2048` |
| `PROCESS_REQUIRE_PICKLABLE_WORKER` | picklable worker 강제 여부 | `true` |
| `PROCESS_BATCH_SIZE` | micro-batch item 수 | `64` |
| `PROCESS_BATCH_BYTES` | batch byte 예산 | `256KB` |
| `PROCESS_MAX_BATCH_WAIT_MS` | batch flush 최대 대기 | `5` |
| `PROCESS_TASK_TIMEOUT_MS` | worker timeout | `30000` |
| `PROCESS_MSGPACK_MAX_BYTES` | decode safety limit | `1000000` |
| `PROCESS_MAX_TASKS_PER_CHILD` | worker recycle threshold | `0` |
| `PROCESS_RECYCLE_JITTER_MS` | recycle jitter | `0` |

## 3. worker 계약

| 항목 | 계약 |
| --- | --- |
| worker type | sync callable |
| pickling | 설정상 요구될 수 있음 |
| Kafka 의존성 | worker 내부에서 Kafka client 직접 의존 금지 |
| 입력 | `WorkItem` |
| 실패 | 예외 또는 timeout |

## 4. IPC payload 계약

| payload | 의미 |
| --- | --- |
| task payload | msgpack으로 직렬화된 `WorkItem` list |
| completion payload | `CompletionEvent` dict 직렬화 결과 |
| registry event | `start`, `timeout`, 기타 worker 상태 신호 |

## 5. shutdown / recycle 규칙

- 정상 종료는 sentinel 기반이다.
- graceful join timeout 이후 남은 worker는 terminate 할 수 있다.
- recycle threshold가 0이면 worker recycle은 꺼진다.
- recycle jitter는 모든 worker가 동시에 재시작되지 않게 하는 보조 수단이다.
