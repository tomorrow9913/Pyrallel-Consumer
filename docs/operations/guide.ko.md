# Pyrallel Consumer 운영 가이드

이 문서는 `Pyrallel Consumer`를 프로덕션 환경에서 운영할 때 필요한 모니터링 지표, 장애 대응, 튜닝 가이드를 제공합니다.

## 1. 핵심 모니터링 지표 (Observability)

Kafka의 기본 Lag(`LogEndOffset - CommittedOffset`)만으로는 병렬 처리 시스템의 상태를 정확히 파악할 수 없습니다. Pyrallel Consumer는 내부 상태를 투명하게 보여주는 `get_metrics()` API를 제공합니다.

### 1.1. True Lag (실제 지연)
- **정의**: `LogEndOffset` (마지막으로 가져온 메시지) - `HWM` (연속적으로 처리가 완료된 최대 오프셋)
- **의미**: 시스템 내부에 실제로 쌓여 있는 미완료 작업의 총량입니다.
- **운영 팁**: `True Lag`이 지속적으로 증가한다면 컨슈머의 처리 용량이 부족하다는 신호입니다. `max_in_flight`를 늘리거나 파티션/프로세스 수를 확장해야 합니다.

### 1.2. Gap (구멍)
- **정의**: 처리는 완료되었지만, 앞선 오프셋의 미완료로 인해 커밋되지 못한 오프셋들의 구간 수.
- **의미**: 병렬 처리의 부작용으로 발생합니다. Gap이 많다는 것은 특정 메시지(키) 처리가 지연되어 뒤따르는 메시지들의 커밋을 막고 있음을 의미합니다.
- **운영 팁**: 일시적인 Gap은 정상이지만, Gap의 수가 너무 많거나 오래 유지된다면 **Blocking Offset**을 확인해야 합니다.

### 1.3. Blocking Offset (병목 오프셋)
- **정의**: 현재 HWM의 전진을 가로막고 있는 가장 낮은 오프셋.
- **의미**: "왜 커밋이 안 되고 있지?"에 대한 직접적인 답입니다. 해당 오프셋의 처리가 끝나야만 HWM이 전진하고 커밋이 가능해집니다.
- **운영 팁**: `blocking_duration_sec` 지표를 통해 특정 메시지가 얼마나 오래 막혀있는지 감시하십시오.

### 1.4. In-Flight (처리 중)
- **정의**: 현재 시스템이 메모리에 들고 있는(처리 중 + 대기 중) 총 메시지 수.
- **의미**: 시스템 부하 상태를 나타냅니다.
- **운영 팁**: 이 값이 `max_in_flight` 설정값에 도달하면 **Backpressure**가 동작하여 Kafka 소비를 일시 중지(`Pause`)합니다.

### 1.5. Process Batch Flush Count (process 배치 flush 이유)
- **Prometheus 쿼리**: `consumer_process_batch_flush_count{reason="size|timer|close|demand"}`
- **의미**:
    - `size`: 배치 크기가 설정값에 도달해 정상적으로 묶여 전송되었습니다.
    - `timer`: 입력이 느리거나 `max_batch_wait_ms`가 먼저 도달해 작은 배치가 자주 전송되고 있습니다.
    - `demand`: 현재 flush policy가 size/timer 경로보다 먼저 누적 버퍼를 강제로 비우고 있습니다.
    - `close`: 종료나 rebalance 정리 과정에서 잔여 버퍼를 배출했습니다.
- **운영 팁**:
    - `timer` 비중이 높고 `consumer_process_batch_avg_size`가 낮으면 batching 효율이 떨어진 상태입니다. 지연 예산이 허용하면 `batch_size`를 낮추거나 `max_batch_wait_ms`를 늘리는 쪽을 검토하십시오.
    - `demand`가 계속 증가하면 latency-first 강제 flush가 많다는 뜻입니다. `flush_policy`, `demand_flush_min_residence_ms`, `process_count`, ordering skew를 함께 확인하십시오.

### 1.6. Process Batch Buffer Health (버퍼 적체 상태)
- **Prometheus 쿼리**:
    - `consumer_process_batch_avg_size`
    - `consumer_process_batch_last_size`
    - `consumer_process_batch_last_wait_seconds`
    - `consumer_process_batch_buffered_items`
    - `consumer_process_batch_buffered_age_seconds`
- **의미**:
    - `avg/last_size`는 실제 micro-batch 효율을 보여줍니다.
    - `last_wait_seconds`와 `buffered_age_seconds`는 flush 전 대기 시간을 보여줍니다.
    - `buffered_items`가 높으면 아직 worker queue로 내려가지 못한 작업이 main process 버퍼에 쌓여 있다는 뜻입니다.
