# 처리량 실험 청사진

이 문서는 benchmark-driven throughput 작업의 현재 연구 방향과 구현 방향을 정리한다.
영어 canonical 문서는 [04-throughput-experiment-blueprint.md](./04-throughput-experiment-blueprint.md)다.

## 목표

최근 benchmark에서는 작은 workload의 ordered scenario에서 async와 process가 모두 baseline보다 느려질 수 있다.
따라서 첫 최적화 slice는 엔진별 병렬성 확장보다 모든 엔진과 ordering mode가 공유하는 control-plane 공통 경로를 먼저 줄인다.

## 연구 프레임

- **Span 상한:** completion ready부터 다음 refill까지의 공통 critical path를 줄인다. 즉시 의심할 지점은 `WorkManager`의 per-completion scheduling churn과 `BrokerPoller`의 post-drain scheduling pass다.
- **Data-movement 상한:** worker 수를 늘리기 전에 queue hop, 반복 metadata scan, payload/envelope 이동을 줄인다.
- **Queue variability 상한:** 평균 TPS뿐 아니라 p95/p99 completion-to-refill delay와 queue age를 본다.
- **Scheduling/locality 상한:** batch release 경로가 충분히 얇아진 뒤에는 repeated global scan보다 dirty-ready 또는 locality-preserving scheduling 구조를 검토한다.
- **Pipeline semantics 상한:** runtime을 명시적인 buffer/handoff policy를 가진 bounded dataflow graph로 다룬다.

## 현재 첫 slice

초기 실험은 작은 common-path 변경이다.

1. `WorkManager.poll_completed_events()`는 completion batch를 처리하고 해당 batch에 대해 내부 refill scheduling을 최대 한 번만 수행한다.
2. `BrokerPoller._drain_completion_events_once()`는 즉시 WorkManager refill overlap을 유지한 뒤, 기존 broker-level post-processing과 한 번의 refill pass를 수행한다. 완전한 broker-side defer는 실험 knob로 남기되, 첫 benchmark에서 partition-order throughput이 낮아졌으므로 기본값으로 두지 않는다.
3. blocking-timeout, poison/fail-fast event 같은 locally generated completion은 기존 WorkManager와 BrokerPoller safety path에 계속 보인다.
4. `WorkManager` 책임은 stale-epoch fencing, local offset-tracker bookkeeping, poison-message accounting, in-flight/order-state release까지로 제한한다. DLQ ledger ordering과 final offset completion은 broker completion processing에 남긴다.

## 제외 항목

- 이 slice에서는 speculative window를 추가하지 않는다. 어떤 workload는 output order뿐 아니라 execution order 자체가 중요할 수 있다.
- 더 큰 batch를 만들기 위한 completion linger를 추가하지 않는다. completion envelope는 다음 pass에서 이미 준비된 completion만 bounded budget 안에서 가져온다.
- async/process engine 내부 구현을 control plane으로 누수하지 않는다.
- benchmark evidence가 lost overlap을 다른 방식으로 회복한다고 보여주기 전에는 유일한 refill pass를 broker completion processing 뒤로 옮기지 않는다.

## 다음에 수집할 evidence

첫 profiler pass는 우선순위를 바꿨다. async partition strict-on profiling에서는 `WorkManager.poll_completed_events`나 `WorkManager.schedule`보다 `_commit_ready_offsets`와 `_commit_offsets`가 훨씬 컸다. 따라서 다음 구현 slice는 completion release/refill은 즉시 유지하고, Kafka commit만 dirty partition과 bounded cadence 정책으로 분리하는 쪽이어야 한다.

- completion drain events per pass
- completion drain duration
- completion-to-refill duration
- schedule calls per completion
- schedule iterations per pass
- refill count per pass
- control-lock hold duration
- commit candidate count와 commit interval
- stale completion drop count

## 수정된 다음 slice

다음 실험은 readiness와 commit transmission을 분리한다.

1. worker는 completion을 즉시 보고한다.
2. `WorkManager`는 in-flight/order state를 해제하고 즉시 refill한다.
3. broker completion processing은 DLQ safety check 이후 offset을 complete로 표시한다.
4. commit planner는 dirty partition을 기록하고 bounded cadence로 가장 작은 committable offset을 commit한다. revoke, shutdown, explicit drain boundary에서는 즉시 flush한다.

성공 기준:

- partition strict-on run에서 message당 commit call이 크게 줄어든다.
- completion-to-refill latency가 악화되지 않는다.
- rebalance와 shutdown commit safety가 기존과 동일하다.
- p99 processing latency가 현재 envelope 안에 머문다.

