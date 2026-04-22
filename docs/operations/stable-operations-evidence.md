# Stable Operations Evidence Reference

This document is the compact reference surface for the GitHub #37 / MQU-269
deliverable. Use it during release review when you need the current
soak/restart evidence package, the fixed performance baseline policy, and the
scope boundary for what remains P1 versus post-stable hardening.

## What This Reference Covers

- where the current fixed benchmark baseline policy lives
- where the template-aligned soak/restart evidence package lives
- which commands/artifacts should be reused in future release reviews
- what remains ongoing P1 evidence collection versus P2 operational hardening

## Canonical Evidence Surfaces

### 1. Performance baseline policy

Use [`playbooks.md`](./playbooks.md) as the normative source for:

- the fixed TPS / p99 threshold table under `Release Go/No-Go Threshold`
- fail-fast criteria (`TIMEOUT`, ordering mismatch, incomplete message count,
  lag/gap violations)
- benchmark verdict rules (`PASS` / `NO-GO` from the machine evaluator, with
  `GO` used only as release-review shorthand after the evaluator passes)
- the standard benchmark command for re-measurement

Release-review benchmark command:

```bash
UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.run_parallel_benchmark \
  --skip-baseline \
  --workloads sleep,cpu,io \
  --order key_hash,partition \
  --strict-completion-monitor on \
  --num-messages 10000 \
  --num-partitions 8 \
  --log-level WARNING \
  --metrics-port 9091 \
  --json-output benchmarks/results/release-gate-<UTC>.json
```

Release-review evaluation requirement:

- Run the benchmark gate evaluator configured in the release-candidate workflow
  against the generated JSON artifacts:
  `UV_CACHE_DIR=.uv-cache uv run python -m benchmarks.release_gate --benchmark-json benchmarks/results/release-gate-<UTC-1>.json --benchmark-json benchmarks/results/release-gate-<UTC-2>.json`.
- Attach the evaluator's machine-readable verdict and reasons to the release
  review.
- **Performance release gate verdict**: comes only from the benchmark evaluator
  (`PASS` or `NO-GO`) and is release-blocking on `NO-GO`.
- **Soak/restart evidence verdict**: comes from `soak-restart-evidence.md` gates;
  a soak/restart `PASS` does not by itself approve the performance release gate.

### 2. Soak / restart evidence package

Use [`soak-restart-evidence.md`](./soak-restart-evidence.md) as the normative
source for:

- the three mandatory gates
  - soak execution gate
  - recovery semantics gate
  - evidence completeness gate
- the required evidence package template
- the latest template-aligned example package and verdict

Latest documented credible package:

- soak JSON:
  `benchmarks/results/mqu122-process-partition-strict-on-20260416T175324Z.json`
- soak log:
  `.omx/artifacts/mqu-122/soak-process-partition-strict-on-20260416T175324Z.log`
- recovery log:
  `.omx/artifacts/mqu-115/process-recovery-20260416T171800Z.log`
- verdict:
  `PASS` for soak execution gate, recovery semantics gate, and evidence
  completeness gate

Fresh recheck note (2026-04-19):

- Default-timeout rerun:
  the same `process + partition + strict-on + 20000 messages` slice timed out
  at `15813 / 20000` within the default `60s` window and produced repeated
  `UNKNOWN_TOPIC_OR_PART` commit failures while using the shared benchmark topic
  prefix.
- Pass rerun:
  repeating the slice with a dedicated topic prefix and `--timeout-sec 180`
  completed successfully.
- Artifacts:
  - fail soak log:
    `.omx/artifacts/mqu-269/soak-process-partition-strict-on-20260419T072721Z.log`
  - pass soak log:
    `.omx/artifacts/mqu-269/soak-process-partition-strict-on-timeout180-20260419T072843Z.log`
  - pass soak json:
    `benchmarks/results/mqu269-process-partition-strict-on-timeout180-20260419T072843Z.json`
  - recovery log:
    `.omx/artifacts/mqu-269/process-recovery-20260419T073004Z.log`
- Recovery proof still passed separately:
  `UV_CACHE_DIR=.uv-cache PYRALLEL_E2E_REQUIRE_BROKER=1 uv run pytest tests/e2e/test_process_recovery.py -q`
  -> `4 passed in 17.54s`
- Interpretation:
  keep the default-timeout failure as a caution artifact, but use the `180s`
  rerun as the latest repeatable evidence package for this combo.

### 3. Release-readiness scope boundary

Use [`release-readiness.md`](./release-readiness.md) for the stable promotion
checklist and the explicit scope boundary for this work:

- GitHub #37 closes the baseline policy and one template-aligned evidence
  package
- repeated long-window coverage remains P1 follow-up
- soak automation and broader operational hardening remain P2

## Reuse Procedure For Future Release Reviews

1. Re-run the fixed benchmark command from `playbooks.md` with profiling off.
2. Attach the generated JSON artifact and evaluate the run against the
   threshold table and fail-fast criteria.
3. Re-run the recovery proof suite:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/e2e/test_process_recovery.py -q
```

4. If you are making a stronger long-running stability claim, record a soak note
   using the template in `playbooks.md` and judge it with
   `soak-restart-evidence.md`.
5. When writing the release-review note, link all three surfaces:
   `playbooks.md`, `soak-restart-evidence.md`, and `release-readiness.md`.

## Interpretation Notes

- Treat the fixed threshold table as the baseline policy. Sample TPS values in
  `README*` or ad-hoc benchmark runs are supporting context, not the release gate.
- Treat the benchmark gate evaluator verdict as the performance release gate.
  Human summaries must not downgrade an evaluator `NO-GO` to `GO`.
- Treat the latest PASS package in `soak-restart-evidence.md` as the minimum
  credible soak/restart evidence trail already on record. This is separate from
  the performance release gate and cannot compensate for a threshold,
  repetition, completion, lag, or gap failure in benchmark artifacts.
- Treat the 2026-04-19 default-timeout rerun as a caution artifact and pair it
  with the `180s` PASS rerun rather than reading either log in isolation.
- Do not claim a new `GO` verdict from a single benchmark slice or a
  strict-off-only run.

## Remaining Work Boundary

- Still P1:
  repeat the soak note flow across more long-window runs and keep accumulating
  artifacts that match the template/gates.
- P2:
  dedicated soak automation, broader operational hardening, dashboards/alerts,
  and other post-stable maturity investments.

For benchmark entrypoint usage details, artifact locations, and profiling caveats,
see [`../../benchmarks/README.md`](../../benchmarks/README.md).
