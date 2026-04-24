# Process Execution Engine Design

## 1. 문서 역할

이 문서는 process execution engine의 구현 계약을 고정한다. 특히
`shared_queue` compatibility path와 `worker_pipes` target direction을 함께
다루면서도, `WorkManager` / `BrokerPoller`가 transport 세부사항을 몰라야 한다는
원칙을 지킨다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `EXECUTION_MODE` | 실행 모드 | `process` |
| `PROCESS_PROCESS_COUNT` | worker process 수 | `8` |
| `PROCESS_QUEUE_SIZE` | shared queue 또는 transport capacity 예산 | `2048` |
| `PROCESS_REQUIRE_PICKLABLE_WORKER` | picklable worker 강제 여부 | `true` |
| `PROCESS_BATCH_SIZE` | micro-batch item 수 | `64` |
| `PROCESS_BATCH_BYTES` | batch byte 예산 | `256KB` |
| `PROCESS_TRANSPORT_MODE` | process input transport (`shared_queue`/`worker_pipes`) | `shared_queue` |
| `PROCESS_MAX_BATCH_WAIT_MS` | batch flush 최대 대기 | `5` |
| `PROCESS_FLUSH_POLICY` | batch flush policy | `size_or_timer` |
| `PROCESS_DEMAND_FLUSH_MIN_RESIDENCE_MS` | demand flush 최소 residence | `0` |
| `PROCESS_SHUTDOWN_DRAIN_TIMEOUT_MS` | shutdown drain timeout | `5000` |
| `PROCESS_WORKER_JOIN_TIMEOUT_MS` | worker join timeout | `30000` |
| `PROCESS_TASK_TIMEOUT_MS` | worker timeout | `30000` |
| `PROCESS_MSGPACK_MAX_BYTES` | decode safety limit | `1000000` |
| `PROCESS_MAX_TASKS_PER_CHILD` | worker recycle threshold | `0` |
| `PROCESS_RECYCLE_JITTER_MS` | recycle jitter | `0` |

설계 원칙:

- `transport_mode=shared_queue`는 기본값이다.
- `transport_mode=worker_pipes`는 ordered affinity-preserving path다.
- invalid transport 값은 config validation에서 즉시 실패해야 한다.

## 3. worker 계약

| 항목 | 계약 |
| --- | --- |
| worker type | sync callable |
| pickling | 설정상 요구될 수 있음 |
| Kafka 의존성 | worker 내부에서 Kafka client 직접 의존 금지 |
| 입력 | `WorkItem` |
| 실패 | 예외 또는 timeout |
| transport awareness | worker function은 transport mode를 몰라야 함 |

## 4. route identity 계약

process engine 내부는 ordered mode에서 route identity를 사용한다.

```text
route_identity = (work_item.tp.topic, work_item.tp.partition, work_item.key)
```

규칙:

- 같은 identity는 같은 worker execution slot/channel로 간다.
- `process_count`가 바뀌면 mapping은 달라질 수 있다.
- stable hash를 사용해야 한다.
- ordered mode의 기본 원칙은 sticky routing이다.
- unordered balancing / stealing은 후속 연구로 분리한다.

## 5. input dispatch / completion aggregation

### 5.1 shared_queue

- msgpack batch payload를 shared `multiprocessing.Queue`에 넣는다.
- 모든 worker가 같은 queue에서 `get()` 경쟁한다.
- 기본값과 compatibility path 역할을 한다.

### 5.2 worker_pipes

- submit 순간 worker channel을 선택한다.
- parent는 worker별 input channel에 payload를 보낸다.
- completion은 기존 single completion queue를 유지한다.
- registry event queue도 parent-side 단일 drain 경로를 유지한다.

즉 design 레벨에서 바꾸는 것은 input dispatch다. completion aggregation은 1차
slice에서 바꾸지 않는다.

## 6. batching 계약

batching은 correctness layer가 아니라 IPC 비용 절감 수단이다.

### shared_queue

- 기존 `batch_size`, `batch_bytes`, `max_batch_wait_ms`, `flush_policy`,
  `demand_flush_min_residence_ms` 의미를 유지한다.

### worker_pipes

- 1차 slice에서는 `batch_size=1`, `max_batch_wait_ms=0` 중심으로 bounded support를
  두는 것이 기본이다.
- richer batching을 지원하지 못하면 silent fallback 대신 startup reject가
  우선이다.
- 아래 의미를 암묵적으로 바꾸면 안 된다.
  - `size_or_timer`
  - `demand`
  - `demand_min_residence`

## 7. `wait_for_completion()` 계약

transport가 달라도 다음 의미를 유지해야 한다.

- prefetched completion이 있으면 즉시 true를 반환할 수 있어야 한다.
- completion queue에 이미 값이 있으면 즉시 반응해야 한다.
- timeout이 0 이하이면 blocking wait를 하지 않아야 한다.
- completion drain 후 in-flight count가 일관되게 줄어야 한다.

## 8. shutdown / recycle / restart 계약

### shutdown

- batch accumulator 또는 buffered submission을 먼저 flush한다.
- transport별 input channel에 sentinel을 보낸다.
- join -> terminate -> kill escalation을 유지한다.
- shutdown 전에 completion / registry event drain을 시도한다.

### recycle

- `max_tasks_per_child` / `recycle_jitter_ms` 의미를 transport별로 유지하거나
  unsupported matrix에서 명시적으로 reject해야 한다.
- silent disable은 금지한다.

### restart / dead worker recovery

- parent는 dead worker를 감지하고 문서화된 transport 정책에 따라 recovery를
  수행한다.
- ordered mode에서 ownership migration까지 자동으로 해결하는 것은 1차 요구가
  아니다.

## 9. metrics / runtime surface

문서가 구현보다 얕아지지 않으려면 metrics surface를 명시해야 한다.

- `get_runtime_metrics()`는 transport별 runtime 해석이 가능해야 한다.
- benchmark metadata는 transport mode를 포함해야 한다.
- release-gate는 final lag / final gap / ordering evidence를 같은 기준으로
  평가해야 한다.
- transport별 unsupported/rejected 조합도 로그 또는 runtime metadata로
  설명 가능해야 한다.

## 10. unsupported matrix 원칙

다음은 design 원칙이다.

- unsupported 조합은 startup reject가 우선이다.
- `shared_queue`는 compatibility path로 유지한다.
- `worker_pipes`는 ordered throughput 개선을 위한 direction path로 다룬다.
- completion queue 분리, broker I/O bridge, work stealing, shared memory transport는
  이 design 범위에 포함하지 않는다.