구현 상태:

- `BrokerPoller`는 broker completion processing이 processed completion을
  보고한 뒤 dirty partition을 기록한다.
- non-forced commit attempt는 `commit_debounce_completion_threshold`만큼
  completion이 처리되거나 `commit_debounce_interval_ms` 시간이 지난 뒤에만
  실행된다.
- revoke는 기존 synchronous rebalance commit path를 그대로 사용하고,
  graceful shutdown은 `_commit_ready_offsets(force=True)`를 호출해 pending safe
  offset을 즉시 flush한다.
- `WorkManager` release/refill은 즉시 유지된다. debounce는 Kafka commit
  transmission에만 적용된다.
- benchmark final metrics는 `BrokerPoller.stop()` 이후 기록한다. 따라서 release
  gate summary는 stop 직전 debounce window가 아니라 forced final commit 이후
  상태를 본다.

초기 검증:

- `benchmarks/results/20260424T073444Z.json`: 8000-message async partition
  strict-on run은 baseline 2115.71 TPS 대비 1869.97 TPS까지 개선됐고,
  final lag/gap은 `0/0`이다.
- `benchmarks/results/20260424T073509Z.json`: 8000-message process partition
  strict-on run은 1006.73 TPS였고, final lag/gap은 `0/0`이다.
- commit cadence가 common-path fixed cost였다는 점은 확인됐다. async는 아직
  baseline보다 낮으므로 다음 실험은 process-specific IPC로 바로 넘어가기 전에
  남은 backpressure/refill/commit overhead를 다시 profiling하는 방향이 맞다.

## 수동 py-spy 실행 결과

사용자가 macOS `py-spy` 명령을 실행해 기대한 artifact가 생성됐다.

- Process run:
  `benchmarks/results/pyspy/codex-commit-cadence/process-partition-strict.speedscope.json`
  및 `benchmarks/results/20260424T070748Z.json`
- Async run:
  `benchmarks/results/pyspy/codex-commit-cadence/async-partition-strict.speedscope.json`
  및 `benchmarks/results/20260424T070807Z.json`

process partition strict-on 결과는 8000 messages 기준 492.81 TPS, p99
processing latency 7.78 ms였다. subprocess 포함 profile에서는 broker commit
path보다 worker process 내부 대기가 지배적이다. `ProcessExecutionEngine._worker_loop`는
aggregate sample의 약 60.8%, `multiprocessing.Queue.get`은 약 67.6%,
`multiprocessing.Connection.recv_bytes`는 약 15.3%를 차지했다. 실제 benchmark
worker function은 약 1.1%뿐이다. broker/control frame은 작다.
`BrokerPoller._drain_completion_events_once`는 약 0.30%,
`WorkManager.poll_completed_events`는 약 0.27%, `_commit_ready_offsets`는 약
0.23%, `_commit_offsets`는 약 0.09%였다.

async partition strict-on 결과는 8000 messages 기준 1083.67 TPS, p99
processing latency 3.04 ms였다. `py-spy`는 blocking Kafka operation에 사용되는
executor thread를 많이 sampling하므로 async awaited commit time을 귀속하는
데에는 yappi wall profile보다 약하다. 따라서 async에서는 이전 yappi 결과,
즉 `_commit_ready_offsets`와 `_commit_offsets`가 common path를 지배한다는
증거를 더 강하게 본다.

수정된 해석:

- async partition strict-on은 dirty-partition commit debounce를 먼저 실험한다.
- process partition strict-on은 single-partition strict-order workload에서
  process 수 추가나 generic completion batching의 효과를 기대하기 어렵다.
  effective concurrency는 item 1개인데 process runtime은 queue/IPC 비용을
  치르고 대부분의 worker는 blocked 상태로 남는다.
- WorkManager release/refill은 즉시 유지한다. 이전 broker-side defer 실험은
  refill overlap을 잃으면 throughput이 떨어진다는 증거였다.
- process strict-partition은 별도 2차 track으로 둔다. effective-concurrency-aware
  async/inline fast path, single-worker affinity, 또는 lower-overhead transport를
  scheduling machinery 추가보다 먼저 검토한다.

## 컨텍스트 압축 이후 실험 계획

이 절은 context compaction 이후 handoff로 사용한다. 현재 evidence는 worker/process 튜닝을 먼저 하라는 쪽이 아니라 commit cadence와 control-plane fixed cost를 먼저 보라는 쪽이다.

