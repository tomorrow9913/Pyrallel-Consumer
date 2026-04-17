# Stable Release Readiness Checklist

This checklist organizes the items that must be verified before promoting
`Pyrallel Consumer` from alpha/prerelease to a commercially usable stable release,
with priority levels.

The branch strategy, version bump rules, tag grammar, and PyPI publishing policy
are canonically defined in `docs/operations/release-versioning-policy.md`.
This document verifies whether that policy is fully closed for stable promotion.

## How To Use

- `P0`: Must be closed before declaring stable
- `P1`: Should be closed as much as possible before stable
- `P2`: Post-stable maturity improvements

For each item, review all three dimensions below.

- **What**: what must be implemented or verified
- **Evidence**: what proves completion
- **Owner hint**: where the work is mainly expected (docs/code area)

## P0: Required Before Stable Promotion

- [x] **Remove alpha metadata**
  - What: `version`, classifiers, and README release policy must match a stable state.
  - Evidence: alpha classifier removed in `pyproject.toml`, stable version (`1.0.0`) applied, and README release policy updated.
  - Evidence link: GitHub [#33](https://github.com/tomorrow9913/Pyrallel-Consumer/issues/33)
  - Owner hint: `pyproject.toml`, `README.md`, `README.ko.md`

- [x] **Freeze core public contract**
  - What: define stable contract defaults for ordering guidance, DLQ payload default,
    commit public surface, and rebalance state strategy.
  - Evidence: `docs/operations/public-contract-v1.md` defines freeze scope/exception
    policy, and `tests/unit/test_public_contract_v1.py` locks regression.
  - Owner hint: `README*`, `docs/operations/*`, `tests/unit/*`

- [x] **Include process mode in real-broker E2E**
  - What: validate core paths (ordering, retry, DLQ, rebalance/restart) for both
    async/process engines against a real Kafka broker.
  - Evidence: process-mode real-broker E2E passes in
    `tests/e2e/test_ordering.py` and `tests/e2e/test_process_recovery.py`.
  - Owner hint: `tests/e2e/`, `.github/workflows/e2e.yml`

- [x] **Fix broker-backed E2E as a release-blocking gate**
  - What: release verification must fail if broker-backed E2E is not green.
  - Evidence: `.github/workflows/release-verify.yml` contains
    `Run broker-backed E2E tests (release gate)`, and
    `.github/workflows/e2e.yml` / `tests/e2e/*` run on Kafka metadata readiness
    plus strict broker mode in CI (`PYRALLEL_E2E_REQUIRE_BROKER=1`), not simple
    socket-port checks.
  - Owner hint: `.github/workflows/release-verify.yml`, `.github/workflows/e2e.yml`, `tests/e2e/*`

- [ ] **Strengthen CI quality gates**
  - What: at minimum, lint/type/security/build/artifact checks should run
    automatically on PR and push.
  - Evidence: GitHub Actions runs `ruff`, `mypy`, `bandit`, `uv build`, and `twine check`.
  - Owner hint: `.github/workflows/ci.yml`, `pyproject.toml`

- [ ] **Standardize release artifact validation**
  - What: clearly define which artifacts are validated so stale artifacts cannot
    contaminate release decisions.
  - Evidence: release build procedure is documented and `twine check` targets only fresh artifacts.
  - Owner hint: `CHANGELOG.md`, release workflow/commands, `GEMINI.md`

- [x] **Document security contact path and responsibility**
  - What: document private security reporting channel and response expectations,
    separate from public issues.
  - Evidence: `SECURITY.md` defines private reporting channels and response windows, and `README*` links to `SECURITY.md` from the release support surface.
  - Owner hint: `SECURITY.md`, `README*`

## P1: Recommended Before Stable

- [ ] **Long-running soak / restart recovery validation**
  - What: validate backpressure, rebalance, worker recycle, and post-restart
    offset/DLQ behavior under long-running processing.
  - Evidence: soak scenario docs + results, with repeatable commands/workflow.
    Pass/fail follows the fixed gates in `docs/operations/soak-restart-evidence.md`
    (soak execution / recovery semantics / evidence completeness).
  - Scope boundary: issue #37 closes the baseline policy and one template-aligned
    evidence package; remaining P1 work is repeated long-window coverage, while
    soak automation and broader operational hardening stay in P2.
  - Owner hint: `benchmarks/`, `tests/e2e/`, `docs/operations/playbooks.md`

- [x] **Document support scope and compatibility policy**
  - What: define supported Python versions, Kafka broker/client compatibility
    boundaries, and deprecation policy.
  - Evidence: `docs/operations/support-policy.md` defines compatibility boundaries
    plus prerelease/stable release-line support matrix, and `README*` links to it directly.
  - Owner hint: `README*`, `SECURITY.md`, `docs/operations/support-policy.md`

- [x] **Add upgrade/rollback guide**
  - What: guide alpha users or previous-version users through configuration/behavior
    differences when upgrading to stable.
  - Evidence: `docs/operations/upgrade-rollback-guide.md` is linked from
    `README*` / docs index, and `docs/operations/playbooks.md` documents
    release-incident rollback operations for operators.
  - Owner hint: `docs/operations/*`, `CHANGELOG.md`, `README*`

- [x] **Fix performance regression baseline**
  - What: lock workload-specific TPS/p99 ranges as release-review inputs,
    not advisory notes.
  - Evidence: `docs/operations/playbooks.md` documents fixed threshold table
    (mode/workload/ordering TPS floor + p99 ceiling), fail-fast criteria, and
    verdict procedure.
  - Owner hint: `docs/operations/playbooks.md`, `benchmarks/README.md`

## P2: Post-Stable Maturity Improvements

- [ ] **Release automation**
  - What: automate tag-based build, artifact validation, publish, and release-note generation.
  - Evidence: repeatable release workflow works without manual publish.
  - Owner hint: `.github/workflows/`

- [ ] **Support/operations SLO definition**
  - What: define response targets, security patch cadence, and support expectations.
  - Evidence: operations docs or support policy documentation.
  - Owner hint: `docs/operations/*`

- [ ] **Additional observability assets**
  - What: provide canonical dashboard, alert-rule examples, and runbook drill results.
  - Evidence: monitoring assets are maintained together with operations docs.
  - Owner hint: `monitoring/`, `docs/operations/*`

### Current `type: ignore` inventory (reviewed)

Current runtime/source `type: ignore` usage mostly documents either
**third-party stub limitations** or **Kafka Python typing boundaries**.

- `pyrallel_consumer/control_plane/offset_tracker.py`
  - correction for untyped imports from `cachetools`, `sortedcontainers`
- `pyrallel_consumer/control_plane/broker_rebalance_support.py`
  - correction for boundary where `confluent_kafka` stubs do not model
    `KafkaTopicPartition(..., metadata=...)`
- `pyrallel_consumer/control_plane/broker_poller.py`
  - correction for Kafka headers typing mismatch
- `pyrallel_consumer/execution_plane/process_engine.py`
  - correction for untyped import from `msgpack`

In the 2026-04-10 cleanup, private-attribute access on `asyncio.Queue` in
`pyrallel_consumer/control_plane/work_queue_topology.py` and multiprocessing
queue payload put boundaries in `pyrallel_consumer/execution_plane/process_engine.py`
were replaced with `Protocol` and accurate queue generic annotations, removing
those from production `type: ignore` targets.

Additional `type: ignore` remains in tests for white-box/private-attribute checks.
From a stable-readiness perspective, the baseline is to inventory and justify
runtime/source ignores first. Before stable, reduce this list where possible;
if entries remain, maintain justification at the same rigor level.

## Recommended Verification Commands

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q --ignore=tests/unit/benchmarks
UV_CACHE_DIR=.uv-cache uv run pytest tests/integration -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/benchmarks -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/e2e -q
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run mypy pyrallel_consumer
UV_CACHE_DIR=.uv-cache uv run bandit -q -lll -r pyrallel_consumer
UV_CACHE_DIR=.uv-cache uv build
release_artifacts=()
while IFS= read -r line; do release_artifacts+=("$line"); done < <(UV_CACHE_DIR=.uv-cache uv run python scripts/release_policy.py resolve-artifacts)
UV_CACHE_DIR=.uv-cache uv run twine check "${release_artifacts[@]}"
```

### P0/E2E Gate (broker-backed)

- Gate workflow: `Run broker-backed E2E tests (release gate)` stage in `release-verify`
- Fresh evidence for release review:
  - `release-verify` workflow run URL
  - `e2e` workflow run URL (same SHA or release-candidate SHA)
  - `tests/e2e` logs (especially ordering/retry/DLQ/rebalance/restart scenario passes)

## Current Assessment Snapshot

- The current state is a **stable-line posture (`1.0.0`) with broker-backed
  release gates**.
- Beyond `key_hash`/`partition` ordering, real-broker E2E evidence covers
  process-mode retry, DLQ, in-flight rebalance, and restart/offset continuity.
- The remaining gaps are mostly P1/P2 operational maturity items, not alpha
  metadata alignment.

## Type Ignore Inventory Snapshot

As of 2026-04-10, production `type: ignore` usage mostly falls into two groups.

- **third-party stub gap**
  - `pyrallel_consumer/control_plane/offset_tracker.py`
  - `pyrallel_consumer/execution_plane/process_engine.py`
  - reason: typing info from `cachetools`, `sortedcontainers`, and `msgpack`
    does not fully match runtime usage boundaries
- **confluent-kafka call-site stub gap**
  - `pyrallel_consumer/control_plane/broker_rebalance_support.py`
  - `pyrallel_consumer/control_plane/broker_poller.py`
  - reason: `KafkaTopicPartition(metadata=...)` and producer headers typing are
    valid at runtime, but currently modeled too narrowly in stubs

So the current inventory is closer to **documented remaining stub limitations**
rather than uncontrolled ignore accumulation. Before stable transition, review
this list again; in the current phase, prioritize reducing entries where possible,
and keep equivalent-quality justification where entries must remain.
