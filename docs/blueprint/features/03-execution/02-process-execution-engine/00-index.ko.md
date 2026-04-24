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

이 문서 세트는 그 방향을 다음 두 경로로 정리한다.

1. **compatibility path**
   - `shared_queue`
   - 현재의 shared `multiprocessing.Queue` 기반 worker pool
   - 기본값과 fallback 역할 유지
2. **target direction**
   - `worker_pipes`
   - ordered virtual queue identity를 process 경계 너머까지 더 잘 보존하는
     worker-affine input channel topology
   - ordered throughput 개선의 장기 후보

## 왜 방향 전환이 필요한가

현재 process engine은 submit된 item을 single `multiprocessing.Queue`에 넣고 모든
worker가 같은 queue에서 `get()` 경쟁을 한다. 이 구조는 일반적인 process pool로는
단순하지만, ordered partition workload에서는 `WorkManager`가 이미 나눠 둔 logical
queue를 process boundary 앞에서 다시 하나로 합치는 문제가 있다.

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
- `shared_queue`와 `worker_pipes`를 어떤 계약 아래 공존시킬 것인가
- micro-batching, wait-for-completion, shutdown, recycle은 transport별로 어디까지
  같은 의미를 유지해야 하는가
- completion aggregation은 어디까지 single aggregator로 유지할 것인가

## 문서 역할

| 문서 | 역할 |
| --- | --- |
| [01-requirements.ko.md](./01-requirements.ko.md) | process engine 책임, transport mode, acceptance 기준 |
| [02-architecture.ko.md](./02-architecture.ko.md) | shared queue topology와 target worker-affine topology 비교 |
| [03-design.ko.md](./03-design.ko.md) | config, routing identity, batching/lifecycle/runtime contract |
| [04-worker-pipe-transport-experiment.ko.md](./04-worker-pipe-transport-experiment.ko.md) | worker-affine topology를 bounded slice로 검증하는 실험 지시서 |

## 빠른 읽기 분기

- 장기 방향과 왜 shared queue가 충분하지 않은지 먼저 보려면
  `01-requirements.ko.md`
- current topology와 target topology를 비교해서 보려면
  `02-architecture.ko.md`
- config, lifecycle, batching, `wait_for_completion()`, shutdown/recycle 제약을
  보려면 `03-design.ko.md`
- worker-pipe slice를 실제로 어떻게 검증할지 보려면
  `04-worker-pipe-transport-experiment.ko.md`