### 실험 1: dirty-partition commit debounce

가설:

> partition strict-on이 baseline에 지는 주 원인은 completion-driven commit work가 거의 item마다 한 번씩 실행되기 때문이다.

구현 방향:

- completion ingestion과 WorkManager release/refill은 즉시 유지한다.
- broker completion processing이 offset을 complete로 표시할 때 dirty partition set을 기록한다.
- dirty partition은 `N completions`, `M milliseconds`, revoke, shutdown, explicit drain completion 기준으로 commit한다.
- revoke와 shutdown은 즉시 flush boundary로 유지한다.
- commit safety의 기준은 계속 `get_committable_high_water_mark(min_inflight)`로 둔다.

측정값:

- message당 commit call 수
- `_commit_ready_offsets` wall time
- `_commit_offsets` wall time
- completion-to-refill latency
- final lag/gap count
- ordering validation
- 같은 partition strict-on workload의 baseline / async / process TPS

예상:

- yappi에서 commit work가 크게 나온 async partition strict-on이 먼저 개선될 가능성이 높다.
- process partition strict-on은 IPC가 남아 개선폭이 작을 수 있지만, refill overlap을 유지하면 악화되면 안 된다.

### 실험 1.1: caller-side commit/backpressure gating

commit debounce 직후 바로 진행한다. debounce 이후 profile에서도 실제 broker commit 수보다 helper 호출 수가 훨씬 많았으므로, 다음 low-risk slice는 상태가 바뀌지 않은 control-plane check가 broker API까지 내려가지 않게 막는 것이다.

구현 방향:

- dirty partition이 있고 debounce cadence가 열렸거나 idle force flush가 안전할 때만 `_commit_ready_offsets()`를 호출한다.
- pending DLQ는 force commit veto로 유지한다.
- shutdown과 revoke force path는 caller-side gate 밖에 둔다.
- `_check_backpressure()`에서는 consumer가 paused 상태가 아니고 adaptive controller가 꺼져 있으며 현재 load가 pause threshold를 넘을 수 없으면 broker `assignment()/pause()/resume()` 호출을 건너뛴다.
- consumer가 paused 상태이면 resume path는 항상 열어둔다.

성공 기준:

- item당 `_commit_ready_offsets` 호출 감소
- 변화 없는 unpaused loop에서 Kafka assignment/pause/resume 호출 감소
- timer commit, idle flush, DLQ, shutdown, resume 회귀 없음

### 실험 1.2: process effective-width cap sweep

process strict single-partition profile은 effective WIP가 거의 1인데도 많은 worker가 multiprocessing queue에서 대기한다는 것을 보여줬다. 이것은 먼저 control-plane 정책이 아니라 명시적 benchmark 변수로 다룬다.

구현 방향:

- benchmark 전용 `--process-count` override를 추가한다.
- partition 수와 active key 수를 바꿔가며 process count `{1, 2, 4, 8}`을 sweep한다.
- `BrokerPoller`와 `WorkManager`는 `process_count`를 모르게 유지한다. override는 engine 생성 전 benchmark/runtime config에만 적용한다.

성공 기준:

- strict `partition`에서 assigned partition이 1개면 `process_count` `1` 또는 `2`가 우세해야 한다.
- `key_hash`는 active key와 control-plane overhead가 허용하는 만큼만 scale해야 한다.
- 결과를 바탕으로 나중에 자동 advisor/policy를 둘지 판단한다.

### 실험 1.3: dedicated broker I/O bridge design

구현 전에 설계를 먼저 한다. bridge는 반복적인 `asyncio.to_thread` submit을 줄일 수 있지만, Kafka callback과 rebalance threading semantics의 ownership boundary가 되기 때문이다.

설계 제약:

- consume, commit, committed, assignment snapshot, pause, resume, close를 감싸는 `BrokerConsumerIO` 계층을 둔다.
- broker thread는 Kafka 호출을 소유할 수 있지만, `OffsetTracker`, `WorkManager`, dirty partition, message cache 같은 event-loop state는 event loop가 소유해야 한다.
- rebalance callback은 control-plane mutation을 event loop로 marshal해야 하며 broker thread에서 직접 mutate하지 않는다.
- rebalance callback 안에서 같은 bridge thread에 작업을 enqueue하고 기다리면 안 된다.
- synchronous commit의 per-partition error를 확인한다.
- DLQ producer flush는 별도 stage로 의도적으로 모델링하기 전까지 consumer bridge에 넣지 않는다.

