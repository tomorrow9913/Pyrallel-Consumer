# Operational Playbooks & Tuning

## Quick Profiles
- **Low Latency (p99 < 200ms)**: `ExecutionMode=async`, `max_in_flight=256-512`, `poll_batch_size=200-500`, `worker_pool_size` (process) not used, `async_config.task_timeout_ms=500-1000`, `max_blocking_duration_ms=0`.
- **High Throughput**: `ExecutionMode=process`, `worker_pool_size=cpu_count`, `poll_batch_size=1000-2000`, `max_in_flight=2_000+`, `process_config.batch_size=128-256`, `process_config.queue_size=4096`, `task_timeout_ms=5000`.
- **Resource Constrained**: `max_in_flight=128-256`, `poll_batch_size=200-500`, `worker_pool_size=cpu_count/2`, `process_config.batch_size=64`, enable backoff (`max_retries=3`, `retry_backoff_ms=1000`).

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
4) Worker layer: measure worker latency; increase `worker_pool_size` (process) or lower `task_timeout_ms`.
5) Retry/DLQ: ensure `max_retries` and backoff align with idempotency.
6) Re-run representative workload (`benchmarks/run_parallel_benchmark.py`) and compare TPS/p99.

## Workload Guidance
- **I/O bound**: async mode, higher `max_in_flight`, moderate `poll_batch_size` (<=1000).
- **CPU bound**: process mode, `worker_pool_size=cpu_count`, tune `process_config.batch_size` and `queue_size` for CPU saturation.
- **Mixed**: start async; if CPU spikes, move hot paths to process workers for those topics.

## Test Matrix (perf regression gate)
- **Async**: `max_in_flight={256,1024}`, `poll_batch_size={500,1000}` on I/O workload; record TPS/p99.
- **Process**: `worker_pool_size={cpu_count/2, cpu_count}`, `process_config.batch_size={64,128}`, `queue_size=2048`; run CPU workload.
- **Failure paths**: DLQ enabled with `dlq_payload_mode=metadata_only`, inject worker exceptions, assert commits + DLQ succeed.
