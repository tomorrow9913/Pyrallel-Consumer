# Worker Pipe Transport Experiment

이 문서는 worker-pipe transport 실험의 한국어 원본이자 구현 전 청사진 문서다.
목표는 “실험 메모”를 남기는 것이 아니라, `ProcessExecutionEngine`의 장기 방향을
검증 가능한 bounded slice로 잘라 적어 두는 것이다. 즉, 구현 전에는 청사진처럼
읽히고, 구현 후에는 측정값과 실제 제약을 반영해 장기 방향 문서로 흡수되도록
작성한다.

영문 canonical 문서는
[04-worker-pipe-transport-experiment.md](./04-worker-pipe-transport-experiment.md)를 따른다.

## 이 문서가 답하는 질문

이 문서는 하나의 질문만 다룬다.

> shared `multiprocessing.Queue` input topology가 ordered process-mode throughput의 고정비 병목 중 하나인가?

이 질문은 더 큰 장기 방향에 연결된다.

> process engine은 `WorkManager`가 이미 결정한 ordered virtual queue identity를
> process boundary 너머까지 보존하는 worker-affine topology로 진화해야 하는가?

따라서 이 문서는:

- transport 변경 범위를 어디까지 허용하는지,
- control plane 경계를 무엇으로 고정하는지,
- 어떤 config 조합을 1차 실험에서 명시적으로 막아야 하는지,
- benchmark와 release-gate에서 무엇을 증거로 봐야 하는지

를 먼저 적어두는 실험 지시서다.

## 장기 방향과의 연결

이 실험 문서는 단독 memo가 아니다. 다음 문서들과 같은 방향을 공유한다.

- `00-index`: process engine의 장기 목표는 ordered virtual queue identity를
  process 경계 너머에서도 보존하는 것
- `01-requirements`: `shared_queue`는 historical context, `worker_pipes`는
  process execution의 단일 live topology
- `02-architecture`: input dispatch topology가 completion aggregation보다 우선
  개선 대상
- `03-design`: transport mode, route identity, shutdown/recycle, metrics surface
  명시

따라서 이 문서는 “실험을 해볼 수 있다” 수준이 아니라, 장기 architecture direction을
가장 작고 검증 가능한 형태로 실험하는 설계 문서다.

## 현재 구조와 실험 구조

현재 control plane은 이미 ordering/eligibility를 먼저 정한다.

- `WorkManager`는 partition/key별 virtual queue를 가진다.
- 그 위에서 safe-to-run item만 골라 `submit(work_item)` 한다.
- async engine은 submit 순간 `create_task()`로 실행 계층에 work를 바로 보내므로
  input queue에서 다시 하나로 합치지 않는다.

과거 process engine은 submit된 item을 single queue로 다시 합쳤다.

과거 process runtime:

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit()
  -> shared multiprocessing.Queue
  -> N process workers competing on the same input
  -> single completion queue
```

실험 runtime:

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit() / submit_batch()
  -> transport router 또는 RouteBatch route lease
  -> worker-specific input Pipe
  -> owner worker process sequential loop
  -> item completion 또는 BatchCompletion envelope
  -> parent-side item completion surface
```

worker-pipe 1차 slice는 input transport를 바꿨다. route-batch slice는 route batch
정상 경로의 worker-to-parent IPC envelope도 바꾼다. 다만 parent-facing completion
surface, control-plane commit 판단, worker function 실행 의미는 기존 위치를 유지한다.

## py-spy / benchmark evidence

현재 py-spy와 benchmark 결과는 다음을 시사한다.

- ordered partition workload에서 worker side 대부분의 시간이 실제 worker
  function보다 `_receive_task_payload` / `multiprocessing.Queue.get` /
  `synchronize.__enter__` / `connection.recv_bytes`에 있었다.
- 실제 `_io_worker_process` 비중은 매우 작았다.
- 즉, 현재 병목 후보는 completion aggregation보다 input dispatch topology다.

이 문서는 이 evidence를 바탕으로 “input dispatch topology를 먼저 바꿔볼 가치가
있다”는 가설을 검증한다.

## 실험 가설

#129 이후 이 문서는 아래 가설을 historical evidence로 유지한다.

> ordered process-mode에서 shared input queue가 유효 병렬성을 깎는 경계라면, worker별 input channel과 stable routing은 partition/key 폭이 충분한 workload에서 `shared_queue` 대비 더 높은 throughput을 보여야 한다. 단, ordering, final lag, final gap, release-gate correctness는 깨지면 안 된다.

이 문서는 #129 이전에는 `worker_pipes`를 기본 후보로 평가하기 위한 실험 문서였다.
#129 이후에는 `worker_pipes`를 process execution의 단일 live topology로 채택한
근거와 남은 운영 계약을 설명하는 historical blueprint로 읽는다.

