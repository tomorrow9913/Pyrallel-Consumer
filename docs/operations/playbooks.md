# Operational Playbooks & Tuning

For the compact release-review entrypoint that links the fixed benchmark
thresholds, the latest soak evidence package, and the remaining P1/P2 boundary,
see [`stable-operations-evidence.md`](./stable-operations-evidence.md).

## Quick Profiles
- **Low Latency (p99 < 200ms)**: `ExecutionMode=async`, `max_in_flight=256-512`, `poll_batch_size=200-500`, `async_config.task_timeout_ms=500-1000`, `max_blocking_duration_ms=0`.
- **High Throughput**: `ExecutionMode=process`, `process_count=cpu_count`, `poll_batch_size=1000-2000`, `max_in_flight=2_000+`, `process_config.batch_size=128-256`, `process_config.queue_size=4096`, `task_timeout_ms=5000`.
- **Resource Constrained**: `max_in_flight=128-256`, `poll_batch_size=200-500`, `process_count=max(1, cpu_count/2)`, `process_config.batch_size=64`, enable backoff (`max_retries=3`, `retry_backoff_ms=1000`).

## Failure / Recovery Runbook
- **Commit failures**: alert on any increase in `consumer_commit_failures_total{reason="kafka_exception"}`; offsets may replay after restart. Action: check group coordinator health, broker connectivity, commit ACLs, and recent rebalance noise before scaling.
- **DLQ publish failures**: watch `consumer_dlq_publish_failures_total` and logs for `DLQ publish failed`; message remains cached and the offset stays pending retry. Action: check broker connectivity, DLQ topic existence, producer ACLs, and payload limits. Retry by restoring the DLQ path; restart only after confirming the counter stops increasing.
- **Worker crash/timeout**: `CompletionStatus.FAILURE` with attempt=max_retries. Action: inspect worker logs, reduce `task_timeout_ms` for faster detection, increase `max_retries` only with idempotent workers.
- **Rebalance stalls**: commits paused by gaps; monitor `consumer_parallel_lag` and `consumer_gap_count`. Action: verify `blocking_cache_ttl`, ensure `WorkManager` queues stay bounded (see queue cleanup), consider lowering `poll_batch_size`.

## Release Incident / Rollback Runbook

Use this when a new release rollout causes production-impacting regressions.

### Trigger conditions

- sustained `consumer_parallel_lag` growth after rollout
- `consumer_gap_count` not converging to zero
- repeated `DLQ publish failed` events after release promotion
- ordering/retry behavior diverging from pre-release baseline

### Immediate containment (operator)

1. Stop further rollout and freeze deployment automation.
2. Roll back to the last known-good package version (pin/lockfile restore).
3. Restart the consumer with the restored version.
4. Confirm lag/gap/backpressure metrics recover to baseline.

### Release-owner actions (maintainer)

1. Open/append a release incident timeline with UTC timestamps.
2. Capture evidence artifacts: logs, metric snapshots, and failing command/test outputs.
3. Follow `yank + forward fix` policy from `docs/operations/release-versioning-policy.md` for bad releases.
4. Publish mitigation guidance that includes rollback version, scope, and re-rollout criteria.

### Exit criteria

- consumer lag/gap returns to expected steady-state
- no repeated DLQ publish failures related to the incident
- incident note records root cause hypothesis and next verification plan

## Observability & Alerts
- **Backpressure**: `consumer_backpressure_active == 1` for >1 minute -> alert; check `max_in_flight` and queue depth.
- **Lag/Gap**: `consumer_parallel_lag` or `consumer_gap_count` growing for 5m -> investigate stuck offsets, slow workers.
- **Commit failure counter**: any increase in `consumer_commit_failures_total` -> investigate commit path health and replay risk immediately.
- **DLQ**: any increase in `consumer_dlq_publish_failures_total`, failure rate >1%, or repeated warning logs -> validate DLQ topic and payload mode.
- **Oldest task duration**: `consumer_oldest_task_duration_seconds` > timeout -> potential stuck worker; trigger graceful shutdown.

## Tuning Checklist (stepwise)
1) Check logs for errors/backpressure.
2) Inspect metrics: lag, gap count, backpressure, internal queue depth.
3) Fetch/commit path: adjust `poll_batch_size`, `max_in_flight` to relieve pressure.
4) Worker layer: measure worker latency; increase `process_count` (process mode) or lower `task_timeout_ms`.
5) Retry/DLQ: ensure `max_retries` and backoff align with idempotency.
6) Re-run representative workload (`benchmarks/run_parallel_benchmark.py`) and compare TPS/p99.

