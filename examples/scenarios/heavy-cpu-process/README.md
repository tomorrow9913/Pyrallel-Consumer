# Heavy CPU — Process Engine

Use process execution when work is CPU-bound (LLM calls, image/ML preprocessing, heavy computation) and GIL would bottleneck async. Accept higher overhead/latency.

Benchmark (100k msgs, 8 partitions):
- Runtime: 80.46 s
- TPS: ~1,243
- Avg: 1,078 ms, P99: 1,839 ms

Reproduce:
```bash
uv run python -m benchmarks.run_parallel_benchmark \
  --num-messages 100000 \
  --topic-prefix bench-baseline \
  --process-group process-bench \
  --skip-baseline --skip-async \
  --timeout-sec 180 --log-level WARNING
```

When to choose:
- CPU-heavy per-message work (tens of ms+)
- Need parallelism beyond a single Python process (GIL avoidance)
