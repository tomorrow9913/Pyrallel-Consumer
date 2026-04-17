# Support / Compatibility Policy

This document defines the support posture for `Pyrallel Consumer` across both
the current prerelease phase and the `1.0.0` stable launch surface.

The goal is to make release-line support, compatibility boundaries, and
security response scope explicit so operators can decide whether to upgrade,
hold, or rollback.

## Current Release Status

- The project is currently published as a **prerelease / alpha** package.
- Support commitments are intentionally conservative until stable promotion.
- This document already defines the stable-launch support matrix so it can be
  applied without policy re-interpretation at `1.0.0`.

See also:

- `README.md`
- `README.ko.md`
- `SECURITY.md`
- `docs/operations/release-readiness.md`
- `docs/operations/upgrade-rollback-guide.md`
- `docs/operations/playbooks.md`

## Release-Line Support Matrix

### While prerelease is the only published line (current)

| Release line | Status | Notes |
| --- | --- | --- |
| latest prerelease | Active support | Main triage target during prerelease hardening |
| older prerelease builds | Best effort | May be asked to reproduce on latest prerelease |

### After stable launch (effective once `1.0.0` ships)

| Release line | Status | Notes |
| --- | --- | --- |
| latest stable minor | Active support | Primary support and regression triage target |
| previous stable minor | Security-fix-only | Critical/security fixes only; non-critical issues may be deferred |
| prerelease builds newer than latest stable | Best effort | Preview channel, no stable SLA guarantees |
| older prerelease builds | Best effort | Outside primary support target |

## Python Support Policy

### Supported runtime floor

- `pyrallel-consumer` currently requires **Python `>=3.12`**
  (`pyproject.toml`).

### Actively advertised versions

- Current package classifiers advertise:
  - Python `3.12`
  - Python `3.13`

### Support meaning

- **Supported** means:
  - package metadata allows installation
  - CI currently exercises the relevant test/quality gates on that version
    where configured
  - regressions on those versions should be treated as in-scope issues
- **Not supported yet** means:
  - no compatibility commitment is made
  - failures may still be accepted as enhancement / future-work items rather
    than release blockers

### Current boundary

| Python version | Status | Notes |
| --- | --- | --- |
| 3.12 | Supported | Included in package classifiers and CI matrix |
| 3.13 | Supported | Included in package classifiers and CI matrix |
| < 3.12 | Not supported | Outside current metadata contract |

## Kafka Support Policy

### What is actively verified

The currently verified Kafka path is the repository's own local Docker /
GitHub Actions-backed Kafka flow used by the E2E suite and monitoring smoke.

That means the strongest current confidence exists for:

- the Kafka broker path exercised by `.github/e2e.compose.yml`
- the `confluent-kafka` client range allowed by `pyproject.toml`
- runtime behavior covered by unit, integration, and Kafka-backed E2E tests

### What is best-effort

The following are **best-effort** until a broader matrix is documented and
automated:

- broker distributions not exercised by the repository's Docker / CI path
- older client / broker combinations outside the currently tested path
- vendor-specific Kafka-compatible environments with behavior that differs from
  the CI-backed baseline

### Current boundary

| Kafka surface | Status | Notes |
| --- | --- | --- |
| Local Docker / CI-backed Kafka path used by repo E2E | Supported / verified | This is the primary compatibility reference today |
| Other broker distributions | Best effort | No broader certified matrix published yet |
| Older client / broker combinations | Best effort | No compatibility commitment yet |

## Support Expectations

### Bug reports that are in scope

- Reproductions on Python `3.12` / `3.13`
- Reproductions against the Kafka path the repository actively tests
- Regressions against the active release line in the matrix above

### Bug reports that may be downgraded to best-effort

- Reports against older prerelease releases
- Reports against non-active stable lines outside security-fix-only scope
- Reports against unverified broker distributions or older compatibility stacks
- Requests that assume stable/SLA-grade compatibility guarantees before stable
  promotion

## Deprecation Policy

Until `1.0.0`, public-surface changes may still happen as part of hardening
work. The current policy is:

- avoid unnecessary breaking changes during alpha hardening
- document user-visible contract changes in README / operations docs /
  changelog when they occur
- tighten the policy further once the project graduates to stable

Before stable release begins, users should assume:

- the core direction is intentional
- the actively documented runtime / operational contract is the best current
  source of truth
- exact compatibility guarantees are still narrower than they will be after
  stable promotion

After stable release begins, users should assume:

- stable release lines follow the support matrix above
- non-stable lines remain best-effort unless explicitly promoted
- security handling follows `SECURITY.md` without requiring ad-hoc policy changes

## Maintainer Guidance

Before widening this support policy, maintainers should update all of the
following together:

1. package metadata (`pyproject.toml`)
2. CI / verification coverage
3. README support summary
4. `SECURITY.md` supported-version language
5. `docs/operations/release-readiness.md`

This keeps the support statement aligned with real verification evidence rather
than aspirational claims.
