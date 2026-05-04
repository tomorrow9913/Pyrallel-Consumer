# Process Execution Engine Index

이 문서는 `process-execution-engine` subfeature의 목차이자 방향성 요약이다.
이 subfeature는 단순한 process worker pool 설명이 아니라, `WorkManager`가 이미
결정한 ordered virtual queue identity를 process boundary 너머까지 보존하는
장기 방향을 다룬다.

## 이 subfeature의 장기 목표

`ProcessExecutionEngine`의 목표 모델은 다음과 같다.

- `WorkManager`가 partition/key별 virtual queue로 ordering과 eligibility를 먼저
  결정한다.
- async engine은 `submit()` 순간 `create_task()`로 실행 계층에 work를 바로
  흩뿌리므로 input queue에서 다시 하나로 합치지 않는다.
- process engine도 장기적으로는 같은 철학을 따라야 한다.
- 즉, `WorkManager`가 safe-to-run item을 고르면 process engine은 submit 순간
  route identity를 사용해 적절한 worker execution slot/channel로 보내야 한다.
  이 경계는 단일 `submit(work_item)`과 route 단위
  `submit_batch(work_items)`를 모두 포함한다.
- 이 route identity는 process 전용 새 scheduling hint가 아니다. async path에서도
  `WorkManager`가 이미 사용하는 동일한 logical queue identity를 process IPC
  routing에 재사용하는 것이다.

이 문서 세트는 #129 이후의 방향을 기준으로 한다.

- `shared_queue`는 역사적 비교/마이그레이션 맥락으로만 남는다.
- process execution의 live topology는 `worker_pipes` 단일 경로다.
- `worker_pipes`는 ordered virtual queue identity를 process 경계 너머까지 더 잘
  보존하는 worker-affine input channel topology다.
- route-batch size는 global execution 기본값이 아니라 process worker-pipe
  transfer/admission profile이다.

## 왜 방향 전환이 필요한가

제거된 `shared_queue` process engine은 submit된 item을 single
`multiprocessing.Queue`에 넣고 모든 worker가 같은 queue에서 `get()` 경쟁을 했다.
이 구조는 일반적인 process pool로는 단순하지만, ordered partition workload에서는
`WorkManager`가 이미 나눠 둔 logical queue를 process boundary 앞에서 다시 하나로
합치는 문제가 있었다.

현재 benchmark / py-spy evidence는 다음을 시사한다.

- ordered partition workload에서 병목은 completion aggregation보다
  **input dispatch topology** 쪽에 더 가깝다.
- py-spy에서 worker time 대부분은 실제 worker function보다
  `_receive_task_payload -> multiprocessing.Queue.get -> synchronize.__enter__ -> connection.recv_bytes`
  경로에 있었다.
- 실제 `_io_worker_process` 비중은 매우 작았다.

따라서 이 subfeature는 “process worker pool” 설명에서 멈추지 않고,
**worker-affine input channel topology를 어떻게 도입할 것인가**를 함께 다뤄야
한다.

## 이 subfeature가 답하는 질문

- process mode에서 worker는 어떤 제약을 가져야 하는가
- ordered virtual queue identity를 process boundary 너머에서 어떻게 보존할 것인가
- 제거된 `shared_queue` 경로를 어떤 migration/release-note 맥락으로 설명할 것인가
- micro-batching, wait-for-completion, shutdown, recycle은 transport별로 어디까지
  같은 의미를 유지해야 하는가
- completion aggregation은 어디까지 single aggregator로 유지할 것인가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.ko.md](./01-requirements.ko.md) | process engine 책임, transport mode, acceptance 기준 |
| [02-architecture.ko.md](./02-architecture.ko.md) | shared queue topology와 target worker-affine topology 비교 |
| [03-design.ko.md](./03-design.ko.md) | config, routing identity, route batching/lifecycle/runtime contract |
| [04-worker-pipe-transport-experiment.ko.md](./04-worker-pipe-transport-experiment.ko.md) | worker-pipe transport와 route-batch 실험 계약 |

## 현재 route-batch 구현 상태

- `shared_queue`는 더 이상 runtime selector나 fallback path가 아니다.
- `ProcessConfig.route_batch_size` 기본값은 `64`이며 process worker-pipe
  전송/admission profile에 속한다.
- async/common execution은 item-level WorkManager lease를 유지하며 별도의
  execution-level route-batch config surface를 갖지 않는다.
- `WorkManager`는 engine이 `supports_ordered_route_batch=True`를 광고할 때만
  ordered route에서 batch lease를 허용한다.
- `worker_pipes`는 같은 route의 item 묶음을 하나의 route-batch payload로 보내고,
  worker는 batch 내부 item을 순서대로 실행한다.
- worker-to-parent completion은 정상 경로에서 하나의 internal `BatchCompletion`
  envelope로 이동하고, parent가 기존 `CompletionEvent` 목록으로 펼친다.
- registry, retry, commit, recovery accounting은 item 단위를 유지한다. batch는
  transport envelope일 뿐 commit/retry 단위가 아니다.

## 빠른 읽기 분기

- 장기 방향과 왜 shared queue가 충분하지 않은지 먼저 보려면
  `01-requirements.ko.md`
- current topology와 target topology를 비교해서 보려면
  `02-architecture.ko.md`
- config, lifecycle, batching, `wait_for_completion()`, shutdown/recycle 제약을
  보려면 `03-design.ko.md`
- worker-pipe slice를 실제로 어떻게 검증할지 보려면
  `04-worker-pipe-transport-experiment.ko.md`
