# Process Execution Engine Design

## 1. 문서 역할

이 문서는 process execution engine의 구현 계약을 고정한다. 특히 #129 이후
`shared_queue`를 live runtime path에서 제거하고 `worker_pipes`를 process execution
단일 topology로 두면서도, `WorkManager` / `BrokerPoller`가 transport 세부사항을
몰라야 한다는 원칙을 지킨다.

## 2. 핵심 설정 키

| 키 | 의미 | 기본값 |
| --- | --- | --- |
| `EXECUTION_MODE` | 실행 모드 | `process` |
| `PROCESS_PROCESS_COUNT` | worker process 수 | `8` |
| `PROCESS_QUEUE_SIZE` | shared queue 또는 transport capacity 예산 | `2048` |
| `PROCESS_REQUIRE_PICKLABLE_WORKER` | picklable worker 강제 여부 | `true` |
| `PROCESS_BATCH_SIZE` | worker-pipes 호환 item batch size | `1` |
| `PROCESS_BATCH_BYTES` | batch byte 예산 | `256KB` |
| `PROCESS_ROUTE_BATCH_SIZE` | process worker-pipe route-batch 전송/admission 크기 | `64` |
| `PROCESS_MAX_BATCH_WAIT_MS` | batch flush 최대 대기 | `0` |
| `PROCESS_FLUSH_POLICY` | batch flush policy | `size_or_timer` |
| `PROCESS_DEMAND_FLUSH_MIN_RESIDENCE_MS` | demand flush 최소 residence | `0` |
| `PROCESS_SHUTDOWN_DRAIN_TIMEOUT_MS` | shutdown drain timeout | `5000` |
| `PROCESS_WORKER_JOIN_TIMEOUT_MS` | worker join timeout | `30000` |
| `PROCESS_TASK_TIMEOUT_MS` | worker timeout | `30000` |
| `PROCESS_MSGPACK_MAX_BYTES` | decode safety limit | `1000000` |
| `PROCESS_MAX_TASKS_PER_CHILD` | worker recycle threshold | `0` |
| `PROCESS_RECYCLE_JITTER_MS` | recycle jitter | `0` |

설계 원칙:

- process execution은 selectable transport mode를 갖지 않는다.
- `worker_pipes`는 ordered affinity-preserving path이자 단일 live topology다.
- `ProcessConfig.route_batch_size=64`는 process worker-pipe route-batch
  전송/admission profile이다.
- async/common execution은 item-level WorkManager lease를 유지하며 별도의
  execution-level route-batch config surface를 갖지 않는다.

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

이 identity는 process engine만을 위한 별도 hint가 아니다. `WorkManager`가
partition/key virtual queue를 만들고 async engine에 safe-to-run item을 넘길 때
이미 사용한 logical queue identity를, process transport가 worker channel 선택에
재사용하는 것이다. async engine은 IPC channel이 없으므로 이 identity를 별도
routing에 쓰지 않고 `create_task()`로 바로 실행한다.

규칙:

- 같은 identity는 같은 worker execution slot/channel로 간다.
- `process_count`가 바뀌면 mapping은 달라질 수 있다.
- stable hash를 사용해야 한다.
- ordered mode의 기본 원칙은 sticky routing이다.
- unordered balancing / stealing은 후속 연구로 분리한다.

## 5. input dispatch / completion aggregation

### 5.1 historical shared_queue

- `shared_queue`는 #129 이후 live runtime path가 아니다.
- 문서에서는 과거 병목과 migration/release-note 맥락으로만 언급한다.

### 5.2 worker_pipes

- submit 순간 worker channel을 선택한다.
- parent는 worker별 input channel에 payload를 보낸다.
- parent-facing completion surface는 기존 item-level completion queue 의미를
  유지한다.
- route-batch 정상 경로는 internal `BatchCompletion` envelope를 쓸 수 있다.
- registry event queue도 parent-side 단일 drain 경로를 유지한다.

즉 design 레벨에서 바꾸는 것은 input dispatch와 route-batch IPC envelope다.
completion/retry/commit correctness 단위는 item으로 유지한다.

## 6. batching 계약

batching은 correctness layer가 아니라 IPC 비용 절감 수단이다.

### worker_pipes

- `ProcessConfig.batch_size`와 `ProcessConfig.route_batch_size`는 별도 축이다.
- `worker_pipes`의 ordered route batching은 `ProcessConfig.route_batch_size`로
  제어한다.
- `KEY_HASH`/`PARTITION` ordered route batch는 engine이
  `supports_ordered_route_batch=True`를 명시할 때만 활성화한다.
- batch payload는 같은 route의 item만 포함해야 하며, worker는 batch 내부 item을
  순서대로 실행한다.
- 정상 completion path는 executed prefix를 하나의 internal `BatchCompletion`
  envelope로 보내고, parent가 기존 `CompletionEvent` 단위로 펼친다.
- registry, retry, DLQ, commit accounting은 item 단위를 유지한다.
- 기존 process micro-batch flush policy의 의미(`size_or_timer`, `demand`,
  `demand_min_residence`)를 route batch 의미로 암묵적으로 재해석하면 안 된다.

### `submit_batch()` contract

- 기본 fallback은 `submit()`을 item 순서대로 호출한다.
- 일부 prefix만 accept한 뒤 실패하면 `BatchSubmitError(accepted_count=...)`로
  알려야 한다.
- 일반 예외는 accepted count `0`으로 취급한다.
- fallback 자체는 ordered same-route 실행을 보장하는 sequential executor가 아니다.

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
- benchmark metadata는 `process_transport_mode`를 생략하거나 deprecated
  compatibility field로 `"worker_pipes"`만 포함할 수 있다.
- route-batch path는 `items_per_input_ipc`, `items_per_completion_ipc`,
  `route_batch_count`, `route_batch_item_count`, `route_batch_size_avg`,
  `route_batch_size_max`, `completion_item_payload_count`,
  `completion_batch_payload_count`를 통해 IPC amortization을 해석할 수 있어야 한다.
- release-gate는 final lag / final gap / ordering evidence를 같은 기준으로
  평가해야 한다.
- transport별 unsupported/rejected 조합도 로그 또는 runtime metadata로
  설명 가능해야 한다.

## 10. unsupported matrix 원칙

다음은 design 원칙이다.

- unsupported 조합은 startup reject가 우선이다.
- `shared_queue`는 historical context로만 유지한다.
- `worker_pipes`는 process execution의 live path로 다룬다.
- completion queue 분리, broker I/O bridge, work stealing, shared memory transport는
  이 design 범위에 포함하지 않는다.

## 11. route-batch safety guard

- poison/force-fail 대상이 batch 뒤쪽에 있으면 그 item 앞에서 batch를 끊는다.
- worker death 전/후 아직 start되지 않은 batch tail은 recoverable해야 한다.
- live-worker `not_started` tail은 diagnostic-only가 아니라 requeue/recovery 신호다.
- fatal timeout/exit path는 worker가 죽기 전에 completed prefix
  `BatchCompletion`을 flush해야 한다.
- duplicate completion suppression은 장기 실행에서 메모리가 무한히 늘지 않도록
  bounded cache여야 한다.
- msgpack completion bytes는 unpack 전에 size guard를 거치고, malformed batch
  payload는 빈 work/completion으로 해석하지 않는다.