## 실험 범위

`shared_queue` 제거 전에는 명시적 transport option 뒤에 숨겼다. #129 이후에는
해당 selector가 제거된다.

```text
process_transport = worker_pipes
```

process mode는 더 이상 `shared_queue`를 선택할 수 없다.

### 구현된 worker-pipe 범위

- worker별 parent-to-worker 단방향 input pipe
- `WorkItem` identity 기반 stable routing
- 명시적으로 켜진 경우 같은 route `WorkItem` 묶음의 route-batch dispatch
- internal `RouteBatch` / `BatchCompletion` wire envelope
- parent-side item-level `CompletionEvent` expansion
- 기존 single completion queue 재사용
- parent-side registry / in-flight accounting 재사용
- benchmark에서 transport 선택 가능
- benchmark에서 `route_batch_size` 선택 가능
- shared queue와 worker pipes 비교 matrix
- 지원하지 않는 조합은 startup에서 명시적으로 reject

### 1차 실험에서 제외

- work stealing
- dynamic load balancing
- worker death 이후 ownership migration
- worker별 completion queue
- completion ingest thread
- shared-memory ring buffer
- broker I/O ownership 변경
- production 기본 transport 전환
- retry/commit/control-plane의 대규모 재설계

## Control-plane 불변 계약

transport 실험이어도 control plane은 transport를 몰라야 한다.

반드시 유지할 것:

- `BrokerPoller`와 `WorkManager`는 process engine의 pipe/lane 세부사항을 알지 못한다.
- `BaseExecutionEngine` public surface는 명시적이고 안정적으로 유지한다.
  - `submit(work_item)`
  - `submit_batch(work_items)`
  - `poll_completed_events(batch_limit=1000)`
  - `wait_for_completion(timeout_seconds=None)`
  - `get_in_flight_count()`
  - `get_runtime_metrics()`
  - `shutdown()`
- 실행 가능한 `WorkItem`을 고르는 책임은 계속 `WorkManager`에 있다.
- completion aggregation과 offset commit 판단은 parent/control plane에 남는다.
  `BatchCompletion`은 internal IPC envelope일 뿐 public commit 단위가 아니다.
- commit clamp용 최소 in-flight offset은 control-plane `WorkManager`
  dispatch ledger에서 계산한다. engine-level `get_min_inflight_offset()`는
  있더라도 compatibility/private recovery state일 뿐 canonical source가 아니다.
- transport는 ordering correctness를 새로 판단하지 않는다. 이미 safe-to-run으로
  내려온 item을 어느 worker channel로 보낼지만 결정한다.

## Routing 계약

transport는 기존 `WorkItem`이 이미 갖고 있는 logical identity를 재사용한다.

```text
route_identity = (work_item.tp.topic, work_item.tp.partition, work_item.key)
```

이것은 새 hint를 도입하는 것이 아니라, async path와 process path가 같은
`WorkManager` logical queue identity를 공유한다는 뜻이다. `WorkManager`는 이미
partition/key별 virtual queue에서 safe-to-run item을 고르고, async engine은 그
item을 `create_task()`로 바로 실행한다. process `worker_pipes` transport는 같은
identity를 stable hash하여 worker input channel을 선택한다.

Routing 규칙:

- Python built-in `hash()`가 아니라 stable hash를 사용한다.
- `process_count`가 같으면 같은 identity는 항상 같은 worker index로 가야 한다.
- `process_count`가 바뀌면 mapping이 바뀌어도 된다.
- `unordered` mode는 다른 policy를 써도 되지만 문서와 benchmark 해석에 명시해야 한다.
- crash 이후 ownership migration은 1차 범위가 아니다.
- ordered mode의 기본 원칙은 stealing이 아니라 affinity preservation이다.
- `PARTITION` route batch는 `(topic, partition)`을 route로 사용한다.
- `KEY_HASH` route batch는 `(topic, partition, key)`를 route로 사용한다.

## Config / CLI 계약

#129 이후 `ProcessConfig`는 transport selector가 아니라 process-owned route-batch
profile을 가진다.

```python
route_batch_size: int = 64
```

async/common execution은 item-level WorkManager lease를 유지하며 별도의
execution-level route-batch config surface를 갖지 않는다.

Config 요구사항:

