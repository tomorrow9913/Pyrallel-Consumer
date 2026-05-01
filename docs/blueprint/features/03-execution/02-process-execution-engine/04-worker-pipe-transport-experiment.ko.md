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
- `01-requirements`: `shared_queue`는 compatibility path, `worker_pipes`는
  ordering-preserving direction path
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

현재 process engine만이 submit된 item을 single queue로 다시 합친다.

현재 process runtime:

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
  -> ProcessExecutionEngine.submit()
  -> transport router
  -> worker-specific input Pipe
  -> owner worker process
  -> single completion queue
```

1차 실험에서 바꾸는 것은 input transport뿐이다. completion aggregation,
control-plane commit 판단, worker function 실행 의미는 기존 위치를 유지한다.

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

1차 실험은 아래 가설만 검증한다.

> ordered process-mode에서 shared input queue가 유효 병렬성을 깎는 경계라면, worker별 input channel과 stable routing은 partition/key 폭이 충분한 workload에서 `shared_queue` 대비 더 높은 throughput을 보여야 한다. 단, ordering, final lag, final gap, release-gate correctness는 깨지면 안 된다.

이 문서는 `worker_pipes`가 production default가 된다고 약속하지 않는다.
하지만 `worker_pipes`를 **장기 기본 후보**로 평가할 수 있을 만큼의 증거를 만들 수
있는지 판단하려는 문서다.

## 실험 범위

반드시 명시적 transport option 뒤에 숨긴다.

```text
process_transport = shared_queue | worker_pipes
```

기본값은 반드시 `shared_queue`다.

### 1차 실험에 포함

- worker별 parent-to-worker 단방향 input pipe
- `WorkItem` identity 기반 stable routing
- 기존 single completion queue 재사용
- parent-side registry / in-flight accounting 재사용
- benchmark에서 transport 선택 가능
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

- `BrokerPoller`와 `WorkManager`는 `shared_queue`인지 `worker_pipes`인지 알지 못한다.
- `BaseExecutionEngine` public surface는 그대로 유지한다.
  - `submit(work_item)`
  - `poll_completed_events(batch_limit=1000)`
  - `wait_for_completion(timeout_seconds=None)`
  - `get_in_flight_count()`
  - `get_runtime_metrics()`
  - `shutdown()`
- 실행 가능한 `WorkItem`을 고르는 책임은 계속 `WorkManager`에 있다.
- completion aggregation과 offset commit 판단은 parent/control plane에 남는다.
- commit clamp용 최소 in-flight offset은 control-plane `WorkManager`
  dispatch ledger에서 계산한다. engine-level `get_min_inflight_offset()`는
  있더라도 compatibility/private recovery state일 뿐 canonical source가 아니다.
- transport는 ordering correctness를 새로 판단하지 않는다. 이미 safe-to-run으로
  내려온 item을 어느 worker channel로 보낼지만 결정한다.

## Routing 계약

1차 prototype은 기존 `WorkItem`이 이미 갖고 있는 logical identity를 재사용한다.

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

## Config / CLI 계약

`ProcessConfig`에 transport selector를 추가한다.

```python
transport_mode: Literal["shared_queue", "worker_pipes"] = "shared_queue"
```

Config 요구사항:

- 기본값은 `shared_queue`
- env override는 기존 `PROCESS_` naming pattern을 따른다.
- invalid 값은 config validation에서 즉시 실패한다.
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

Benchmark CLI에도 같은 선택지를 노출한다.

```bash
--process-transport shared_queue|worker_pipes
```

전달 경로는 명시적으로 유지한다.

```text
benchmark CLI
  -> benchmark config builder
  -> KafkaConfig.parallel_consumer.execution.process_config.transport_mode
  -> ProcessExecutionEngine
