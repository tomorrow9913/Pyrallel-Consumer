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

### 1.6. Process Worker-Pipe Route Batches
- **Prometheus queries**:
    - `consumer_process_route_batch_count`
    - `consumer_process_route_batch_items`
    - `consumer_process_route_batch_avg_size`
    - `consumer_process_route_batch_max_size`
    - `consumer_process_ipc_items_per_input_payload`
    - `consumer_process_ipc_items_per_completion_payload`
- **Meaning**: The live process topology uses worker-pipes. In this topology, worker-pipes bypasses BatchAccumulator flush counts; route-batch and IPC payload metrics are the primary process-mode batching signals.
- **Tip**:
    - Use route-batch count/items and IPC items-per-payload first when diagnosing process throughput or payload efficiency.
    - Treat zero BatchAccumulator flush/sizing values as expected under the worker-pipes-only path unless the support boundary says a different transport is active.

### 1.6b. Legacy Process Batch Flush Count
- **Prometheus query**: `consumer_process_batch_flush_count{reason=~"size|timer|close|demand"}`
- **Meaning**:
    - `size`: batches are reaching the configured batch size and flushing efficiently.
    - `timer`: input is sparse or `max_batch_wait_ms` is expiring before the batch fills.
    - `demand`: the active flush policy is force-flushing buffered work before the normal size/timer path.
    - `close`: buffered work was flushed during shutdown or rebalance cleanup.
- **Tip**:
    - Consult these only as v1 compatibility signals for the old BatchAccumulator path. Worker-pipes users should prioritize the route-batch and IPC payload metrics above.
    - If `timer` dominates and `consumer_process_batch_avg_size` stays low on a BatchAccumulator-capable path, batching efficiency is poor. Reduce `batch_size` or increase `max_batch_wait_ms` only if the latency budget allows it.
    - If `demand` keeps growing on a BatchAccumulator-capable path, the workload is spending more time on latency-first forced flushes than on full batches. Revisit `flush_policy`, `demand_flush_min_residence_ms`, `process_count`, and ordering skew together.

### 1.6a. Commit and DLQ Failure Counters
- **Prometheus queries**:
    - `consumer_commit_failures_total{reason="kafka_exception"}`
    - `consumer_dlq_publish_failures_total`
- **Meaning**: These counters identify release-critical failures that can otherwise appear only as lag/gap symptoms. Commit failures indicate replay-risk at the broker commit boundary; DLQ publish failures mean a terminal failed message could not be published and the offset remains pending retry.
- **Tip**: Alert on any increase. For commit failures, check Kafka coordinator health, ACLs, and broker connectivity. For DLQ publish failures, verify DLQ topic existence, producer ACLs, payload size limits, and broker availability before restarting or scaling consumers.

### 1.7. Legacy Process Batch Buffer Health
- **Prometheus queries**:
    - `consumer_process_batch_avg_size`
    - `consumer_process_batch_last_size`
    - `consumer_process_batch_last_wait_seconds`
    - `consumer_process_batch_buffered_items`
    - `consumer_process_batch_buffered_age_seconds`
- **Meaning**:
    - These gauges describe the legacy BatchAccumulator buffer surface. In worker-pipes-only process mode they can remain zero while route-batch/IPC metrics show real work.
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

### 1.9. Process Batch Support Boundary
- **Prometheus queries**:
    - `consumer_process_batch_transport_mode`
    - `consumer_process_batch_support_state`
    - `consumer_process_batch_timer_flush_supported`
    - `consumer_process_batch_demand_flush_supported`
    - `consumer_process_batch_recycle_supported`
- **Meaning**: These gauges describe which process execution diagnostics are active and which process-batch control paths the current transport supports. `transport_mode` is currently bounded to `worker_pipes`; `support_state` is bounded to `full` or `bounded`.
- **Tip**: Treat these as compatibility and support-boundary signals, not throughput counters. Use them before interpreting zero-valued timer/demand/recycle behavior as a runtime fault.

### 1.10. Engine Capability and Pipeline Diagnostics Boundary
- **Definition**: The control plane only depends on the shared execution-engine contract.
- **Meaning**: Commit clamping is computed from the control-plane `WorkManager` dispatch ledger. This commit clamping rule belongs to the control plane, while process-private registries remain recovery/diagnostics state rather than a required engine capability.
- **Tip**: Keep `process_batch_metrics` documented as a v1 compatibility projection and keep `get_pipeline_diagnostics()` as the separate supported sidecar for broker-neutral pipeline observability. `RuntimeSnapshot` v1 remains unchanged. When validating refactors, run the same control-plane checks against async and process engines (or mocks) to confirm the boundary stays polymorphic.