- `ProcessConfig.transport_mode`와 `PROCESS_TRANSPORT_MODE`는 제거한다.
- env override는 `PROCESS_ROUTE_BATCH_SIZE`를 사용한다.
- `--process-route-batch-size`가 benchmark canonical flag다.
- 기존 `--route-batch-size`는 benchmark transition alias로만 남길 수 있다.
- 아래 key의 의미를 깨면 안 된다.
  - `process_count`
  - `queue_size`
  - `require_picklable_worker`
  - `batch_size`
  - `max_batch_wait_ms`
  - `flush_policy`
  - `demand_flush_min_residence_ms`
  - `msgpack_max_bytes`
  - `max_tasks_per_child`
  - `recycle_jitter_ms`

Benchmark CLI는 process transport selector를 노출하지 않는다.

```bash
--process-route-batch-size 1|8|32|64|128
```

전달 경로는 명시적으로 유지한다.

```text
benchmark CLI
  -> benchmark config builder
  -> KafkaConfig.parallel_consumer.execution.process_config.route_batch_size
  -> resolve_work_manager_route_batch_size()
  -> ProcessExecutionEngine
```

## 지원/비지원 매트릭스

이 실험은 silent fallback보다 explicit rejection을 선호한다.

| Surface | 규칙 | 이유 |
| --- | --- | --- |
| process mode | `worker_pipes` 단일 live topology | selectable transport 제거 |
| `ProcessConfig.route_batch_size=1` | supported | unbatched worker-affine path |
| `ProcessConfig.route_batch_size>1` | engine이 ordered batch capability를 광고할 때 같은 route lease 지원 | IPC amortization |
| ordered mode + `supports_ordered_route_batch=False` engine | effective route batch size `1` | fallback `submit_batch()`는 ordered sequential executor가 아님 |
| removed `shared_queue` value | startup/CLI/release-gate에서 거부 | 제거된 transport를 live evidence로 해석하지 않기 위해 |
| process micro-batch flags | 기존 의미 유지 또는 명시적 unsupported 처리 | `ProcessConfig.batch_size`와 `ProcessConfig.route_batch_size`를 분리하기 위해 |
| `worker_pipes` + recycle semantics 미구현 | startup reject | silent disable은 benchmark 해석을 오염시킴 |

지원 범위가 넓어지면 표를 수정해야지, 삭제하면 안 된다.

## Route-batch 방침

route batching은 process micro-batching과 별도 축이다.

- `ProcessConfig.batch_size`는 process payload compatibility knob로 남되
  worker-pipes profile에서는 기본 `1`이다.
- `ProcessConfig.route_batch_size`는 `WorkManager`가 같은 route에서 한 번에 lease할
  `WorkItem` 수를 제어하는 process-owned profile이다.
- `ProcessConfig.route_batch_size=64`가 #129의 process worker-pipe 기본값이다.
- async/common execution은 item-level lease를 사용한다.

아래 의미를 route batch 의미로 암묵적으로 바꾸면 안 된다.

- `flush_policy="size_or_timer"`
- `flush_policy="demand"`
- `flush_policy="demand_min_residence"`

## Worker lifecycle / shutdown 계약

관측 가능한 lifecycle은 현재 process engine과 최대한 유사해야 한다.

### Startup

- `process_count`만큼 worker 시작
- worker index / PID tracking 유지
- worker logging setup 현재 의미 유지
- `require_picklable_worker` validation은 transport와 무관하게 유지

### Runtime

- 각 worker는 자기 input channel에서 blocking receive
- worker는 자신이 실행할 payload envelope를 decode
- route-batch worker는 batch 내부 item을 순서대로 실행하고 첫 failure에서 멈춤
- 정상 route-batch completion은 하나의 `BatchCompletion` envelope로 보내고 parent가
  펼침
- parent-side registry event는 여전히 in-flight accounting에 의미가 있어야 함

### Shutdown

- sentinel 전송 전 buffered submission flush
- `worker_pipes` mode에서는 worker channel마다 sentinel 정확히 1개
- join / terminate / kill escalation 기존 정책 유지
- teardown 전에 registry/completion queue drain 유지
- prefetched completion과 already-queued completion에 대한 `wait_for_completion()` 기대를 유지

### Crash / restart / recycle guardrail

- 1차 실험은 ownership migration 없이 구현 가능한 현재 dead-worker recovery만 유지해도 된다.
- worker start event 또는 live-worker failure 이후에도 아직 start되지 않은
  route-batch tail은 recoverable해야 한다.
- worker restart policy는 transport별로 문서화해야 한다.
- `max_tasks_per_child`, `recycle_jitter_ms`는 의미를 유지하거나 `worker_pipes`에서 명시적으로 reject해야 한다.
- 구현 증거 없이 과거 shared-queue 경로와 동일한 lifecycle parity를 가진다고
  과장하면 안 된다.

## ordered mode와 unordered mode의 분리

이 실험은 ordered mode의 기본 원칙을 고정한다.

