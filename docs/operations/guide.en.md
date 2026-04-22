# Pyrallel Consumer Operations Guide

This document provides monitoring metrics, troubleshooting tips, and tuning guides for operating `Pyrallel Consumer` in a production environment.

## 1. Core Monitoring Metrics (Observability)

Kafka's default Lag (`LogEndOffset - CommittedOffset`) alone cannot accurately represent the state of a parallel processing system. Pyrallel Consumer provides the `get_metrics()` API to transparently show internal state.

### 1.1. True Lag
- **Definition**: `LogEndOffset` (Last fetched message) - `HWM` (Highest Contiguous Completed Offset)
- **Meaning**: The total amount of incomplete work actually piled up inside the system.
- **Tip**: If `True Lag` keeps increasing, it's a sign that the consumer's processing capacity is insufficient. Increase `max_in_flight` or scale out partitions/processes.

### 1.2. Gap
- **Definition**: Ranges of offsets that are completed but cannot be committed because preceding offsets are incomplete.
- **Meaning**: A side effect of parallel processing. A high Gap count means processing of specific messages (Keys) is delayed, blocking the commit of subsequent messages.
- **Tip**: Temporary gaps are normal, but if the Gap count remains high for too long, check the **Blocking Offset**.

### 1.3. Blocking Offset
- **Definition**: The lowest offset currently preventing the HWM from advancing.
- **Meaning**: The direct answer to "Why isn't the commit progressing?". Processing of this specific offset must complete for the HWM to advance and commit.
- **Tip**: Monitor the `blocking_duration_sec` metric to detect if specific messages are stuck for too long.

### 1.4. In-Flight
- **Definition**: The total number of messages currently held in memory (Processing + Queued).
- **Meaning**: Represents the current system load.
- **Tip**: When this value reaches the `max_in_flight` setting, **Backpressure** activates, and Kafka consumption is `Paused`.

### 1.5. Resource Signals
- **Prometheus queries**:
    - `consumer_resource_signal_status{status="available"}`
    - `consumer_resource_signal_status{status="unavailable"}`
    - `consumer_resource_signal_status{status="stale"}`
    - `consumer_resource_signal_status{status="first_sample_pending"}`
    - `consumer_resource_cpu_utilization_ratio`
    - `consumer_resource_memory_utilization_ratio`
- **Meaning**: Resource signal gauges are advisory inputs for tuning experiments. The status gauge uses fixed labels only; no dynamic `provider` label is exported.
- **Tip**: Treat `unavailable`, `stale`, and `first_sample_pending` as fail-open states. They should explain why resource-aware tuning is inactive, not force a lower concurrency limit.

### 1.6. Process Batch Flush Count
- **Prometheus query**: `consumer_process_batch_flush_count{reason=~"size|timer|close|demand"}`
- **Meaning**:
    - `size`: batches are reaching the configured batch size and flushing efficiently.
    - `timer`: input is sparse or `max_batch_wait_ms` is expiring before the batch fills.
    - `demand`: the active flush policy is force-flushing buffered work before the normal size/timer path.
    - `close`: buffered work was flushed during shutdown or rebalance cleanup.
- **Tip**:
    - If `timer` dominates and `consumer_process_batch_avg_size` stays low, batching efficiency is poor. Reduce `batch_size` or increase `max_batch_wait_ms` only if the latency budget allows it.
    - If `demand` keeps growing, the workload is spending more time on latency-first forced flushes than on full batches. Revisit `flush_policy`, `demand_flush_min_residence_ms`, `process_count`, and ordering skew together.

### 1.6a. Commit and DLQ Failure Counters
- **Prometheus queries**:
    - `consumer_commit_failures_total{reason="kafka_exception"}`
    - `consumer_dlq_publish_failures_total`
- **Meaning**: These counters identify release-critical failures that can otherwise appear only as lag/gap symptoms. Commit failures indicate replay-risk at the broker commit boundary; DLQ publish failures mean a terminal failed message could not be published and the offset remains pending retry.
- **Tip**: Alert on any increase. For commit failures, check Kafka coordinator health, ACLs, and broker connectivity. For DLQ publish failures, verify DLQ topic existence, producer ACLs, payload size limits, and broker availability before restarting or scaling consumers.