### 실험 2: completion ingest stage

실험 1 이후 또는 profiler에서 completion transport가 지배적으로 보일 때만 진행한다. SEDA 관점에서는 engine transport와 scheduler-side release 사이에 dedicated completion ingest stage를 두는 것이다.

가설:

> dedicated completion ingest stage는 foreign transport read를 main scheduling loop에서 분리해 p99 completion turnaround를 줄인다.

제약:

- completion batch를 만들기 위한 sender-side waiting은 넣지 않는다.
- 이미 도착한 completion만 opportunistic하게 drain한다.
- blocking-timeout, poison/fail-fast 같은 local completion event를 보존한다.
- WorkManager/BrokerCompletionSupport safety boundary를 보존한다.

### 실험 3: edge-triggered scheduler

commit cadence 측정 이후 진행한다. scheduler는 모든 completion이 아니라 frontier change에 반응해야 한다.

트리거:

- ready count가 `0`에서 `>0`으로 바뀜
- idle capacity가 `0`에서 `>0`으로 바뀜
- imbalance score가 threshold를 넘음
- failsafe periodic tick

성공 기준:

- item당 schedule call 감소
- completion-to-refill latency 악화 없음
- ordering, rebalance, DLQ 회귀 없음

## py-spy 캡처 레시피

macOS에서 `py-spy`는 root 권한이 필요하다. 아래 명령은 Codex가 아니라 일반 터미널에서 실행하고, 생성된 `benchmarks/results/pyspy/codex-commit-cadence/` 아래 파일을 Codex가 분석하게 한다.

### Process partition strict-on subprocess profile

```bash
cd /Users/mqueue/Desktop/project/Pyrallel-Consumer
mkdir -p benchmarks/results/pyspy/codex-commit-cadence
sudo .venv/bin/py-spy record \
  --subprocesses \
  --format speedscope \
  --output benchmarks/results/pyspy/codex-commit-cadence/process-partition-strict.speedscope.json \
  --rate 200 \
  -- \
  .venv/bin/python -m benchmarks.run_parallel_benchmark \
    --num-messages 8000 \
    --num-keys 8000 \
    --num-partitions 1 \
    --workloads io \
    --order partition \
    --strict-completion-monitor on \
    --adaptive-concurrency off \
    --worker-io-sleep-ms 0.3 \
    --process-batch-size 1 \
    --process-max-batch-wait-ms 0 \
    --metrics-port 0 \
    --timeout-sec 180 \
    --log-level WARNING \
    --topic-prefix codex-pyspy-process-partition \
    --skip-baseline \
    --skip-async
```

### Async partition strict-on control profile

```bash
cd /Users/mqueue/Desktop/project/Pyrallel-Consumer
mkdir -p benchmarks/results/pyspy/codex-commit-cadence
sudo .venv/bin/py-spy record \
  --format speedscope \
  --output benchmarks/results/pyspy/codex-commit-cadence/async-partition-strict.speedscope.json \
  --rate 200 \
  -- \
  .venv/bin/python -m benchmarks.run_parallel_benchmark \
    --num-messages 8000 \
    --num-keys 8000 \
    --num-partitions 1 \
    --workloads io \
    --order partition \
    --strict-completion-monitor on \
    --adaptive-concurrency off \
    --worker-io-sleep-ms 0.3 \
    --process-batch-size 1 \
    --process-max-batch-wait-ms 0 \
    --metrics-port 0 \
    --timeout-sec 150 \
    --log-level WARNING \
    --topic-prefix codex-pyspy-async-partition \
    --skip-baseline \
    --skip-process
```

실행 후 runner가 출력한 benchmark JSON 경로도 함께 보존한다. Codex는 speedscope 파일과 matching JSON summary를 함께 분석해야 한다.

## 기반 문헌

- Brent, "The Parallel Evaluation of General Arithmetic Expressions", Communications of the ACM, 1974.
- Blumofe and Leiserson, "Scheduling Multithreaded Computations by Work Stealing", Journal of the ACM, 1999.
- Williams, Waterman, and Patterson, "Roofline: An Insightful Visual Performance Model for Multicore Architectures", Communications of the ACM, 2009.
- Little, "A Proof for the Queuing Formula: L = lambda W", Operations Research, 1961.
- Dean and Barroso, "The Tail at Scale", Communications of the ACM, 2013.
- Lee and Messerschmitt, "Synchronous Data Flow", Proceedings of the IEEE, 1987.
