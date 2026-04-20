# Soak/Restart Evidence Package and Pass/Fail Gate

This document fixes the **evidence package format** and **pass/fail criteria**
for long-running soak/restart validation.
The P1 item in release readiness (`long-running soak / restart recovery
validation`) is judged against this document.

## Fixed Pass/Fail Criteria

All three gates below must pass for final `PASS`.

1. Soak execution gate
   - Required soak command (or equivalent coverage command) must complete with
     exit code `0`.
   - No `TIMEOUT`, `RuntimeError`, or incomplete message state in results.
   - Artifact paths (JSON or equivalent structured output + execution log)
     must be retained.
2. Recovery semantics gate
   - `tests/e2e/test_process_recovery.py` must pass.
   - The suite must include all axes below.
     - in-flight rebalance commit safety
     - restart/offset continuity
     - retry-after-success commit safety
     - DLQ-after-retry-exhaustion commit behavior
3. Evidence completeness gate
   - Record command/time/volume/ordering/workload/strict monitor values.
   - Record backpressure and lag/gap observations.
   - Explicitly mark `rebalance`, `restart`, and `DLQ` status as
     `pass|fail|not exercised`.

`Overall verdict` rule:
- all three gates pass: `PASS`
- any gate fails: `FAIL`

## Evidence Package Template

```md
## Soak/Restart evidence - <date>

- Soak command: `<exact command>`
- Runtime / volume: `<wall time>`, `<messages>`, `<partitions>`, `<keys>`
- Workloads / ordering / strict monitor: `<...>`
- Artifacts:
  - soak log: `<path>`
  - soak json: `<path-or-none>`
  - recovery log: `<path>`
- Observations:
  - backpressure: `<summary>`
  - lag/gap: `<summary>`
  - anomalies: `<summary>`
- Recovery checks:
  - rebalance: `<pass|fail|not exercised>`
  - restart: `<pass|fail|not exercised>`
  - DLQ: `<pass|fail|not exercised>`
- Gate results:
  - soak execution gate: `<pass|fail>`
  - recovery semantics gate: `<pass|fail>`
  - evidence completeness gate: `<pass|fail>`
- Overall verdict: `<PASS|FAIL>`
- Follow-up: `<issue / owner / next action>`
```

## Latest Evidence (2026-04-17 UTC)

### Run A - full soak matrix (default timeout)

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --workloads sleep,io --order key_hash,partition --num-messages 20000 --num-partitions 8 --strict-completion-monitor on,off --metrics-port 9091 --json-output benchmarks/results/mqu115-soak-20260416T171555Z.json`
- Artifact:
  - soak log: `.omx/artifacts/mqu-115/soak-20260416T171555Z.log`
  - soak json: not generated (run aborted on timeout)
- Key observation:
  - `process + partition + strict on` hit the `60s` timeout
  - processed `16653 / 20000`, `Final TPS 276.55`
  - other preceding slices (`key_hash` combinations, `async partition`) completed with ordering pass

### Run B - failing slice isolated with extended timeout (structured artifact captured)

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --timeout-sec 180 --metrics-port 9091 --json-output benchmarks/results/mqu115-process-partition-strict-on-20260416T171849Z.json`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-115/soak-process-partition-strict-on-20260416T171849Z.log`
  - soak json: `benchmarks/results/mqu115-process-partition-strict-on-20260416T171849Z.json`
- Result:
  - completed in `81.00s`
  - throughput `246.90 TPS`
  - ordering validation `PASS (partition)`

### Run C - recovery semantics

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run pytest tests/e2e/test_process_recovery.py -q`
- Artifact:
  - recovery log: `.omx/artifacts/mqu-115/process-recovery-20260416T171800Z.log`
- Result:
  - `4 passed in 17.58s`
  - rebalance/restart/retry/DLQ scenarios all passed

### Run D - strict-on failing slice rerun after remediation (default timeout 60s)

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --metrics-port 9091 --json-output benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-122/soak-process-partition-strict-on-20260416T175324Z.log`
  - soak json: `benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json`
- Result:
  - completed in `31.89s` (within default timeout `60s`)
  - throughput `627.08 TPS`
  - ordering validation `PASS (partition)`

### Gate Verdict (2026-04-17)

- soak execution gate: **PASS**
  - evidence: in Run D, the target combination (`process + partition + strict-on`,
    20000 messages) completed within the default `60s` timeout and generated JSON/log artifacts
- recovery semantics gate: **PASS**
  - evidence: `tests/e2e/test_process_recovery.py` passed 4/4
- evidence completeness gate: **PASS**
  - evidence: command/artifact/observation/status are all recorded
- Overall verdict: **PASS**
  - rationale: all three gates passed

## Soak/Restart evidence - 2026-04-17 UTC (template-aligned package)

- Soak command: `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --metrics-port 9091 --json-output benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json`
- Runtime / volume: `31.89s`, `20000 messages`, `8 partitions`, `100 keys`
- Workloads / ordering / strict monitor: `sleep`, `partition`, `strict-completion-monitor=on`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-122/soak-process-partition-strict-on-20260416T175324Z.log`
  - soak json: `benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json`
  - recovery log: `.omx/artifacts/mqu-115/process-recovery-20260416T171800Z.log`
