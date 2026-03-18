[English](./README.md) | [한국어](./README.ko.md)

# Pyrallel Consumer

## High-performance Kafka Parallel Processing Library

`Pyrallel Consumer` is a **Python Kafka parallel consumer** for high-throughput stream processing.
If you are looking for a **parallel consumer for Kafka in Python**, this project provides **key-ordered processing**, robust offset commit semantics, and runtime-selectable execution engines (`asyncio` / multiprocessing).

Inspired by Java's `confluentinc/parallel-consumer`, it is designed to maximize parallelism while preserving ordering guarantees and data consistency.

> **Release policy:** current published versions are alpha/prerelease (`0.1.2a1`). Treat `main` as an active hardening branch until the version/classifier policy is promoted beyond alpha.

## 🌟 Key Features

- **High parallelism**: Process messages in parallel without being tightly limited by Kafka partition count.
- **Ordering guarantees**: Preserve processing order per message key.
- **Data consistency**: Gap-based offset commit strategy to minimize duplicate processing during rebalances/restarts.
- **Stability & visibility**: Epoch-based fencing and rich operational metrics.
- **Flexible execution model**: Runtime-selectable hybrid architecture (`AsyncExecutionEngine` / `ProcessExecutionEngine`).

## 📈 Observability

A `PrometheusMetricsExporter` helper exists for Prometheus integration, but the
current `PyrallelConsumer` facade does **not** auto-start or auto-wire that
exporter from `KafkaConfig.metrics` yet.

Treat `KafkaConfig.metrics` as configuration for manual or advanced wiring
rather than a one-line facade toggle.

### Core Metrics

| Metric | Type | Labels | Description |
| --- | --- | --- | --- |
| `consumer_processed_total` | Counter | `topic`, `partition`, `status` | Number of completed messages (success/failure) |
| `consumer_processing_latency_seconds` | Histogram | `topic`, `partition` | End-to-end processing latency (WorkManager submit → completion) |
| `consumer_in_flight_count` | Gauge | – | Current in-flight message count |
| `consumer_parallel_lag` | Gauge | `topic`, `partition` | True lag (`last_fetched - last_committed`) |
| `consumer_gap_count` | Gauge | `topic`, `partition` | Number of commit-blocking gaps |
| `consumer_internal_queue_depth` | Gauge | `topic`, `partition` | Messages waiting in virtual partition queue |
| `consumer_oldest_task_duration_seconds` | Gauge | `topic`, `partition` | Time blocked by oldest offset/task |
| `consumer_backpressure_active` | Gauge | – | Backpressure status (1=paused) |
| `consumer_metadata_size_bytes` | Gauge | `topic` | Kafka commit metadata payload size |

These metrics are based on the same values returned by `BrokerPoller.get_metrics()`.

## 📊 Benchmark Snapshot (profiling OFF)

Recent run (4 partitions, 2000 messages, 100 keys, profiling off):

### Workload semantics (what each option actually does)

- `sleep` workload (`--worker-sleep-ms N`)
  - Simulates blocking latency by calling `time.sleep(N/1000)` per message.
  - Useful for modeling external blocking work with minimal CPU usage.

- `io` workload (`--worker-io-sleep-ms N`)
  - Simulates async I/O latency by awaiting `asyncio.sleep(N/1000)` per message.
  - Useful for network/DB-like I/O-bound behavior.

- `cpu` workload (`--worker-cpu-iterations K`)
  - Simulates CPU-bound work by repeatedly applying hash computation (`sha256`) `K` times per message.
  - Higher `K` means more CPU pressure per message.

These options are implemented in the benchmark workers and directly control per-message work cost.

| Workload | Setting | baseline TPS | async TPS | process TPS |
| --- | --- | --- | --- | --- |
| sleep | `--workloads sleep --order key_hash --worker-sleep-ms 5` | 159.69 | 2206.35 | 910.04 |
| cpu | `--workloads cpu --order key_hash --worker-cpu-iterations 500` | 2598.79 | 1403.07 | 2072.26 |
| io | `--workloads io --order key_hash --worker-io-sleep-ms 5` | 159.89 | 2797.18 | 916.60 |

> Note: process mode benchmarks were run with profiling disabled for stability.

## 🚀 Architecture Overview

`Pyrallel Consumer` is organized into three layers: **Control Plane**, **Execution Plane**, and **Worker Layer**.
The Control Plane manages Kafka communication and offsets independently from execution mode.
The Execution Plane runs user workers via `asyncio` tasks or multiprocessing.

The control plane talks to execution engines through the shared `BaseExecutionEngine`
contract. Process-specific commit clamping is exposed as an engine capability, so
`BrokerPoller` does not need concrete `ProcessExecutionEngine` type checks to stay safe.

```mermaid
graph TD
    subgraph "Ingress Layer (Kafka Client)"
        A["Kafka Broker"] --> B["BrokerPoller"]
    end

    subgraph "Routing Layer (Dispatcher)"
        B --> C{"Key Extractor"}
        C --> D["Virtual Partition 1"]
        C --> E["Virtual Partition 2"]
    end

    subgraph "Execution Layer (Execution Engine)"
        D --> G["ExecutionEngine.submit"]
        E --> G
        G --> H["AsyncExecutionEngine"]
        G --> I["ProcessExecutionEngine"]
        H --> J["Async Worker Task"]
        I --> K["Process Worker"]
        J & K --> L["Completion Channel"]
    end

    subgraph "Management & Control (Control Plane)"
        L --> M["WorkManager"]
        M --> N["Offset Tracker"]
        N --> O["Commit Encoder"]
    end
```

