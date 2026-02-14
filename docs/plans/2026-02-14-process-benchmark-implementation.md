# Process Benchmark + Prometheus Metrics Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ensure process-engine benchmarks finish without hangs and expose production-grade Prometheus metrics (throughput, lag, queue depth, latency, metadata size).

**Architecture:** Record WorkItem dispatch timestamps in the parent process, compute latency when completion events arrive, and feed both benchmark stats and Prometheus histograms. Add a `PrometheusMetricsExporter` that maps existing `SystemMetrics`/`PartitionMetrics` data into counters and gauges, wired through `consumer.py` with opt-in config.

**Tech Stack:** Python 3.12, Prometheus client library, asyncio, multiprocessing, pytest.

---

### Task 1: Dependencies & Configuration Skeleton

**Files:**
- Modify: `pyproject.toml` (dependency block)
- Modify: `requirements.txt` (if present)
- Modify: `pyrallel_consumer/config.py:41-107`
- Create: `pyrallel_consumer/config.py` section for `MetricsConfig`

**Step 1: Add Prometheus client dependency**

```toml
[project]

**Step 2: Update requirements**

```

**Step 3: Introduce MetricsConfig**

```python
class MetricsConfig(BaseSettings):
    enabled: bool = False
    port: int = 9095

class KafkaConfig(...):
    metrics: MetricsConfig = MetricsConfig()
```

**Step 4: No tests yet (run later when modules exist)**

**Step 5: Commit**

```

### Task 2: WorkManager Dispatch Timestamp Tracking

**Files:**
- Modify: `pyrallel_consumer/control_plane/work_manager.py`
- Test: `tests/unit/control_plane/test_work_manager.py` (new test covering latency cache)

**Step 1: Write failing test**

Add test verifying WorkManager exposes hooks for dispatch timestamps and passes durations to a fake exporter on completion.

```python
    exporter = mocker.Mock()
    manager = WorkManager(..., metrics_exporter=exporter)
    manager.submit_work_item(fake_item)
    completion = CompletionEvent(...)
    manager.handle_completion(completion)
    exporter.observe_completion.assert_called_once()
```

**Step 2: Run test**

```
pytest tests/unit/control_plane/test_work_manager.py::test_work_manager_records_and_clears_dispatch_latency -v
```
Expected: FAIL (method not implemented).

**Step 3: Implement dispatch cache**

```python
class WorkManager:
    def __init__(..., metrics_exporter: Optional[MetricsExporter] = None):
        self._metrics_exporter = metrics_exporter
        self._dispatch_times: dict[tuple[TopicPartition, int], float] = {}

    async def _submit(...):
        self._dispatch_times[(tp, offset)] = time.perf_counter()

    def _handle_completion(...):
        ts = self._dispatch_times.pop((event.tp, event.offset), None)
        if ts and self._metrics_exporter:
            duration = time.perf_counter() - ts
            self._metrics_exporter.observe_completion(event.tp, event.status, duration)
```

Ensure cache cleanup on partition revoke/epoch mismatch.

**Step 4: Re-run test (plus regression)**

```
pytest tests/unit/control_plane/test_work_manager.py -v
```

**Step 5: Commit**

```
```

### Task 3: Prometheus Metrics Exporter Module

**Files:**
- Add: `pyrallel_consumer/metrics_exporter.py`
- Modify: `pyrallel_consumer/__init__.py` (optional export)
- Tests: `tests/unit/metrics/test_prometheus_exporter.py`

**Step 1: Write failing test**

```python
    exporter = PrometheusMetricsExporter(port=None)
    exporter.update_from_system_metrics(system_metrics)
    gauge_value = exporter.consumer_parallel_lag.labels(...).get()
    assert gauge_value == expected
```

**Step 2: Run tests**

```
pytest tests/unit/metrics/test_prometheus_exporter.py::test_exporter_updates_gauges -v
```
Expect failure (module missing).

**Step 3: Implement exporter**

```python
from prometheus_client import Counter, Gauge, Histogram, start_http_server

class PrometheusMetricsExporter:
    def __init__(self, config: MetricsConfig):
        if config.enabled:
            start_http_server(config.port)
        self._processed_total = Counter(...)
        self._latency_hist = Histogram(...)
        self._lag_gauge = Gauge(...)
        ...

    def update_from_system_metrics(self, metrics: SystemMetrics) -> None:
        self._in_flight_gauge.set(metrics.total_in_flight)
        for partition in metrics.partitions:
            self._lag_gauge.labels(...).set(partition.true_lag)
            ...

    def observe_completion(self, tp, status, duration):
        self._processed_total.labels(topic=tp.topic, partition=tp.partition, status=status.value).inc()
        self._latency_hist.labels(topic=tp.topic, partition=tp.partition).observe(duration)

    def update_metadata_size(self, topic: str, size: int) -> None:
        self._metadata_gauge.labels(topic=topic).set(size)
