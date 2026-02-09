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

## 4. Monitoring Dashboard (Grafana Recommended)

Assuming `get_metrics()` results are collected via Prometheus, the following panel configuration is recommended.

### 4.1. System Overview (Row)
- **Total In-Flight**:
    - Type: Stat
    - Query: `sum(pyrallel_system_in_flight)`
    - Threshold: Yellow if > 80% of `max_in_flight`, Red if > 100%
- **Consumer Status**:
    - Type: State Timeline / Status History
    - Query: `pyrallel_system_paused` (0=Running, 1=Paused)
    - Color: 0=Green, 1=Red

### 4.2. Performance (Row)
- **True Lag by Partition**:
    - Type: Time Series (Stacked)
    - Query: `pyrallel_partition_true_lag`
    - Insight: If Lag spikes for a specific partition, check for Key Skew.
- **Blocking Duration**:
    - Type: Time Series
    - Query: `max(pyrallel_partition_blocking_duration_sec)`
    - Insight: If this value keeps increasing, it's highly likely a "Poison Pill" message that never finishes processing.

### 4.3. Internal State (Row)
- **Gap Count**:
    - Type: Time Series
    - Query: `sum(pyrallel_partition_gap_count)`
    - Insight: Spikes after rebalancing are normal, but high steady-state values indicate severe `OutOfOrder` processing.
- **Queued Messages**:
    - Type: Bar Gauge
    - Query: `pyrallel_partition_queued_count`
    - Insight: Checks the backlog status of virtual partition queues.

---
© 2026 Pyrallel Consumer Project
