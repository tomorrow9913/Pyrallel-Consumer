# Benchmark Validation & Kafka Reset Design (2026-02-14)

## Overview
The user wants three sequential tracks:
1. Run full repository validation (pytest + pre-commit) on the current branch before further edits.
2. Enhance the benchmark harness so it deletes and recreates Kafka topics/consumer groups before each run using Confluent's AdminClient, guaranteeing zero residual lag.
3. Re-run baseline, async, and process benchmarks end-to-end once resetting is reliable.

This design focuses on how to accomplish (1) and (2) safely so that (3) becomes routine. The existing `2026-02-14-process-benchmark-design.md` already covers parent-side latency metrics; we build atop that foundation.

## Validation Workflow
1. Ensure dependencies are installed via `uv pip install -r requirements.txt` and `dev-requirements.txt` as needed.
2. Execute `uv run pytest` for the full suite. Capture output and treat any failure as a blocker until fixed.
3. Execute `pre-commit run --all-files` to cover lint, formatting, typing, and security checks.
4. Log both outcomes (pass/fail) into `GEMINI.md` immediately after each meaningful step per GEMINI rules.

Failure handling: stop modifications when validation fails, fix targeted modules, rerun the affected command(s), and only proceed once the full suite plus pre-commit succeed.

## Kafka Reset Utility
### Goals
- Provide a Python-native helper that deletes then recreates topics and consumer groups using `confluent_kafka.admin.AdminClient`.
- Integrate helper into both `benchmarks/run_parallel_benchmark.py` and `pyrallel_consumer_test.py` so they can start with a clean slate.
- Offer a CLI flag to skip resets when Kafka permissions are missing.

### API Sketch
```python
def reset_topics_and_groups(
    bootstrap_servers: str,
    topics: dict[str, TopicConfig],
    consumer_groups: list[str],
    replication_factor: int = 1,
    timeout_s: float = 10.0,
    retries: int = 3,
) -> None:
    ...
```

`TopicConfig` holds `num_partitions` and optional configs. The helper internally instantiates `AdminClient` once and performs delete -> wait -> create.

### Behavior Details
1. **Delete Topics**: call `delete_topics`. Treat `UNKNOWN_TOPIC_OR_PARTITION` as success. Retry up to `retries` with exponential backoff when Kafka reports `REQUEST_TIMED_OUT` or network issues.
2. **Delete Consumer Groups**: `delete_consumer_groups`. Ignore `GROUP_ID_NOT_FOUND` but surface other errors.
3. **Create Topics**: use `NewTopic` with the configured partition count and replication factor. Wait for futures; retry on transient failures.
4. **Skip Flag**: CLI flag `--skip-reset` bypasses everything, logging an info message.
5. **Logging**: use percent-style `logger.info` with topic/group names for traceability.

### Error Surfaces
- If deletes fail consistently, abort the benchmark with a clear exception instructing the user to fix permissions or clean up manually.
- Helper should propagate `KafkaException` after exhausting retries.

## Benchmark Flow After Reset
1. (Optional) Reset topics/groups (default: enabled).
2. Produce workload and run baseline consumer; collect stats JSON.
3. Run async Pyrallel benchmark; ensure Prometheus exporter toggles remain available.
4. Run process Pyrallel benchmark; rely on parent-side latency metrics to avoid hangs.
5. Summarize metrics and store artifacts under `benchmarks/results/` plus GEMINI entries.

## Testing Strategy
- Unit-test the reset helper with mocked `AdminClient` methods to verify retry logic and graceful handling of missing topics/groups.
- Smoke-test benchmarks locally against Kafka by running with a tiny workload and ensuring topics/groups disappear between runs.
- Keep existing metrics/unit tests intact; no new coverage required beyond the helper.

## Next Step
With this design approved, proceed to the implementation plan (invoke `superpowers/writing-plans`).