## 🛠️ Installation & Setup

### Dependency Management (`uv`)

```bash
pip install uv
uv sync
uv sync --group dev  # optional
```

### Package Build / Distribution

```bash
pip install .
python -m pip install build
python -m build
# artifacts: dist/*.tar.gz, dist/*.whl
```

### Security / Config Notes

- Grafana admin password is expected via `GF_SECURITY_ADMIN_PASSWORD` in `.env`.
- For DLQ payload minimization, set `KAFKA_DLQ_PAYLOAD_MODE=metadata_only`.
- License: Apache-2.0

### Env-based Config (`pydantic-settings`)

```dotenv
KAFKA_BOOTSTRAP_SERVERS=localhost:9092
KAFKA_CONSUMER_GROUP=my-consumer-group
PARALLEL_CONSUMER_EXECUTION__MODE=async  # or process
```

## 🔁 Retry & DLQ

Pyrallel Consumer supports automatic retries and DLQ publishing.

### Retry (`ExecutionConfig`)

| Env | Default | Description |
| --- | --- | --- |
| `EXECUTION_MAX_RETRIES` | `3` | Max retry count |
| `EXECUTION_RETRY_BACKOFF_MS` | `1000` | Initial backoff (ms) |
| `EXECUTION_EXPONENTIAL_BACKOFF` | `true` | Exponential backoff toggle |
| `EXECUTION_MAX_RETRY_BACKOFF_MS` | `30000` | Max backoff cap (ms) |
| `EXECUTION_RETRY_JITTER_MS` | `200` | Random jitter range (ms) |

### DLQ (`KafkaConfig`)

| Env | Default | Description |
| --- | --- | --- |
| `KAFKA_DLQ_ENABLED` | `true` | Enable DLQ publish |
| `KAFKA_DLQ_TOPIC_SUFFIX` | `.dlq` | DLQ topic suffix |

DLQ headers include:
- `x-error-reason`
- `x-retry-attempt`
- `source-topic`
- `partition`
- `offset`
- `epoch`

## 💡 Usage

```python
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import ExecutionMode, WorkItem

config = KafkaConfig()
config.dlq_enabled = True
config.DLQ_TOPIC_SUFFIX = ".failed"
config.parallel_consumer.execution.mode = ExecutionMode.ASYNC
config.parallel_consumer.execution.max_retries = 5
config.parallel_consumer.execution.retry_backoff_ms = 2000

async def worker(item: WorkItem):
    ...

consumer = PyrallelConsumer(config=config, worker=worker, topic="orders")
```

For detailed runnable patterns, see [`examples/`](./examples/).

### Rebalance state preservation

- Default: `contiguous_only`
  - On rebalance/restart, only the safe contiguous HWM is preserved via the committed Kafka offset.
  - Sparse completed offsets beyond the HWM may be replayed later; this is the simplest and safest at-least-once default.
- Optional: `metadata_snapshot`
  - Sparse completed offsets are encoded into Kafka commit metadata on revoke/commit and restored on the next assignment.
  - This can reduce avoidable reprocessing, but failures must remain fail-closed back to `contiguous_only` semantics.

Even with `metadata_snapshot`, downstream side effects should remain idempotent.

## 🧪 Run Benchmarks

```bash
uv run python -m benchmarks.run_parallel_benchmark

# or pass flags directly for the existing CLI flow
uv run python benchmarks/run_parallel_benchmark.py \
  --bootstrap-servers localhost:9092 \
  --num-messages 50000 \
  --num-keys 200 \
  --num-partitions 8
```

- JSON report is saved to `benchmarks/results/<UTC timestamp>.json`.
- No flags: launches a Textual TUI so you can configure the benchmark interactively.
- You can skip rounds with `--skip-baseline`, `--skip-async`, `--skip-process`.
- Use `--workloads sleep,cpu` to run any subset of workloads and `--order key_hash,partition` to run multiple ordering modes in one invocation.
- Use `--strict-completion-monitor on,off` to compare the completion monitor modes in benchmark output.
- Topic/group reset is enabled by default; disable with `--skip-reset` if needed.

## 📖 Documentation

- `prd_dev.md`: concise developer-oriented summary
- `prd.md`: full design rationale and architecture details
- `docs/ops_playbooks.md`: ops playbook, tuning guide, incident response

## 📊 Monitoring Stack (Prometheus + Grafana)

Included stack (via `docker-compose.yml`):
- Prometheus (9090)
- Grafana (3000)
- Kafka Exporter (9308)
- Kafka UI (8080)
- Kafka (9092)

Usage:

1) Current facade note:
- `config.metrics.enabled = True` by itself does **not** auto-expose `/metrics`
  through `PyrallelConsumer` yet.
- Use this stack only after wiring `PrometheusMetricsExporter` manually in a
  custom integration, or when working through lower-level components.

2) Start stack:
```bash
docker compose up -d
```

3) Verify:
- Prometheus: http://localhost:9090
- Grafana: http://localhost:3000 (`GF_SECURITY_ADMIN_PASSWORD` from `.env`)

4) Add Grafana datasource:
- Type: Prometheus
- URL: `http://prometheus:9090`
- Access: Server

5) Example queries:
- `consumer_processed_total`
- `consumer_processing_latency_seconds_bucket`
- `consumer_in_flight_count`

## 🤝 Contributing

All commit messages follow [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).

---
© 2026 Pyrallel Consumer Project