```

## 1차 실험 지원/비지원 매트릭스

이 실험은 silent fallback보다 explicit rejection을 선호한다.

| Surface | 1차 규칙 | 이유 |
| --- | --- | --- |
| `transport_mode=shared_queue` | fully supported | baseline 유지 |
| `transport_mode=worker_pipes` + `batch_size=1` | supported | topology만 검증하는 최소 단위 |
| `worker_pipes` + timer/demand batching | fully 구현 전에는 startup reject | batching 재설계와 transport 검증을 섞지 않기 위해 |
| `worker_pipes` + recycle semantics 미구현 | startup reject | silent disable은 benchmark 해석을 오염시킴 |
| invalid transport value | config validation failure | 실험 범위 고정 |

지원 범위가 넓어지면 표를 수정해야지, 삭제하면 안 된다.

## Batching 방침

이 실험의 1순위는 input topology다. batching sophistication은 후순위다.

권장 1차 설정:

```text
batch_size = 1
max_batch_wait_ms = 0
```

이렇게 해야 timer flush나 residence policy 복잡도를 transport 실험과 분리할 수 있다.

`worker_pipes`가 더 넓은 batching을 지원하게 되면 2차 slice로 분리해서 문서화한다. 아래 의미를 암묵적으로 바꾸면 안 된다.

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
- completion event는 기존 completion queue로 보냄
- parent-side registry event는 여전히 in-flight accounting에 의미가 있어야 함

### Shutdown

- sentinel 전송 전 buffered submission flush
- `worker_pipes` mode에서는 worker channel마다 sentinel 정확히 1개
- join / terminate / kill escalation 기존 정책 유지
- teardown 전에 registry/completion queue drain 유지
- prefetched completion과 already-queued completion에 대한 `wait_for_completion()` 기대를 유지

### Crash / restart / recycle guardrail

- 1차 실험은 ownership migration 없이 구현 가능한 현재 dead-worker recovery만 유지해도 된다.
- worker restart policy는 transport별로 문서화해야 한다.
- `max_tasks_per_child`, `recycle_jitter_ms`는 의미를 유지하거나 `worker_pipes`에서 명시적으로 reject해야 한다.
- 구현 증거 없이 “shared_queue와 동일하다”라고 과장하면 안 된다.

## ordered mode와 unordered mode의 분리

이 실험은 ordered mode의 기본 원칙을 고정한다.

- ordered mode:
  - sticky routing / affinity preservation 우선
  - work stealing은 기본 원칙이 아님
- unordered mode:
  - 별도 balancing 또는 hybrid 연구 대상으로 분리 가능

즉 `worker_pipes`는 ordered parallelism을 검증하는 transport이고, stealing/dynamic
balancing 설계는 이 문서 범위가 아니다.

## Observability / benchmark 증거 계약

실험은 증거 없이는 완료가 아니다.

최소한 아래는 남겨야 한다.

- `shared_queue` vs `worker_pipes` benchmark matrix
- final lag / final gap evidence
- ordering validation evidence
- release-gate가 같은 correctness 기준으로 GO/NO-GO를 판단했다는 증거
- transport-specific benchmark metadata
- reject/skip된 transport-config 조합을 설명할 수 있는 로그나 runtime metadata
- release-gate summary가 관측된 `process_transport_mode` 목록을 포함해 artifact 비교 시 어떤 transport가 평가되었는지 드러내야 함

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

- `partition` workload에서 width가 `process_count` 이상이면 `shared_queue` 대비 개선
- `key_hash` workload에서 active-key width가 `process_count` 이상이면 `shared_queue` 대비 개선
- `p=1`, `k=1` 같은 narrow workload가 과도하게 악화되지 않음
- improvement claim은 benchmark artifact를 인용함

## 권장 구현 slice

### Slice 1 — bounded transport toggle

- `transport_mode` config와 benchmark CLI plumbing 추가
- `shared_queue` path는 그대로 유지
- `worker_pipes` startup + routing은 `batch_size=1` 기준으로만 구현
- single completion queue 유지
- unsupported 조합은 명시적으로 reject

### Slice 2 — evidence / operational hardening

- benchmark/report metadata에 transport mode 노출
- release-gate와 benchmark summary에서 transport mode 확인 가능하게 유지
- restart/recycle limitation이 남으면 문서와 결과에 명시
- py-spy / benchmark evidence를 문서 본문 또는 결과 요약에 연결

### Slice 3 — optional expansion

- 1차 증거가 좋을 때만 richer batching, recycle parity, advanced routing을 검토

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
