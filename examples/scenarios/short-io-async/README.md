# Short I/O — Async Engine

Use async when each message does external I/O (DB/API) in the few–hundreds ms range. Concurrency hides latency better than processes for I/O-bound tasks.

Benchmark (100k msgs, 8 partitions):
- Runtime: 5.62 s
- TPS: ~17,790
- Avg: 92.5 ms, P99: 368.8 ms

Reproduce:
```bash
uv run python -m benchmarks.run_parallel_benchmark \
  --num-messages 100000 \
  --topic-prefix bench-baseline \
  --async-group async-bench \
  --skip-baseline --skip-process \
  --timeout-sec 180 --log-level WARNING
```

When to choose:
- External API/DB/file I/O per message (ms–hundreds ms)
- Need good throughput with moderate latency
