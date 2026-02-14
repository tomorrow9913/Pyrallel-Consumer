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

## 4. 모니터링 대시보드 (Grafana 권장)

`get_metrics()` 결과를 Prometheus 등으로 수집한다고 가정할 때, 다음과 같은 패널 구성을 권장합니다.

### 4.1. System Overview (Row)
- **Total In-Flight**:
    - Type: Stat
    - Query: `sum(pyrallel_system_in_flight)`
    - Threshold: `max_in_flight`의 80% 이상 시 Yellow, 100% 이상 시 Red
- **Consumer Status**:
    - Type: State Timeline / Status History
    - Query: `pyrallel_system_paused` (0=Running, 1=Paused)
    - Color: 0=Green, 1=Red

### 4.2. Performance (Row)
- **True Lag by Partition**:
    - Type: Time Series (Stacked)
    - Query: `pyrallel_partition_true_lag`
    - Insight: 특정 파티션만 Lag가 튄다면 해당 파티션의 Key 분포(Skew)를 확인하십시오.
- **Blocking Duration**:
    - Type: Time Series
    - Query: `max(pyrallel_partition_blocking_duration_sec)`
    - Insight: 이 값이 계속 증가한다면 처리가 영원히 끝나지 않는 "독 메시지(Poison Pill)"일 가능성이 높습니다.

### 4.3. Internal State (Row)
- **Gap Count**:
    - Type: Time Series
    - Query: `sum(pyrallel_partition_gap_count)`
    - Insight: 리밸런싱 직후 증가하는 것은 정상이나, 평상시에도 높다면 `OutOfOrder` 처리가 심한 상태입니다.
- **Queued Messages**:
    - Type: Bar Gauge
    - Query: `pyrallel_partition_queued_count`
    - Insight: 가상 파티션 큐의 백로그 상태를 확인합니다.

---
© 2026 Pyrallel Consumer Project
