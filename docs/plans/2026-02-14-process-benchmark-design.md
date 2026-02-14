# Process Engine Benchmark & Observability Design (2026-02-14)

## Goals
1. Fix process-execution benchmark hangs by measuring per-message latency in the parent process instead of pushing metrics from child workers.
2. Extend the production Pyrallel-Consumer runtime with Prometheus-friendly observability covering throughput, lag, gaps, queue depth, blocking offsets, and metadata size.
3. Keep existing `SystemMetrics` / `PartitionMetrics` DTOs as the single source of truth while adding cumulative counters and latency telemetry.

## Scope
- Benchmark harness (`benchmarks/run_parallel_benchmark.py`) + `pyrallel_consumer_test.py`
- Core runtime: WorkManager, BrokerPoller, OffsetTracker, ExecutionEngines, MetadataEncoder, consumer facade
- New Prometheus exporter module and configuration wiring

## Architecture Overview

### 1. Parent-Side Latency Measurement
- When WorkManager submits a `WorkItem`, the benchmark harness (and optionally control plane) will attach a dispatch timestamp (e.g., storing in a side dict keyed by `(tp, offset)` or inside `WorkItem.metadata`).
- When a completion event is processed in the parent process (Control Plane), compute `duration = now - dispatch_ts`.
- Feed durations into both:
  * Benchmark harness `BenchmarkStats` (for JSON/table outputs)
  * Prometheus latency histogram `consumer_processing_latency_seconds`
- No more metrics queues from worker processes → eliminates process-engine hangs.

### 2. Prometheus Exporter Layer
- Add dependency: `prometheus-client>=0.20.0` (pyproject / requirements).
- New module `pyrallel_consumer/metrics_exporter.py`:
  ```python
  class PrometheusMetricsExporter:
      def __init__(..., port: int):
          start_http_server(port)
          # define counters/gauges/histograms
      def update_from_system_metrics(self, metrics: SystemMetrics):
          ...  # set gauge values
      def observe_completion(self, tp, status, duration):
          processed_total.labels(...).inc()
          processing_latency.observe(duration)
      def update_metadata_size(self, topic, size_bytes):
          metadata_size_gauge.labels(topic=topic).set(size_bytes)
  ```
- `consumer.py` owns exporter lifecycle: if `metrics.enabled`, instantiate exporter, pass to BrokerPoller/WorkManager hooks.
- Minimum configuration: `METRICS_ENABLED`, `METRICS_PORT` (default 9095) added to `KafkaConfig` or new `MetricsConfig`.

### 3. Metrics & Counters
| Metric | Type | Labels | Source |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` (`success`/`failure`) | WorkManager completion handler |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | WorkManager completion handler (parent timestamp diff) |
| `consumer_in_flight_count` | Gauge | `topic` | `SystemMetrics.total_in_flight` |
| `consumer_internal_queue_depth` | Gauge | `topic`, `virtual_partition` | `WorkManager.get_virtual_queue_sizes()` |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | `PartitionMetrics.true_lag` |
| `consumer_gap_count` | Gauge | `topic`, `partition` | `PartitionMetrics.gap_count` |
| `consumer_backpressure_active` | Gauge (0/1) | `topic` | `SystemMetrics.is_paused` |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | `PartitionMetrics.blocking_duration_sec` |
| `consumer_metadata_size_bytes` | Gauge | `topic` | `MetadataEncoder` when producing commit payload |

### 4. Control Plane Integration
- **WorkManager**:
  - Maintain a `Dict[Tuple[TopicPartition, int], float]` dispatch timestamp cache when submitting to `ExecutionEngine`.
  - On completion (success/failure), pop timestamp, compute duration, call exporter hooks to inc counters + observe histogram.
  - Optional: purge stale entries if rebalances drop completions (match epoch).
- **BrokerPoller**:
  - After each tick (or on-demand via `get_metrics()`), call `exporter.update_from_system_metrics(system_metrics)`.
- **MetadataEncoder**:
  - When generating commit metadata bytes, report size via exporter (labels by topic).
- **ExecutionEngines**: no change beyond ensuring completion events fire for all tasks.

### 5. Benchmark Harness Changes
- `BenchmarkStats` already collects durations; now it should attach dispatch timestamps before submitting to either async or process engine.
- Use same timestamp cache approach; for async engine, map WorkItem IDs to start times; for process engine, map `(tp, offset)` (WorkItem IDs may remain unique if provided).
- When completion events arrive, run `stats.record(duration)` and also pass to exporter if running in benchmark mode with Prometheus enabled.
- Process engine no longer needs `_PROCESS_METRICS_QUEUE`; remove queue draining logic once parent timing is fully adopted.

### 6. Configuration & Docs
- `config.py`: add `MetricsConfig` with `enabled: bool = False`, `port: int = 9095`.
- `consumer.py`: accept `metrics_config`, instantiate exporter.
- `README.md` + `docs/operations.md`: document `/metrics` endpoint and sample Prometheus scrape configs. Mention required labels and semantics of each metric.
- `AGENTS.md`: note Prometheus dependency, percent-format logging, and new `metrics` config usage.

### 7. Testing Strategy
- Unit tests for `PrometheusMetricsExporter` (ensure metrics update correctly).
- Extend `tests/unit/control_plane/test_broker_poller_metrics.py` to cover exporter update path (mock exporter and assert gauge updates).
- Add WorkManager unit test verifying dispatch timestamp cache & histogram observation runs (with fake exporter).
- Benchmark harness integration test: run with small message counts, assert JSON + (optionally) scrape HTTP endpoint.

## Next Steps
1. Implement exporter module + config wiring.
2. Migrate process benchmark to parent-side latency measurement and remove worker-queue hack.
3. Instrument WorkManager/BrokerPoller/MetadataEncoder with exporter hooks.
4. Update docs + AGENTS.md, run tests, and re-execute benchmarks (baseline + async + process) with Prometheus metrics enabled.