### 1.7. Process Batch Buffer Health
- **Prometheus queries**:
    - `consumer_process_batch_avg_size`
    - `consumer_process_batch_last_size`
    - `consumer_process_batch_last_wait_seconds`
    - `consumer_process_batch_buffered_items`
    - `consumer_process_batch_buffered_age_seconds`
- **Meaning**:
    - `avg/last_size` show real micro-batch efficiency.
    - `last_wait_seconds` and `buffered_age_seconds` show how long work sat before flush.
    - `buffered_items` means work is still accumulating in the main-process batch buffer and has not reached worker queues yet.
- **Tip**:
    - If `buffered_items` and `buffered_age_seconds` rise together, the bottleneck is before worker execution, in the batching handoff path. Interpret them together with `consumer_in_flight_count`, `consumer_backpressure_active`, and `consumer_internal_queue_depth`.
    - If `last_size` stays around 1-2 while `last_wait_seconds` keeps climbing, the producer rate is sparse or the batch policy is oversized for this workload.

### 1.8. IPC / Worker Timing Split
- **Prometheus queries**:
    - `consumer_process_batch_last_main_to_worker_ipc_seconds`
    - `consumer_process_batch_avg_main_to_worker_ipc_seconds`
    - `consumer_process_batch_last_worker_exec_seconds`
    - `consumer_process_batch_avg_worker_exec_seconds`
    - `consumer_process_batch_last_worker_to_main_ipc_seconds`
    - `consumer_process_batch_avg_worker_to_main_ipc_seconds`
- **Meaning**:
    - `main_to_worker`: serialization plus task-queue transfer cost.
    - `worker_exec`: actual user-worker execution time.
    - `worker_to_main`: completion transfer cost back into the main process.
- **Tip**:
    - High `main_to_worker` alone points to payload size, pickle cost, or queue pressure.
    - High `worker_exec` alone points to CPU saturation or slow handler logic; tune `process_count`, optimize the worker, or tighten timeout/DLQ policy.
    - High `worker_to_main` with rising `buffered_items` or `consumer_in_flight_count` suggests completion drain is lagging. Check main-process load, completion polling cadence, and overly chatty logging/metrics loops.

### 1.9. Engine Capability Boundary
- **Definition**: The control plane only depends on the shared execution-engine contract.
- **Meaning**: Process-only safety data such as minimum in-flight offsets should be exposed as an optional engine capability, not by branching on a concrete engine class inside `BrokerPoller`.
- **Tip**: When validating refactors, run the same control-plane checks against async and process engines (or mocks) to confirm the boundary stays polymorphic.

## 2. Tuning Guide

### 2.1. `max_in_flight_messages` (Control Plane)
- **Description**: The maximum number of messages the entire system can process concurrently.
- **Tuning**:
    - **Too Low**: Parallel processing efficiency drops, and consumers sit idle (Starvation).
    - **Too High**: Memory usage increases, and reprocessing costs during rebalancing become high.
    - **Recommendation**: Set it to roughly (Worker Count * 2) ~ (Worker Count * 10) to ensure workers always have tasks.

### 2.2. `process_count` (Process Engine)
- **Description**: The number of worker processes to perform parallel processing.
- **Tuning**:
    - **CPU-bound**: Set close to the number of CPU cores (`os.cpu_count()`).
    - **I/O-bound**: Can be set higher than CPU cores, but consider using `AsyncExecutionEngine` instead.

## 3. Troubleshooting

### 3.1. Consumer appears stuck
1. **Check Metrics**: Call `get_metrics()` to check the `is_paused` state.
2. **Backpressure**: If `is_paused=True`, wait until `total_in_flight` decreases. Check if workers are blocked.
3. **Blocking Offset**: If `blocking_duration_sec` is abnormally high, the message processing for that offset might be in an infinite loop or deadlock.