```

**Step 4: Re-run tests**

```
pytest tests/unit/metrics/test_prometheus_exporter.py -v
```

**Step 5: Commit**

```
```

### Task 4: Wire Exporter into Consumer & Poller

**Files:**
- Modify: `pyrallel_consumer/consumer.py`
- Modify: `pyrallel_consumer/control_plane/broker_poller.py`
- Modify: `pyrallel_consumer/control_plane/work_manager.py` (inject exporter)
- Modify: `pyrallel_consumer/control_plane/metadata_encoder.py`
- Tests: update relevant unit tests (consumer, broker_poller)

**Step 1: Update consumer facade**

```python
class PyrallelConsumer:
    def __init__(self, kafka_config: KafkaConfig):
        self._metrics_exporter = PrometheusMetricsExporter(kafka_config.metrics)
        self._work_manager = WorkManager(..., metrics_exporter=self._metrics_exporter)
        self._poller = BrokerPoller(..., metrics_exporter=self._metrics_exporter)
```

**Step 2: BrokerPoller sends gauges**

```python
metrics = SystemMetrics(...)
if self._metrics_exporter:
    self._metrics_exporter.update_from_system_metrics(metrics)
return metrics
```

**Step 3: MetadataEncoder reports payload size**

```python
payload = encoder.encode(...)
if self._metrics_exporter:
    self._metrics_exporter.update_metadata_size(topic, len(payload))
```

**Step 4: Adjust unit tests**
- Mock exporter to assert update calls in broker poller tests.
- Ensure consumer init wires exporter when metrics enabled.

**Step 5: Run targeted tests**

```
pytest tests/unit/control_plane/test_broker_poller_metrics.py tests/unit/control_plane/test_work_manager.py tests/unit/test_consumer.py -v
```

**Step 6: Commit**

```
```

### Task 5: Benchmark Harness & Process Engine Cleanup

**Files:**
- Modify: `benchmarks/stats.py`
- Modify: `benchmarks/run_parallel_benchmark.py`
- Modify: `pyrallel_consumer_test.py`
- Modify: `pyrallel_consumer/execution_plane/process_engine.py` (remove unused queue hooks)
- Tests: `tests/unit/execution_plane/test_process_execution_engine.py` (ensure functionality unaffected)

**Step 1: Update BenchmarkStats to track dispatch timestamps**

```python
self._dispatch_times[id] = perf_counter()
def complete(id):
    duration = perf_counter() - self._dispatch_times.pop(id)
    self.record(duration)
```

**Step 2: Modify harness to use parent timing**

```python
start = stats.mark_dispatched(work_item_id)
...
stats.mark_completed(work_item_id)
```

**Step 3: Remove `_PROCESS_METRICS_QUEUE` from `pyrallel_consumer_test.py`
- Delete queue setup/drain threads
- Use WorkManager timestamps for process mode too

**Step 4: Ensure ProcessExecutionEngine still valid (no extra code needed)**

**Step 5: Run benchmark smoke test (small message count)**

```
uv run python benchmarks/run_parallel_benchmark.py --num-messages 1000 --skip-baseline False --skip-async False --skip-process True
```

**Step 6: Commit**

```
```

### Task 6: Documentation & Full Test Suite

**Files:**
- Modify: `README.md`
- Modify: `docs/operations.md`
- Modify: `AGENTS.md`
- Modify: `GEMINI.md`

**Step 1: Document /metrics endpoint**
- Explain enabling via config, list metrics table with descriptions.

**Step 2: Update operations docs**
- Replace “assuming Prometheus” wording with actual instructions.

**Step 3: Update AGENTS.md & GEMINI.md**
- Note new dependency and metrics workflow.

**Step 4: Run full tests**

```
uv run pytest -q
```

**Step 5: Run lint (optional)**

```
pre-commit run --all-files
```

**Step 6: Commit**

```
```

---

Plan complete and saved to `docs/plans/2026-02-14-process-benchmark-implementation.md`. Two execution options:

1. **Subagent-Driven (this session)** – I’ll spin up a fresh subagent per task, review between tasks, and iterate quickly.
2. **Parallel Session (separate)** – Start a new session in a clean worktree using `superpowers:executing-plans` for batch execution.

Which approach would you like?
