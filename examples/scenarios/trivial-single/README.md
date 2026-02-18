# Trivial Workload — Single Consumer

Use a single consumer when per-message work is effectively zero (≲0.1 ms). Parallel control-plane overhead only slows you down.

Benchmark (100k msgs, 8 partitions):
- Runtime: 0.35 s
- TPS: ~286,869
- Latency: ~0 ms (avg/p99)

Reproduce:
```bash
uv run python -m benchmarks.run_parallel_benchmark \
  --num-messages 100000 \
  --topic-prefix bench-baseline \
  --baseline-group baseline-bench \
  --skip-async --skip-process \
  --timeout-sec 180 --log-level WARNING
```

When to choose:
- No meaningful CPU/I-O per message
- Just need fastest ingest/commit