### 3.2. Frequent Rebalancing
- Increase `max_poll_interval_ms`. With parallel processing, individual message processing might be delayed, causing the Kafka broker to assume the consumer is dead.
- Use `max_revoke_grace_ms` to ensure cleanup time during rebalancing.

### 3.3. Low throughput with growing lag in process mode
1. Inspect `consumer_process_batch_flush_count{reason="timer"}` together with `consumer_process_batch_avg_size`.
2. If timer-driven flushes dominate and average batch size is small, batching efficiency is poor. Reduce `batch_size` or increase `max_batch_wait_ms` within your latency budget.
3. If batch size looks healthy but `consumer_process_batch_avg_main_to_worker_ipc_seconds` is high, the bottleneck is payload serialization or IPC pressure. Check message size, serialization cost, and `queue_size`.
4. If IPC looks normal but `consumer_process_batch_avg_worker_exec_seconds` is high, the worker logic is the bottleneck. Check CPU saturation, downstream I/O, and timeout/DLQ behavior.

### 3.4. Repeating queue/backpressure oscillation in process mode
1. Inspect `consumer_backpressure_active`, `consumer_in_flight_count`, and `consumer_process_batch_buffered_items` together.
2. If `buffered_items` and `consumer_internal_queue_depth` are both high, the main batch buffer and partition queues are backing up together. Revisit `max_in_flight_messages`, `queue_size`, and ordering skew.
3. If `buffered_items` stays low but `consumer_process_batch_avg_worker_to_main_ipc_seconds` or `consumer_process_batch_last_worker_to_main_ipc_seconds` is high, completion draining may be the bottleneck. Check main-process load and completion polling cadence.

## 4. Monitoring Dashboard (Grafana Recommended)

Assuming `get_metrics()` results are collected via Prometheus, the following panel configuration is recommended.

### 4.1. System Overview (Row)
- **Total In-Flight**:
    - Type: Stat
    - Query: `consumer_in_flight_count`
    - Threshold: Yellow if > 80% of `max_in_flight`, Red if > 100%
- **Consumer Status**:
    - Type: State Timeline / Status History
    - Query: `consumer_backpressure_active` (0=Running, 1=Paused)
    - Color: 0=Green, 1=Red

### 4.2. Performance (Row)
- **True Lag by Partition**:
    - Type: Time Series (Stacked)
    - Query: `consumer_parallel_lag`
    - Insight: If Lag spikes for a specific partition, check for Key Skew.
- **Blocking Duration**:
    - Type: Time Series
    - Query: `max(consumer_oldest_task_duration_seconds)`
    - Insight: If this value keeps increasing, it's highly likely a "Poison Pill" message that never finishes processing.

### 4.3. Internal State (Row)
- **Gap Count**:
    - Type: Time Series
    - Query: `sum(consumer_gap_count)`
    - Insight: Spikes after rebalancing are normal, but high steady-state values indicate severe `OutOfOrder` processing.
- **Queued Messages**:
    - Type: Bar Gauge
    - Query: `consumer_internal_queue_depth`
    - Insight: Checks the backlog status of virtual partition queues.

### 4.4. Process Mode Health (Row)
- **Flush Reason Mix**:
    - Type: Time Series
    - Query: `consumer_process_batch_flush_count`
    - Insight: In steady state, `size` should usually dominate while `timer` and `demand` remain secondary. A `timer`-heavy mix means small batches; a `demand`-heavy mix means frequent forced flushes.
- **Batch Efficiency**:
    - Type: Time Series
    - Query: `consumer_process_batch_avg_size`, `consumer_process_batch_last_size`, `consumer_process_batch_buffered_age_seconds`
    - Insight: Falling batch size plus rising buffered age usually means the current batching policy does not match the workload.
- **IPC vs Worker Time Split**:
    - Type: Time Series
    - Query: `consumer_process_batch_avg_main_to_worker_ipc_seconds`, `consumer_process_batch_avg_worker_exec_seconds`, `consumer_process_batch_avg_worker_to_main_ipc_seconds`
    - Insight: Split these three values to quickly decide whether the bottleneck is serialization/IPC, worker execution, or completion draining.

---
© 2026 Pyrallel Consumer Project