## Workload Guidance
- **I/O bound**: async mode, higher `max_in_flight`, moderate `poll_batch_size` (<=1000).
- **CPU bound**: process mode, `process_count=cpu_count`, tune `process_config.batch_size` and `queue_size` for CPU saturation.
- **Mixed**: start async; if CPU spikes, move hot paths to process workers for those topics.

## Execution Mode Recommendation Matrix (MQU-168)

This matrix is the operator-facing recommendation surface derived from the current
playbook defaults and release-gate evidence.

| Workload | Ordering | Recommended Mode | Caution Condition | Evidence Anchor |
| --- | --- | --- | --- | --- |
| `io` | `key_hash` | `async` | `process` only when synchronous picklable workers and CPU isolation are required | `Workload Guidance`, release threshold table |
| `io` | `partition` | `async` | `process` requires strict-on benchmark + lag/gap confirmation before adoption | release threshold table |
| `cpu` | `key_hash` | `process` | `async` is acceptable if operational simplicity is preferred | `Workload Guidance`, release threshold table |
| `cpu` | `partition` | `async` (release-path default) | `process` is allowed only with dedicated strict-on validation | release threshold table |
| `sleep` | `key_hash` | `async` | `process` can be used, but profile it explicitly | release threshold table |
| `sleep` | `partition` | `async` | `process` is a caution combo; compare `b64w5` vs `b1w0` before defaults | `mqu166` and `mqu122` artifacts |

`sleep + partition + process` evidence snapshot:
- `benchmarks/results/mqu166-process-partition-b64w5-20260417T050318Z.json` -> `291.61 TPS`
- `benchmarks/results/mqu166-process-partition-b1w0-20260417T050435Z.json` -> `499.13 TPS`
- `benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json` -> completed within the default 60s timeout, `627.08 TPS`

## Benchmark Interpretation Rules (Recommended / Caution / Forbidden)

The categories below are interpretation rules for release decisions, not hard
runtime bans.

### Recommended reading

- Read `strict-completion-monitor=on` as the primary release signal.
- Repeat the same command at least twice and judge by worst values (`TPS` min, `p99` max) per combination.
- Apply fail-fast checks first, then threshold checks, then lag/gap/backpressure observations.

### Caution combinations

- `process + partition`, especially tiny `sleep`/`io` workloads, due to batching/IPC sensitivity.
- `cpu + partition + process` without explicit strict-on verification.
- Mixed workloads moved wholesale to `process` without hot-path isolation.

### Forbidden interpretation

- Declaring release `GO` or changing defaults from a single benchmark run.
- Overriding a strict-on failure with strict-off results.
- Offsetting one failing combination with strong numbers from other combinations.
- Ignoring fail-fast events (`TIMEOUT`, runtime errors, ordering mismatch, incomplete message count, lag/gap gate violations).

## Test Matrix (perf regression gate)
- **Async**: `max_in_flight={256,1024}`, `poll_batch_size={500,1000}` on I/O workload; record TPS/p99.
- **Process**: `process_count={cpu_count/2, cpu_count}`, `process_config.batch_size={64,128}`, `queue_size=2048`; run CPU workload.
- **Kafka-backed correctness**: run `tests/e2e/test_ordering.py` for both `async` and `process` execution modes, including KEY_HASH and PARTITION ordering paths, before stable promotion.
- **Failure paths**: DLQ enabled with `dlq_payload_mode=metadata_only`, inject worker exceptions, assert commits + DLQ succeed.

## Soak / Long-Running Stability Notes
- Goal: capture longer-running evidence for backpressure, rebalance, worker recycle, restart continuity, and DLQ behavior beyond the short correctness suites.
- Minimum note set per run:
  - command line used
  - runtime duration / message volume
  - workload + ordering mode
  - key metrics observed (`throughput_tps`, `p99_processing_ms`, lag/gap, backpressure)
  - whether rebalance/restart/DLQ behavior matched expectations
  - links or paths to JSON/profiler outputs when produced
- Recommended baseline soak flow:
  1. Start the local Kafka/monitoring stack: `docker compose -f .github/e2e.compose.yml up -d kafka-1 kafka-exporter prometheus grafana`
  2. Run a longer benchmark window with Prometheus exposure enabled, for example:
     `uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --workloads sleep,io --order key_hash,partition --num-messages 50000 --num-partitions 8 --strict-completion-monitor on,off --metrics-port 9091`
  3. Re-run the recovery proof suite after the long run: `uv run pytest tests/e2e/test_process_recovery.py -q`
  4. Record observations in the release-readiness issue/comment or a follow-up note before making stronger stability claims.
