# Scenario Guides

When to choose single consumer, async, or process execution based on workload shape.

## Quick Decision
- **Single consumer**: work per message ≲0.1 ms (no real processing). Overhead of parallel control plane outweighs benefits. Baseline TPS ~287k (100k msgs).
- **Async engine**: I/O-bound (DB/API) in the few–hundreds ms range. Throughput scales with concurrency. Example TPS ~17.8k (avg 92 ms, p99 369 ms) on 100k msgs.
- **Process engine**: CPU-bound (LLM, image, heavy compute) where GIL matters. Accept higher overhead/latency. Example TPS ~1.24k (avg 1,078 ms, p99 1,839 ms) on 100k msgs.

## Benchmarks (100k messages, 8 partitions, log-level=WARNING)
| Mode      | Runtime | TPS       | Avg (ms) | P99 (ms) | Notes |
|-----------|---------|-----------|----------|----------|-------|
| baseline  | 0.35 s  | 286,869   | ~0       | ~0       | Plain consumer, no parallel control plane |
| async     | 5.62 s  | 17,790    | 92.5     | 368.8    | Good for I/O-bound |
| process   | 80.46 s | 1,243     | 1,078    | 1,839    | For CPU-bound workloads |

Commands to reproduce:
- Baseline only:
  ```bash
  uv run python -m benchmarks.run_parallel_benchmark \
    --num-messages 100000 \
    --topic-prefix bench-baseline \
    --baseline-group baseline-bench \
    --skip-async --skip-process \
    --timeout-sec 180 --log-level WARNING
  ```
- Async only:
  ```bash
  uv run python -m benchmarks.run_parallel_benchmark \
    --num-messages 100000 \
    --topic-prefix bench-baseline \
    --async-group async-bench \
    --skip-baseline --skip-process \
    --timeout-sec 180 --log-level WARNING
  ```
- Process only:
  ```bash
  uv run python -m benchmarks.run_parallel_benchmark \
    --num-messages 100000 \
    --topic-prefix bench-baseline \
    --process-group process-bench \
    --skip-baseline --skip-async \
    --timeout-sec 180 --log-level WARNING
  ```

## Example Scenarios
- `trivial-single/`: near-zero work per message; single consumer is fastest.
- `short-io-async/`: few–hundreds ms external I/O; async engine shines.
- `heavy-cpu-process/`: CPU-heavy tasks; process engine avoids GIL.
