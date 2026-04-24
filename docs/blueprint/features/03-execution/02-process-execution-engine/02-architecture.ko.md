# Process Execution Engine Architecture

## 1. 문서 목적

이 문서는 `ProcessExecutionEngine`의 현재 topology와 목표 topology를 비교해
설명한다. 핵심은 process mode가 단지 worker process를 띄우는 계층이 아니라,
`WorkManager`의 ordered virtual queue identity를 process boundary 너머에서도
가능한 한 보존해야 한다는 점이다.

## 2. 상위 경계

상위 control plane의 역할은 이미 분리돼 있다.

- `BrokerPoller`는 poll / revoke / runtime coordination을 담당한다.
- `WorkManager`는 ordering mode에 맞춰 virtual queue를 유지하고 safe-to-run item을
  선택한다.
- execution engine은 selection 이후의 실행 transport를 담당한다.

이 경계는 process transport가 바뀌어도 유지해야 한다.

즉:

- `WorkManager`는 transport mode를 몰라야 하고,
- process engine은 `submit(work_item)` 안에서 내부 topology를 선택해야 한다.

## 3. 현재 topology: shared queue compatibility path

현재 process runtime은 아래와 같다.

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit()
  -> BatchAccumulator / msgpack payload
  -> shared multiprocessing.Queue
  -> N workers compete on get()
  -> single completion queue
  -> single registry event queue
```

이 구조의 특징:

- 구현이 단순하다.
- 일반적인 process pool과 호환된다.
- 그러나 `WorkManager`가 이미 만든 logical queue 분리를 process boundary 앞에서
  다시 하나의 input queue로 합친다.

## 4. target topology: worker-affine input channel path

장기 방향의 target topology는 아래와 같다.

```text
WorkManager virtual queues
  -> ProcessExecutionEngine.submit()
  -> route identity resolution
  -> worker-affine execution slot/channel
  -> owner worker process
  -> single completion queue
  -> single registry event queue
```

`worker_pipes`는 이 방향을 실험/구현하는 첫 transport다.

핵심 차이:

- current topology는 “submit된 item을 한 input queue로 모은 뒤 경쟁”한다.
- target topology는 “submit 순간 적절한 worker channel을 선택”한다.
- channel 선택에 쓰는 identity는 새로 만든 process-only hint가 아니라,
  async path에서도 `WorkManager`가 safe-to-run queue를 고를 때 쓰는 같은
  logical queue identity다.

## 5. 왜 input topology가 우선인가

현재 py-spy / benchmark evidence는 다음을 보여준다.

- ordered partition workload에서 worker side 대부분의 시간이 실제 worker function
  바깥에 있었다.
- sampling stack 상단은 주로:
  - `_receive_task_payload`
  - `multiprocessing.Queue.get`
  - `synchronize.__enter__`
  - `connection.recv_bytes`
  였다.
- 실제 `_io_worker_process` 비중은 작았다.

이 증거는 현재 병목 후보가 worker function 자체보다 **shared input queue를 통한
payload 수신/경쟁**에 더 가깝다는 뜻이다.

따라서 architecture priority는:

1. completion aggregation을 먼저 분산하는 것보다
2. input dispatch topology를 affinity-preserving 형태로 바꾸는 것이 더 높다.

## 6. transport mode architecture

### 6.1 shared_queue

- 역할: 기본값, 호환성, fallback path
- input: single shared queue
- completion: single aggregator
- 장점: 단순성, 기존 경로 유지
- 약점: ordered workload에서 logical queue를 다시 하나로 합침

### 6.2 worker_pipes

- 역할: ordered parallelism 검증과 장기 방향의 first-class path
- input: worker별 parent-to-worker channel
- routing: `WorkItem` route identity 기반 sticky dispatch
- completion: 기존 single aggregator 유지
- 장점: ordered affinity preservation
- 약점: batching/recycle/restart parity를 transport별로 더 명시해야 함

## 7. route identity와 affinity

ordered mode에서 process engine 내부는 route identity를 사용해야 한다.

```text
route_identity = (topic, partition, key)
```

원칙:

- 같은 identity는 같은 worker channel로 간다.
- identity는 `WorkManager` virtual queue의 `(TopicPartition, key)` 의미를 process
  boundary 너머로 보존한 것이다.
- ordered mode의 기본 원칙은 stealing이 아니라 affinity preservation이다.
- unordered mode에서만 별도 balancing policy를 연구 대상으로 둘 수 있다.

## 8. IPC 계층

현재와 목표 architecture 모두에서 다음 경계는 유지한다.

| 계층 | 유지할 계약 |
| --- | --- |
| input dispatch | transport mode별로 달라질 수 있음 |
| completion aggregation | 1차로 single queue 유지 |
| registry event queue | parent-side in-flight/lifecycle accounting 유지 |
| logging queue | worker log를 parent listener에 집계 |

즉, 지금 바꾸려는 것은 “input topology”지 “completion topology”가 아니다.

## 9. lifecycle / shutdown architecture

transport가 달라도 아래 lifecycle 축은 parent에서 일관되게 보존해야 한다.

- worker start / PID tracking
- registry event drain
- `wait_for_completion()` semantics
- buffered submission flush
- sentinel delivery
- join -> terminate -> kill escalation
- recycle / restart handling

`worker_pipes`는 이 lifecycle을 가능한 한 shared queue와 가깝게 유지하되,
동일하지 못한 부분은 unsupported matrix로 명시해야 한다.

## 10. architecture에서 명시적으로 제외할 것

다음은 이 문서의 architecture 방향에 포함하지 않는다.

- production default를 즉시 `worker_pipes`로 바꾸는 결정
- work stealing 구현 지시
- broker I/O bridge와의 통합
- worker별 completion queue 분리
- shared memory / ring buffer transport 확장
