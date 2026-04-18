# Operational Playbooks & Tuning

## Quick Profiles
- **Low Latency (p99 < 200ms)**: `ExecutionMode=async`, `max_in_flight=256-512`, `poll_batch_size=200-500`, `async_config.task_timeout_ms=500-1000`, `max_blocking_duration_ms=0`.
- **High Throughput**: `ExecutionMode=process`, `process_count=cpu_count`, `poll_batch_size=1000-2000`, `max_in_flight=2_000+`, `process_config.batch_size=128-256`, `process_config.queue_size=4096`, `task_timeout_ms=5000`.
- **Resource Constrained**: `max_in_flight=128-256`, `poll_batch_size=200-500`, `process_count=max(1, cpu_count/2)`, `process_config.batch_size=64`, enable backoff (`max_retries=3`, `retry_backoff_ms=1000`).

## Failure / Recovery Runbook
- **DLQ publish failures**: watch logs for `DLQ publish failed`; message remains cached. Action: check broker connectivity, DLQ topic ACLs. Retry by restarting consumer after restoring DLQ path.
- **Worker crash/timeout**: `CompletionStatus.FAILURE` with attempt=max_retries. Action: inspect worker logs, reduce `task_timeout_ms` for faster detection, increase `max_retries` only with idempotent workers.
- **Rebalance stalls**: commits paused by gaps; monitor `consumer_parallel_lag` and `consumer_gap_count`. Action: verify `blocking_cache_ttl`, ensure `WorkManager` queues stay bounded (see queue cleanup), consider lowering `poll_batch_size`.

## Observability & Alerts
- **Backpressure**: `consumer_backpressure_active == 1` for >1 minute → alert; check `max_in_flight` and queue depth.
- **Lag/Gap**: `consumer_parallel_lag` or `consumer_gap_count` growing for 5m → investigate stuck offsets, slow workers.
- **DLQ**: failure rate >1% or repeated warning logs → validate DLQ topic and payload mode.
- **Oldest task duration**: `consumer_oldest_task_duration_seconds` > timeout → potential stuck worker; trigger graceful shutdown.

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

## Test Matrix (perf regression gate)
- **Async**: `max_in_flight={256,1024}`, `poll_batch_size={500,1000}` on I/O workload; record TPS/p99.
- **Process**: `process_count={cpu_count/2, cpu_count}`, `process_config.batch_size={64,128}`, `queue_size=2048`; run CPU workload.
- **Kafka-backed correctness**: run `tests/e2e/test_ordering.py` (KEY_HASH/PARTITION + async/process) before stable promotion.
- **Kafka-backed recovery gates**: run `tests/e2e/test_process_recovery.py::test_process_rebalance_keeps_commit_safe_while_work_is_inflight`, `tests/e2e/test_process_recovery.py::test_process_restart_preserves_offset_continuity`, `tests/e2e/test_process_recovery.py::test_process_retry_path_commits_only_after_success`, `tests/e2e/test_process_recovery.py::test_process_dlq_path_commits_after_retry_exhaustion`.
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