### 1.11. Shutdown Drain Diagnostics
- **Log lines**:
    - `ProcessExecutionEngine shutdown pre-join drain: registry_events=... completion_events=... residual_in_flight_registry=...`
    - `ProcessExecutionEngine shutdown post-join drain: registry_events=... completion_events=... passes=... residual_in_flight_registry=...`
    - `Residual in-flight registry after shutdown drain: ...`
- **Meaning**: These entries describe visible IPC reconciliation during shutdown and remaining process-private diagnostic state. They are not Prometheus counters, a retry ledger, a DLQ trigger, or commit-safety evidence.
- **Tip**: Treat non-zero `completion_events` as evidence that already-visible real completions were moved into the normal prefetched completion path. Treat `passes` as a bounded stable-empty observation only; it is not proof that no hidden worker outcome can still exist outside the shutdown boundary. Commit advancement, DLQ publish, and epoch fencing remain control-plane decisions driven by normal completion handling.

### 1.12. Adaptive Backpressure / Adaptive Concurrency Runtime Snapshots
- **Prometheus queries**:
    - `consumer_adaptive_backpressure_configured_max_in_flight`
    - `consumer_adaptive_backpressure_effective_max_in_flight`
    - `consumer_adaptive_backpressure_min_in_flight`
    - `consumer_adaptive_backpressure_scale_up_step`
    - `consumer_adaptive_backpressure_scale_down_step`
    - `consumer_adaptive_backpressure_cooldown_ms`
    - `consumer_adaptive_backpressure_lag_scale_up_threshold`
    - `consumer_adaptive_backpressure_low_latency_threshold_ms`
    - `consumer_adaptive_backpressure_high_latency_threshold_ms`
    - `consumer_adaptive_backpressure_avg_completion_latency_seconds`
    - `consumer_adaptive_backpressure_last_decision`
    - `consumer_adaptive_concurrency_configured_max_in_flight`
    - `consumer_adaptive_concurrency_effective_max_in_flight`
    - `consumer_adaptive_concurrency_min_in_flight`
    - `consumer_adaptive_concurrency_scale_up_step`
    - `consumer_adaptive_concurrency_scale_down_step`
    - `consumer_adaptive_concurrency_cooldown_ms`
- **Meaning**: These gauges expose both current runtime decisions and configured control limits for adaptive backpressure/adaptive concurrency. They are especially useful when tuning `max_in_flight_messages` and diagnosing oscillation.
- **Tip**: Pair `*_effective_*` with `consumer_backpressure_active` and `consumer_in_flight_count`; if `*_effective_*` is pinned low while in-flight is saturated, reduce aggressive concurrency changes and check batch/processing latency behavior.

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
1. Inspect `consumer_process_route_batch_count`, `consumer_process_route_batch_items`, `consumer_process_ipc_items_per_input_payload`, and `consumer_process_ipc_items_per_completion_payload` first. Worker-pipes bypasses BatchAccumulator flush counts, so legacy flush/sizing gauges can stay zero while work is flowing.
2. If route batches are frequent but `consumer_process_ipc_items_per_input_payload` is low, payload efficiency is poor. Revisit route batch size, ordering skew, and message size.
3. If payload efficiency looks healthy but `consumer_process_batch_avg_main_to_worker_ipc_seconds` is high, the bottleneck is payload serialization or IPC pressure. Check message size, serialization cost, and `queue_size`.
4. If IPC looks normal but `consumer_process_batch_avg_worker_exec_seconds` is high, the worker logic is the bottleneck. Check CPU saturation, downstream I/O, and timeout/DLQ behavior.

### 3.4. Repeating queue/backpressure oscillation in process mode
1. Inspect `consumer_backpressure_active`, `consumer_in_flight_count`, `consumer_process_route_batch_items`, and the IPC items-per-payload gauges together.
2. If route-batch items and `consumer_internal_queue_depth` are both high, worker-pipe routing and partition queues are backing up together. Revisit `max_in_flight_messages`, `queue_size`, and ordering skew.
3. If `buffered_items` stays low but `consumer_process_batch_avg_worker_to_main_ipc_seconds` or `consumer_process_batch_last_worker_to_main_ipc_seconds` is high, completion draining may be the bottleneck. Check main-process load and completion polling cadence.

## 4. Monitoring Dashboard (Grafana Recommended)

Assuming `get_metrics()` results are collected via Prometheus, the following panel configuration is recommended.
The checked-in Grafana dashboard is a reference/sample dashboard for exploring the public metric surface and composing panels; it is not a production opinionated dashboard or alert policy.
The dashboard is intentionally two-layered: Operator triage is a curated subset for first-screen health/risk/bottleneck checks, while Metric catalog/reference covers the public metric surface for detailed panel composition.

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
- **Worker-Pipe Route Batches**:
    - Type: Time Series
    - Query: `consumer_process_route_batch_count`, `consumer_process_route_batch_items`
    - Insight: Worker-pipes bypasses BatchAccumulator flush counts; these route-batch counters are the primary process throughput view.
