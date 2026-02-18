# Pyrallel Consumer Examples

This directory contains runnable examples demonstrating how to use Pyrallel Consumer.

## Prerequisites

1. A running Kafka broker (default: `localhost:9092`)
2. Install dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```
3. Produce test messages (using the bundled producer script):
   ```bash
   uv run python benchmarks/producer.py --num-messages 1000 --num-keys 50 --topic demo
   ```

## Examples

### `async_simple.py` — Async Execution Engine

Demonstrates the recommended async mode using `PyrallelConsumer` facade. Best for I/O-bound workloads (HTTP calls, database queries, file I/O).

```bash
uv run python examples/async_simple.py
```

### `process_cpu.py` — Process Execution Engine

Demonstrates multiprocessing mode for CPU-bound workloads. Uses micro-batching to reduce IPC overhead.

```bash
uv run python examples/process_cpu.py
```

## Configuration

All examples use environment variables or `.env` file for Kafka configuration. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker address |
| `KAFKA_CONSUMER_GROUP` | `pyrallel-consumer-group` | Consumer group ID |
| `PARALLEL_CONSUMER_EXECUTION__MODE` | `async` | `async` or `process` |
| `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | `1000` | Max concurrent messages |

See `pyrallel_consumer/config.py` for the full configuration schema.

## Tuning Guide

### Async Mode (I/O-bound)
- Increase `max_in_flight` to improve throughput for high-latency workers
- Set `async_config.task_timeout_ms` to match your SLA

### Process Mode (CPU-bound)
- `process_config.process_count`: Match your CPU core count
- `process_config.batch_size`: Larger batches reduce IPC overhead but increase latency
  - I/O-bound workers: `batch_size=16-32`
  - CPU-bound workers: `batch_size=64-128`
- `process_config.max_batch_wait_ms`: Lower = less latency, higher = better batching
  - Real-time: `1-5ms`
  - Throughput-optimized: `10-50ms`
- `process_config.queue_size`: Should be `>= batch_size * process_count * 2`

### Scenario Guides

See `examples/scenarios/` for when to pick single vs async vs process, with benchmark results for each workload shape.