- **운영 팁**:
    - `buffered_items`와 `buffered_age_seconds`가 같이 증가하면 process-mode 진입 이전(main thread batching 단계)에서 적체가 발생한 것입니다. `queue_size`만 보기보다 `consumer_in_flight_count`, `consumer_backpressure_active`, `consumer_internal_queue_depth`와 함께 해석하십시오.
    - `last_size`가 계속 1~2 수준이고 `last_wait_seconds`만 늘어나면 producer 입력이 듬성듬성하거나, 배치 정책이 현재 workload에 비해 과하게 큽니다.

### 1.7. IPC / Worker Timing Split (IPC와 워커 실행 시간 분해)
- **Prometheus 쿼리**:
    - `consumer_process_batch_avg_main_to_worker_ipc_seconds`
    - `consumer_process_batch_avg_worker_exec_seconds`
    - `consumer_process_batch_avg_worker_to_main_ipc_seconds`
    - 필요 시 `last_*` gauge를 함께 확인
- **의미**:
    - `main_to_worker`: 직렬화 + task queue 전달 비용입니다.
    - `worker_exec`: 실제 사용자 worker 코드 실행 시간입니다.
    - `worker_to_main`: completion payload를 main process가 회수하는 비용입니다.
- **운영 팁**:
    - `main_to_worker`만 높으면 payload가 크거나 pickle/IPC 비용이 병목입니다. batch payload 크기, message size, queue saturation을 점검하십시오.
    - `worker_exec`만 높으면 CPU saturation 또는 느린 사용자 로직 문제입니다. `process_count` 증설, worker 최적화, timeout/DLQ 정책 점검이 우선입니다.
    - `worker_to_main`이 높고 `buffered_items`나 `total_in_flight`도 높으면 completion drain이 밀리고 있을 가능성이 큽니다. main process 부하, completion polling cadence, 과도한 로그/metrics 갱신 빈도를 확인하십시오.

### 1.8. Engine Capability Boundary (엔진 capability 경계)
- **정의**: Control Plane은 공통 실행 엔진 계약에만 의존합니다.
- **의미**: 최소 in-flight offset 같은 Process 전용 안전 정보는 `BrokerPoller` 내부의 구체 클래스 분기 대신, 선택적 엔진 capability로 노출되어야 합니다.
- **운영 팁**: 리팩터링 검증 시 async/process 엔진(또는 mock) 모두에 동일한 control-plane 검증을 적용해 polymorphic 경계가 유지되는지 확인하십시오.

## 2. 튜닝 가이드

### 2.1. `max_in_flight_messages` (Control Plane)
- **설명**: 시스템 전체가 동시에 처리할 수 있는 최대 메시지 수입니다.
- **튜닝**:
    - **너무 낮음**: 병렬 처리 효율이 떨어지고 컨슈머가 놉니다(Starvation).
    - **너무 높음**: 메모리 사용량이 증가하고, 리밸런싱 시 재처리 비용이 커집니다.
    - **권장**: (Worker 수 * 2) ~ (Worker 수 * 10) 정도로 설정하여 항상 워커가 일할 거리를 확보하십시오.

### 2.2. `process_count` (Process Engine)
- **설명**: 병렬 처리를 수행할 워커 프로세스의 개수입니다.
- **튜닝**:
    - **CPU-bound 작업**: CPU 코어 수와 비슷하게 설정 (`os.cpu_count()`).
    - **I/O-bound 작업**: CPU 코어 수보다 높게 설정 가능하지만, `AsyncExecutionEngine` 사용을 고려하십시오.

## 3. 장애 대응

### 3.1. 컨슈머가 멈춘 것처럼 보일 때
1. **Metrics 확인**: `get_metrics()`를 호출하여 `is_paused` 상태인지 확인합니다.
2. **Backpressure**: `is_paused=True`라면 `total_in_flight`가 줄어들 때까지 기다려야 합니다. 워커가 막혀있지 않은지 확인하십시오.
3. **Blocking Offset**: `blocking_duration_sec`이 비정상적으로 높다면, 해당 오프셋의 메시지 처리가 무한 루프에 빠졌거나 데드락 상태일 수 있습니다.

### 3.2. 리밸런싱이 너무 잦을 때
- `max_poll_interval_ms`를 늘리십시오. 병렬 처리로 인해 개별 메시지 처리가 늦어지면 Kafka 브로커가 컨슈머를 죽은 것으로 오해할 수 있습니다.
- `max_revoke_grace_ms` 설정을 통해 리밸런싱 시 정리 시간을 확보하십시오.