- **Worker-Pipe Payload Efficiency**:
    - Type: Time Series
    - Query: `consumer_process_ipc_items_per_input_payload`, `consumer_process_ipc_items_per_completion_payload`
    - Insight: Low items per IPC payload means route batches are not amortizing serialization and queue transfer costs.
- **IPC vs Worker Time Split**:
    - Type: Time Series
    - Query: `consumer_process_batch_avg_main_to_worker_ipc_seconds`, `consumer_process_batch_avg_worker_exec_seconds`, `consumer_process_batch_avg_worker_to_main_ipc_seconds`
    - Insight: Split these three values to quickly decide whether the bottleneck is serialization/IPC, worker execution, or completion draining.
- **Support Boundary**:
    - Type: Time Series
    - Query: `consumer_process_batch_transport_mode`, `consumer_process_batch_support_state`, `consumer_process_batch_timer_flush_supported`, `consumer_process_batch_demand_flush_supported`, `consumer_process_batch_recycle_supported`
    - Insight: Confirm whether the active process transport supports the timer, demand, and recycle paths before treating missing activity as an operational failure.

### 4.5. Adaptive Control Runtime (Row)
- **Adaptive Backpressure Limits**:
    - Type: Stat
    - Query: `consumer_adaptive_backpressure_configured_max_in_flight`, `consumer_adaptive_backpressure_effective_max_in_flight`
- **Adaptive Concurrency Limits**:
    - Type: Stat
    - Query: `consumer_adaptive_concurrency_configured_max_in_flight`, `consumer_adaptive_concurrency_effective_max_in_flight`
- **Adaptive Decision Input**:
    - Type: Time Series
    - Query: `consumer_adaptive_backpressure_avg_completion_latency_seconds`, `consumer_adaptive_backpressure_last_decision`
- **Tuning Reference**:
    - Type: Table
    - Query: `consumer_adaptive_backpressure_scale_up_step`, `consumer_adaptive_backpressure_scale_down_step`, `consumer_adaptive_backpressure_cooldown_ms`, `consumer_adaptive_concurrency_scale_up_step`, `consumer_adaptive_concurrency_scale_down_step`, `consumer_adaptive_concurrency_cooldown_ms`

### 4.6. Pipeline Diagnostics Surface (Row)
- **Surface**: `PyrallelConsumer.get_pipeline_diagnostics()` and `BrokerPoller.get_pipeline_diagnostics()` provide the supported sidecar that backs the `pyrallel_pipeline_*` Prometheus projection. Unsupported sections are visible through `pyrallel_pipeline_section_support_state`; do not treat absent observed gauges as zero work.
- **Pipeline Stages**:
    - Type: Time Series
    - Query: `pyrallel_pipeline_stage_messages`
- **Pipeline Blocked Reasons**:
    - Type: Time Series
    - Query: `pyrallel_pipeline_blocked_messages`, `pyrallel_pipeline_dispatch_capacity_blocked_messages`
- **Pipeline Settlement Blocker State**:
    - Type: State Timeline / Status History
    - Query: `pyrallel_pipeline_settlement_blocker_state`
    - Insight: One-hot bounded current settlement blocker reason (`commit_pending`, `dlq_publish_pending`, `ordered_cursor_gap`, `ack_pending`, `delete_pending`, `archive_pending`, or `unknown`). Supported healthy settlement emits all reasons as `0`; unsupported settlement is represented by `pyrallel_pipeline_section_support_state`, not fake observed values.
- **Pipeline Support and Worker Capacity**:
    - Type: Time Series
    - Query: `pyrallel_pipeline_section_support_state`, `pyrallel_pipeline_worker_capacity_units`
- **Pipeline Subqueues, Polling, and Commit Settlement**:
    - Type: Time Series
    - Query: `pyrallel_pipeline_subqueue_items`, `pyrallel_pipeline_subqueues`, `pyrallel_pipeline_poll_records_total`, `pyrallel_pipeline_poll_events_total`, `pyrallel_pipeline_completed_offset_skips_total`, `pyrallel_pipeline_completion_to_commit_latency_seconds_bucket`
    - Insight: Completion-to-commit latency is a broker-owned pipeline event metric emitted alongside the sidecar projection, not a field returned by `get_pipeline_diagnostics()`. `pyrallel_pipeline_completed_offset_skips_total` is projected delta-safely from `PipelinePollDiagnostics.completed_offset_skips_total` as a record-level restored-offset skip count, not a poll-call event.

---
© 2026 Pyrallel Consumer Project