- ordered mode:
  - sticky routing / affinity preservation 우선
  - work stealing은 기본 원칙이 아님
- unordered mode:
  - 별도 balancing 또는 hybrid 연구 대상으로 분리 가능

즉 `worker_pipes`는 process execution의 live topology이고, stealing/dynamic
balancing 설계는 이 문서 범위가 아니다.

## Observability / benchmark 증거 계약

실험은 증거 없이는 완료가 아니다.

최소한 아래는 남겨야 한다.

- worker-pipes process benchmark evidence
- final lag / final gap evidence
- ordering validation evidence
- release-gate가 같은 correctness 기준으로 GO/NO-GO를 판단했다는 증거
- `process_transport_mode`가 남는다면 deprecated compatibility field로
  `"worker_pipes"`만 emit
- `route_batch_size`, `items_per_input_ipc`, `items_per_completion_ipc`,
  `route_batch_size_avg`, `route_batch_size_max` 같은 route-batch IPC metric
- reject/skip된 process-config 조합을 설명할 수 있는 로그나 runtime metadata
- release-gate summary는 missing 또는 `"worker_pipes"` process artifact만 live
  evidence로 인정하고, 제거된 transport 값은 invalid로 표시해야 함

Benchmark 보고서는 아래 질문에 바로 답할 수 있어야 한다.

1. `worker_pipes`가 partition-wide throughput을 개선했는가
2. `worker_pipes`가 key-wide throughput을 개선했는가
3. narrow workload 회귀가 허용 범위를 넘는가
4. final lag / gap이 `0/0`에서 벗어났는가
5. 어떤 조합이 명시적으로 reject 또는 skip되었는가

## 성공 기준

이 실험은 성능만 좋아서는 성공이 아니다. correctness가 계속 보여야 한다.

### Correctness gate

- ordering validation pass
- final lag = `0`
- final gap = `0`
- release-gate verdict가 약화된 검증이 아니라 기존 correctness 기준을 유지
- shutdown semantics가 미완료 in-flight work를 숨기지 않음

### Performance gate

- `partition` workload는 baseline sequential consumer와 동급 이상이어야 하고,
  이상적으로는 fixed-cost amortization으로 이를 넘어야 한다.
- `key_hash` workload는 active-key width가 process count보다 충분히 클 때 baseline
  sequential consumer를 큰 폭으로 넘어야 한다.
- `p=1`, `k=1` 같은 narrow workload가 과도하게 악화되지 않음
- improvement claim은 benchmark artifact를 인용함

## 권장 구현 slice

### Slice 1 — worker-pipes-only topology

- `transport_mode` config와 benchmark CLI plumbing 제거
- `ProcessConfig.route_batch_size=64` 추가
- execution-level route-batch config surface 제거
- worker-pipes startup + item-level routing + route-batch path 유지
- single completion queue 유지
- unsupported 조합은 명시적으로 reject

### Slice 2 — evidence / operational hardening

- release gate / metrics / benchmark JSON에서 live `shared_queue` surface 제거
- old artifact의 missing `process_transport_mode`는 worker-pipes compatible로 해석
- `"shared_queue"` artifact는 historical note 외 live release evidence에서 reject

### Slice 3 — worker-pipes route-batch dispatch

- 선택된 worker pipe에 하나의 route-batch payload 전송
- pending tail recoverability 유지
- worker 내부 순서 실행 보장

### Slice 4 — batch completion envelope

- executed prefix를 `BatchCompletion`으로 전송
- parent에서 item completion으로 expansion
- bounded cache로 legacy/batch completion overlap dedupe

### Slice 5 — benchmark / metric evidence

- `--route-batch-size` 추가
- benchmark JSON에 nullable route-batch IPC metric 노출
- performance claim은 저장된 benchmark artifact에 연결

## Non-goal

이 문서는 다음을 승인하지 않는다.

- production 기본 transport 변경
- hot-key skew 해결을 같은 slice에 넣는 것
- commit logic 재설계
- worker stealing / migration 추가
- unsupported lifecycle 조합을 parity라고 주장하는 것
- “input topology 실험”을 “process engine 전체 재작성”으로 확대하는 것

## 구현 후 문서에 남길 질문

구현이 끝나면 아래 질문에 측정값으로 답하면서 이 문서를 수정한다.

1. 어떤 workload shape가 얼마나 개선되었는가
2. 어떤 unsupported 조합을 계속 reject해야 하는가
3. shutdown / crash recovery / restart semantics가 transport별로 달라졌는가
4. 2차 투자 가치가 충분한가
5. `worker_pipes`는 계속 experimental이어야 하는가, 승격해야 하는가, 폐기해야 하는가