- Until there is a dedicated automated soak workflow, treat these notes as the required evidence trail for the P1 stability item rather than assuming the short E2E suite is sufficient by itself.

### Copy/paste note template

Use a lightweight note like the following so each soak pass leaves comparable evidence:

```md
## Soak note - <date / branch / operator>

- Command: `<exact benchmark / test command>`
- Duration: `<wall time>` / Volume: `<messages, partitions, keys>`
- Workloads: `<sleep/io/cpu>` / Ordering: `<key_hash|partition>` / Execution: `<async|process>`
- Strict completion monitor: `<on|off>`
- Artifacts:
  - JSON: `<path-or-link>`
  - Profiles / logs / screenshots: `<path-or-link>`
- Observed metrics:
  - throughput_tps: `<value or range>`
  - p99_processing_ms: `<value or range>`
  - lag / gap trend: `<stable|growing|spiky>`
  - backpressure: `<none|intermittent|sustained>`
- Recovery checks:
  - rebalance during in-flight work: `<pass|fail|not exercised>`
  - restart / offset continuity: `<pass|fail|not exercised>`
  - DLQ path: `<pass|fail|not exercised>`
- Result: `<pass|needs follow-up>`
- Follow-ups:
  - `<issue / doc / next action>`
```

### Minimum acceptance for a credible soak note

Before treating a run as meaningful evidence for the remaining P1 stability item, confirm:

1. the exact command and runtime window were recorded
2. at least one artifact path or link was retained
3. lag/gap/backpressure observations were written down, not inferred later
4. rebalance/restart/DLQ status was explicitly marked as `pass`, `fail`, or `not exercised`
5. any limitation or anomaly was recorded before making a stronger stability claim

Release gate verdicts (`PASS`/`FAIL`) follow the fixed gate definitions in
`docs/operations/soak-restart-evidence.md`.

## Release Go/No-Go Threshold (MQU-117)

The thresholds below are the **fixed performance baselines** used for release review
before stable promotion. They were finalized in a CTO round based on cumulative
distribution across `benchmarks/results/*.json` (focused on 10k+ messages,
8 partitions, strict monitor on). For direct MQU-164 decision quoting, also
use the one-page summary in
`docs/operations/mqu-168-execution-mode-release-interpretation.md`.

### Standard Measurement Conditions

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --workloads sleep,cpu,io --order key_hash,partition --strict-completion-monitor on --num-messages 10000 --num-partitions 8 --log-level WARNING --metrics-port 9091 --json-output benchmarks/results/release-gate-<UTC>.json`
- Compare only with profiling disabled (`--profile`/`--py-spy` not used).
- Run at least twice under identical conditions and judge by worst-case values
  per combination (`TPS` minimum, `p99` maximum).

### Workload-Specific Thresholds (PASS Criteria)

| Mode | Workload | Ordering | TPS floor (>=) | p99 ceiling (<= ms) |
| --- | --- | --- | ---: | ---: |
| async | sleep | key_hash | 4,900 | 13 |
| async | sleep | partition | 2,950 | 2 |
| async | cpu | key_hash | 2,050 | 30 |
| async | cpu | partition | 2,050 | 3 |
| async | io | key_hash | 4,950 | 15 |
| async | io | partition | 2,950 | 2 |
| process | sleep | key_hash | 2,550 | 30 |
| process | sleep | partition | 380 | 11 |
| process | cpu | key_hash | 2,100 | 30 |
| process | cpu | partition | 390 | 11 |
| process | io | key_hash | 2,650 | 30 |
| process | io | partition | 390 | 10 |

### Fail-fast / Immediate NO-GO Criteria

If any of the following occurs, it is an immediate `NO-GO`.

1. Non-zero benchmark exit, `TIMEOUT`, `RuntimeError`, or incomplete messages
2. Ordering validation failure or `messages_processed != num_messages`
3. Any single combination falls below `TPS floor` or exceeds `p99 ceiling`
4. Lag/gap gate violation
   - At completion (including +30s observation): `consumer_parallel_lag != 0`
     or `consumer_gap_count != 0`
   - During run: `consumer_gap_count > 0` persists continuously for over 60s

### Verdict Rules

- All combinations meet thresholds and no fail-fast condition: `GO`
- Any single combination triggers fail-fast or violates thresholds: `NO-GO`
- On `NO-GO`, attach run artifacts (JSON/log/metrics query) to the issue
  comment and include a re-measurement plan.
