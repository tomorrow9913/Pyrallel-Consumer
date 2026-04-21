# Upgrade / Rollback Guide

This document summarizes the checks required when `Pyrallel Consumer` users
upgrade from alpha/prerelease to the latest version (or future stable), and the
rollback procedure when issues occur.

Because the project is currently in prerelease hardening, this guide emphasizes
**conservative upgrades** and **fast recovery capability**.

## 1. Scope and Principles

- Canonical support-scope policy follows `support-policy.md`.
- Release incident response/rollback operations follow the
  `Release Incident / Rollback Runbook` section in `playbooks.md`.
- The current primary support target is the **latest public prerelease**.
- Perform upgrades in verifiable stages instead of large one-shot jumps.
- On incidents, prioritize **fast rollback** for service stability before
  full root-cause analysis/retry.

## 2. Pre-upgrade Checklist

1. Record the exact current production version and configuration snapshot.
2. Review target-version changes in `CHANGELOG.md` (especially
   ordering/retry/DLQ/commit-related changes).
3. Confirm no assumptions conflict with
   `docs/operations/public-contract-v1.md`.
4. Check whether support boundaries changed in
   `docs/operations/support-policy.md`.
5. Pre-stage rollback artifacts (or lockfile) for the previous version.

## 3. Recommended Upgrade Procedure

### 3.1 Upgrade between Alpha/Prerelease versions

1. Pin and install the new version in test/staging.
2. Run baseline smoke validation.
   - consumer start/stop
   - message consume/commit flow
   - DLQ path behavior (if enabled)
3. Run a short soak with production-like settings.
4. Check for anomalies in metrics (`lag`, `gap`, `backpressure`,
   `oldest task duration`).
5. If healthy, roll out progressively to production traffic.

### 3.2 Upgrade to future stable release

- The first stable release enforces stricter contract boundaries than prerelease.
- Review release notes + support policy + public contract documents together.
- Re-evaluate previous alpha assumptions (for example, best-effort
  compatibility) separately from operations docs/SLO commitments.

## 4. Rollback Procedure

### 4.1 Rollback from user/operator perspective

1. Revert dependencies to the most recent known-good version
   (restore version pin/lockfile).
2. If new-version-only settings were introduced, revert them to values
   compatible with the prior version.
3. Restart the consumer and verify key metrics return to normal range.
4. Record rollback timestamp and root-cause summary in operations logs/release notes.

### 4.2 Rollback from maintainer/release perspective

If a bad release was published, follow the rollback rule in
`release-versioning-policy.md`.

- do not overwrite existing versions
- do not reuse existing tags
- apply `yank + forward fix`

In short, yank the problematic version and publish a corrected new version.

## 5. Minimum Post-upgrade Verification Commands (recommended)

Minimum verification commands for project maintainers:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q --ignore=tests/unit/benchmarks
UV_CACHE_DIR=.uv-cache uv run pytest tests/integration -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/e2e/test_ordering.py -q
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run mypy pyrallel_consumer
```

For user environments, running the full set may be unnecessary, but at minimum,
run the same smoke checks for message consume/commit/DLQ/metrics behavior.
