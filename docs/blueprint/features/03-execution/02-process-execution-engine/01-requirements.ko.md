# Process Execution Engine Requirements

## 1. 문서 목적

이 문서는 `ProcessExecutionEngine`의 책임을 “process worker pool” 수준에서 끝내지
않고, `WorkManager`가 이미 결정한 ordered virtual queue identity를 process
boundary 너머까지 보존하는 장기 요구사항으로 정의한다.

즉 process engine의 역할은 단순히 sync worker를 별도 프로세스에서 실행하는 것이
아니다. `WorkManager`가 safe-to-run이라고 판단한 item이 execution layer에
도달할 때 불필요하게 다시 하나로 합쳐지지 않도록 transport topology를 제공하는
것도 이 subfeature의 책임이다.

## 2. 배경과 문제 정의

현재 control plane은 이미 ordering과 eligibility를 먼저 정한다.

- `WorkManager`는 partition/key별 virtual queue를 가진다.
- 그 위에서 ordering mode에 맞춰 safe-to-run item을 고른다.
- async engine은 submit 순간 `create_task()`로 실행 계층에 work를 바로
  분산하므로, input queue 단계에서 다시 하나로 합치지 않는다.

반면 현재 process engine은:

- submit된 item을 single `multiprocessing.Queue`에 넣고,
- 모든 worker process가 같은 queue에서 `get()` 경쟁한다.

현재 benchmark / py-spy 결과가 시사하는 문제는 다음과 같다.

- ordered partition workload에서 worker side 비용 대부분이 실제 worker function이
  아니라 `_receive_task_payload -> multiprocessing.Queue.get -> synchronize.__enter__ -> connection.recv_bytes`
  쪽에 있었다.
- 실제 `_io_worker_process` 비중은 매우 작았다.
- 따라서 현재 병목 후보는 completion aggregation보다 input dispatch topology가
  더 우선이다.

## 3. 장기 목표

process execution engine의 목표 모델은 다음과 같다.

- `WorkManager`가 ordering/eligibility를 판단한다.
- process engine은 `submit(work_item)` 시점에 route identity를 사용해 적절한
  worker execution slot/channel을 선택한다.
- route identity는 async와 다른 별도 scheduling hint가 아니라, `WorkManager`가
  partition/key virtual queue에서 이미 사용한 logical queue identity다.
- ordered mode에서는 sticky routing / affinity preservation이 기본 원칙이다.
- `shared_queue`는 default compatibility path로 남는다.
- `worker_pipes`는 ordering-preserving parallelism을 검증하고, 장기적으로는 기본
  후보가 될 수 있는 path다.

이 문서는 `worker_pipes`를 production default로 즉시 전환하라고 요구하지는 않는다.
하지만 process engine이 장기적으로 worker-affine input channel topology를 지원해야
한다는 점은 명시적으로 요구한다.

## 4. 책임

- sync worker를 별도 프로세스에서 실행해야 한다.
- process transport mode를 통해 compatibility path와 target direction을 함께
  지원해야 한다.
- `shared_queue`와 `worker_pipes` 모두에서 `BaseExecutionEngine` 공개 계약을
  유지해야 한다.
- completion 결과를 control plane이 소비할 수 있는 형태로 surface 해야 한다.
- worker crash/timeout/restart/shutdown을 consumer 전체 crash와 분리해야 한다.
- batching, `wait_for_completion()`, shutdown, recycle semantics를 transport별로
  명시적으로 정의해야 한다.
- runtime metrics surface가 transport 차이를 설명할 수 있어야 한다.

## 5. 기능 요구사항

### 5.1 transport mode

- process mode는 최소 두 transport를 문서화해야 한다.
  - `shared_queue`
  - `worker_pipes`
- 기본값은 `shared_queue`여야 한다.
- transport mode는 `ProcessConfig`를 통해 선택 가능해야 한다.
- invalid transport 값은 startup 이전에 config validation으로 실패해야 한다.

### 5.2 control-plane compatibility

- `WorkManager`와 `BrokerPoller`는 transport mode를 몰라야 한다.
- `BaseExecutionEngine.submit(work_item)` 계약은 유지해야 한다.
- completion queue는 1차로 single aggregator를 유지해야 한다.
- process transport 변경이 commit / broker I/O / retry policy를 직접 바꿔서는 안
  된다.

### 5.3 ordered throughput direction

- ordered mode에서 process engine은 가능한 한 route identity를 보존해야 한다.
- ordered partition/key workload에서는 input topology가 logical queue를 다시
  하나로 합치지 않는 방향이 우선이다.
- work stealing / dynamic balancing은 unordered mode 또는 후속 hybrid 연구로
  분리해야 한다.
- ordered mode의 기본 원칙은 stealing이 아니라 sticky routing / affinity
  preservation이다.

### 5.4 worker / batching / lifecycle

- process mode에서는 coroutine worker를 허용하지 않아야 한다.
- picklable worker 검증이 설정으로 강제될 수 있어야 한다.
- batching은 IPC 비용 절감 수단이어야 하며 ordering correctness 계층을 대체하면
  안 된다.
- `wait_for_completion()`은 transport별로 관측 가능한 의미가 동일해야 한다.
- shutdown은 sentinel, join, terminate/kill escalation 순서를 유지해야 한다.
- recycle semantics는 transport별로 유지하거나 명시적으로 reject해야 한다.

## 6. 비기능 요구사항

- ordered partition workload에서 shared input queue 병목을 줄이는 방향이
  benchmark로 설명 가능해야 한다.
- msgpack payload는 size guard를 가져야 한다.
- 프로세스 누수가 없어야 한다.
- crash/timeout이 consumer 전체 crash로 번지지 않아야 한다.
- metrics / benchmark / release-gate evidence가 transport 차이를 해석할 수
  있어야 한다.

## 7. 입력/출력 경계

입력:

- `WorkItem`
- sync picklable worker
- process nested config
- `WorkManager`가 선택한 safe-to-run item

출력:

- transport-specific input dispatch
- `CompletionEvent`
- registry event(start/timeout/finish/recycle/restart)
- worker process lifecycle state
- runtime metrics / benchmark evidence

## 8. 제외 범위

이 요구사항 문서는 다음을 포함하지 않는다.

- production default를 지금 당장 `worker_pipes`로 바꾸는 결정
- work stealing 구현
- broker I/O bridge와의 결합
- completion queue를 worker별로 나누는 설계
- shared-memory / ring-buffer 같은 별도 transport 확장

## 9. Acceptance 기준

- `shared_queue`가 compatibility/default path라는 점이 명확해야 한다.
- `worker_pipes`가 ordered throughput 개선을 위한 장기 방향이라는 점이
  명확해야 한다.
- `WorkManager`가 이미 virtual queue를 가지고 ordering/eligibility를 결정한다는
  점이 설명돼야 한다.
- async engine이 input queue에서 다시 하나로 합치지 않는 비교 기준으로
  설명돼야 한다.
- py-spy / benchmark evidence가 현재 shared queue input topology를 병목 후보로
  지목한다는 점이 드러나야 한다.
- config, lifecycle, batching, `wait_for_completion()`, shutdown/recycle,
  metrics surface가 문서에서 빠지지 않아야 한다.