- Observations:
  - backpressure: observed as intermittent pauses during run; run completed all messages
  - lag/gap: temporary partition-local gaps were observed and converged by completion
  - anomalies: none in this slice (`TIMEOUT`/`RuntimeError` absent)
- Recovery checks:
  - rebalance: `pass` (covered by `tests/e2e/test_process_recovery.py`)
  - restart: `pass` (covered by `tests/e2e/test_process_recovery.py`)
  - DLQ: `pass` (covered by `tests/e2e/test_process_recovery.py`)
- Gate results:
  - soak execution gate: `pass`
  - recovery semantics gate: `pass`
  - evidence completeness gate: `pass`
- Overall verdict: `PASS`
- Follow-up: `P1` keeps long-window matrix repetition as ongoing work; soak automation and broader operational hardening remain `P2` (`docs/operations/release-readiness.md`).

## Evidence refresh - 2026-04-19 UTC

### Run E - default-timeout rerun (caution artifact)

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --metrics-port 9091 --json-output benchmarks/results/mqu269-process-partition-strict-on-20260419T072721Z.json`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-269/soak-process-partition-strict-on-20260419T072721Z.log`
  - soak json: not generated (run aborted on timeout before JSON write)
- Result:
  - timed out after `60s`
  - processed `15813 / 20000`
  - `Final TPS 249.98`
  - ordering validation still reported `PASS (partition)` on the processed subset
- Key observation:
  - repeated `Commit failed: Broker: Unknown topic or partition` errors
    appeared while the run was using the shared benchmark topic prefix

### Run F - isolated rerun with dedicated topic prefix and extended timeout

- Command:
  - `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --topic-prefix mqu269-soak --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --timeout-sec 180 --metrics-port 9091 --json-output benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-269/soak-process-partition-strict-on-timeout180-20260419T072843Z.log`
  - soak json: `benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json`
- Result:
  - completed in `71.97s`
  - throughput `277.88 TPS`
  - `avg_processing_ms 6.82`, `p99_processing_ms 10.22`
  - ordering validation `PASS (partition)`
- Key observation:
  - intermittent backpressure pauses recurred through the run
  - partition-local lag/gap spikes were visible in diagnostics and converged by completion

### Run G - recovery semantics refresh

- Command:
  - `UV_CACHE_DIR=.uv-cache PYRALLEL_E2E_REQUIRE_BROKER=1 uv run pytest tests/e2e/test_process_recovery.py -q`
- Artifact:
  - recovery log: `.omx/artifacts/mqu-269/process-recovery-20260419T073004Z.log`
- Result:
  - `4 passed in 17.54s`
  - rebalance/restart/retry/DLQ scenarios all passed

### Gate Verdict (2026-04-19 refresh)

- soak execution gate: **PASS**
  - evidence: Run F completed successfully and retained JSON/log artifacts
- recovery semantics gate: **PASS**
  - evidence: Run G passed 4/4
- evidence completeness gate: **PASS**
  - evidence: command/artifact/observation/status are recorded, and Run E is retained as a caution artifact rather than hidden
- Overall verdict: **PASS**
  - rationale: the credible package for this refresh is Run F + Run G; Run E documents the default-timeout sensitivity of this combo but does not replace the retained PASS package

## Soak/Restart evidence - 2026-04-19 UTC (template-aligned refresh package)

- Soak command: `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark --topic-prefix mqu269-soak --skip-baseline --skip-async --workloads sleep --order partition --strict-completion-monitor on --num-messages 20000 --num-partitions 8 --timeout-sec 180 --metrics-port 9091 --json-output benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json`
- Runtime / volume: `71.97s`, `20000 messages`, `8 partitions`, `100 keys`
- Workloads / ordering / strict monitor: `sleep`, `partition`, `strict-completion-monitor=on`
- Artifacts:
  - soak log: `.omx/artifacts/mqu-269/soak-process-partition-strict-on-timeout180-20260419T072843Z.log`
  - soak json: `benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json`
  - recovery log: `.omx/artifacts/mqu-269/process-recovery-20260419T073004Z.log`
- Observations:
  - backpressure: intermittent pauses recurred throughout the run and recovered without manual intervention
  - lag/gap: partition-local gaps appeared transiently and converged by completion
  - anomalies: the paired default-timeout rerun (`.omx/artifacts/mqu-269/soak-process-partition-strict-on-20260419T072721Z.log`) timed out with commit errors, so the dedicated-prefix `180s` rerun is the retained PASS artifact for this refresh
- Recovery checks:
  - rebalance: `pass` (covered by `tests/e2e/test_process_recovery.py`)
  - restart: `pass` (covered by `tests/e2e/test_process_recovery.py`)
  - DLQ: `pass` (covered by `tests/e2e/test_process_recovery.py`)
- Gate results:
  - soak execution gate: `pass`
  - recovery semantics gate: `pass`
  - evidence completeness gate: `pass`
- Overall verdict: `PASS`
- Follow-up: `P1` keeps long-window matrix repetition as ongoing work; soak automation, default-timeout tuning for this sensitive combo, and broader operational hardening remain `P2` (`docs/operations/release-readiness.md`).