### 3.3. Process-mode에서 처리량은 낮고 lag만 늘어날 때
1. `consumer_process_batch_flush_count{reason="timer"}`와 `consumer_process_batch_avg_size`를 같이 보십시오.
2. `timer` flush가 지배적이고 평균 배치가 작으면 batching 비효율입니다. latency budget 안에서 `batch_size`를 낮추거나 `max_batch_wait_ms`를 늘리십시오.
3. 배치 크기는 충분한데 `consumer_process_batch_avg_main_to_worker_ipc_seconds`가 높으면 payload/IPC 비용이 병목입니다. message size, serialization 비용, `queue_size` 포화를 먼저 확인하십시오.
4. IPC는 정상인데 `consumer_process_batch_avg_worker_exec_seconds`만 높으면 worker 로직이 병목입니다. CPU saturation, 외부 I/O, timeout/DLQ를 점검하십시오.

### 3.4. Process-mode에서 queue/backpressure가 반복될 때
1. `consumer_backpressure_active`, `consumer_in_flight_count`, `consumer_process_batch_buffered_items`를 같이 보십시오.
2. `buffered_items`와 `consumer_internal_queue_depth`가 동시에 높으면 main buffer와 partition queue가 함께 밀리는 상태입니다. `max_in_flight_messages`, `queue_size`, ordering skew를 점검하십시오.
3. `buffered_items`는 낮은데 `worker_to_main_ipc_seconds`만 높으면 completion 회수가 병목일 수 있습니다. main process 부하와 polling cadence를 점검하십시오.

## 4. 모니터링 대시보드 (Grafana 권장)

`get_metrics()` 결과를 Prometheus 등으로 수집한다고 가정할 때, 다음과 같은 패널 구성을 권장합니다.

### 4.1. System Overview (Row)
- **Total In-Flight**:
    - Type: Stat
    - Query: `consumer_in_flight_count`
    - Threshold: `max_in_flight`의 80% 이상 시 Yellow, 100% 이상 시 Red
- **Consumer Status**:
    - Type: State Timeline / Status History
    - Query: `consumer_backpressure_active` (0=Running, 1=Paused)
    - Color: 0=Green, 1=Red

### 4.2. Performance (Row)
- **True Lag by Partition**:
    - Type: Time Series (Stacked)
    - Query: `consumer_parallel_lag`
    - Insight: 특정 파티션만 Lag가 튄다면 해당 파티션의 Key 분포(Skew)를 확인하십시오.
- **Blocking Duration**:
    - Type: Time Series
    - Query: `max(consumer_oldest_task_duration_seconds)`
    - Insight: 이 값이 계속 증가한다면 처리가 영원히 끝나지 않는 "독 메시지(Poison Pill)"일 가능성이 높습니다.

### 4.3. Internal State (Row)
- **Gap Count**:
    - Type: Time Series
    - Query: `sum(consumer_gap_count)`
    - Insight: 리밸런싱 직후 증가하는 것은 정상이나, 평상시에도 높다면 `OutOfOrder` 처리가 심한 상태입니다.
- **Queued Messages**:
    - Type: Bar Gauge
    - Query: `consumer_internal_queue_depth`
    - Insight: 가상 파티션 큐의 백로그 상태를 확인합니다.

### 4.4. Process Mode Health (Row)
- **Flush Reason Mix**:
    - Type: Time Series
    - Query: `consumer_process_batch_flush_count`
    - Insight: steady-state에서 `size`가 주도하고 `timer`/`demand`는 보조적으로 나타나는 편이 일반적입니다. `timer` 편중은 작은 배치, `demand` 편중은 잦은 강제 배출 신호입니다.
- **Batch Efficiency**:
    - Type: Time Series
    - Query: `consumer_process_batch_avg_size`, `consumer_process_batch_last_size`, `consumer_process_batch_buffered_age_seconds`
    - Insight: 평균 배치 크기 하락과 버퍼 age 상승이 동시에 보이면 batching 정책이 workload와 맞지 않는 경우가 많습니다.
- **IPC vs Worker Time Split**:
    - Type: Time Series
    - Query: `consumer_process_batch_avg_main_to_worker_ipc_seconds`, `consumer_process_batch_avg_worker_exec_seconds`, `consumer_process_batch_avg_worker_to_main_ipc_seconds`
    - Insight: 세 값을 분리해서 보면 병목이 serialization/IPC인지, 실제 worker 실행인지, completion 회수인지 빠르게 구분할 수 있습니다.

---
© 2026 Pyrallel Consumer Project
