# Release & Versioning Policy

This document is the canonical policy source for the `Pyrallel Consumer`
repository's **branch strategy**, **version bump rules**, **tag rules**, and
**PyPI publishing operations**.

## 1. Source of Truth

The package's authoritative version is always recorded manually in
`pyproject.toml`.

Principles:

- CI does not decide the initial base version (`x`, `y`, `z`) or suffix-line
  transitions.
- Subsequent increments of `aN` / `bN` / `rcN` may be automated.
- For stable PRs targeting `main`, automatic bumping may be allowed only for
  patch-only changes (`z`) with no `x`/`y` changes.
- Entry points into suffix lines (`a1`, `b1`, `rc1`) and transition timing are
  human decisions in PR/maintainer actions.
- CI/CD validates whether branch/version/tag combinations follow policy.

Rationale:

- Deciding which of `x`, `y`, `z` to bump requires understanding change impact
  and release intent.
- In this repository's current operating model, PR-driven manual bumps are safer
  than dynamic/SCM-derived versioning.

## 2. Canonical Branch Flow

```text
feat/* -> develop -> release/x.y -> main
                      ^              \
                      |               -> hotfix/x.y.z -> main + develop
                      +--------------------------------/
```

Role of each branch:

- `feat/*`
  - Starting point for all feature/bugfix work
  - Not directly published
- `develop`
  - Integration/hardening branch
  - Always on alpha line
- `release/x.y`
  - Stabilization branch for a specific release line
  - Only beta/RC allowed
- `main`
  - Stable-release-only branch
  - Only stable versions allowed
- `hotfix/x.y.z`
  - Emergency stable patch branch cut from `main`

## 3. Version Bump Rules

### 3.1 `feat/*`

- Do not finalize version policy on feature branches.
- Apply final version bump in the PR to the target branch.

### 3.2 `develop`

**Every PR merged into `develop` must increment the alpha version.**

Examples:

- `0.3.0a7` -> `0.3.0a8`
- `0.4.0a1` -> `0.4.0a2`

Purpose of this rule:

- Assign a unique identifier to every merged develop build
- Enable unambiguous communication about which build failed in QA/ops/issue tracking

Automation boundary:

- Entering `a1` is a human decision.
- Once already on the alpha line, `aN -> a(N+1)` may be automated.

### 3.3 `release/x.y`

For typical stabilization PRs on release branches, increment the beta line.

Examples:

- branch cut: `0.3.0b1`
- stabilization PR: `0.3.0b2`
- stabilization PR: `0.3.0b3`

Automation boundary:

- Entering `b1` is a human decision.
- Once already on beta line, `bN -> b(N+1)` may be automated.

### 3.4 RC promotion

When maintainers run a **manual release action** to promote to a release
candidate, enter the `rc` line.

Example:

- `0.3.0b3` -> `0.3.0rc1`

After the first RC, only increment the RC line within the same release cycle.

Examples:

- `0.3.0rc1` -> `0.3.0rc2`
- `0.3.0rc2` -> `0.3.0rc3`

That is:

- `b*` -> manual action -> `rc1`
- then release-cycle bumps stay on `rcN`

Automation boundary:

- Entering `rc1` is a human decision.
- Once already on RC line, `rcN -> rc(N+1)` may be automated.

### 3.5 `main`

`main` is stable-only.

Examples:

- `0.3.0`
- `0.3.1`
- `0.4.0`

Automation boundary:

- The start of a stable line (`0.3.0`, `0.4.0`) is a human decision.
- For PRs into `main`, if `x` and `y` are unchanged and only patch changes
  are intended, automation may allow `z -> z+1`.
- So stable patch increments such as `0.3.1 -> 0.3.2` may be automated,
  while `0.3.x -> 0.4.0` or prerelease -> stable transitions remain
  human decisions.

### 3.6 `hotfix/x.y.z`

Hotfix branches use stable patch versions only.

Examples:

- `0.3.0` -> hotfix -> `0.3.1`
- `0.3.1` -> hotfix -> `0.3.2`

## 4. Tag Policy

Canonical tags always follow this format:

- `v0.3.0a8`
- `v0.3.0b2`
- `v0.3.0rc1`
- `v0.3.0`
- `v0.3.1`

Rules:

- Tag must be exactly `v<exact pyproject.toml version>`
- Alternate grammar is forbidden
  - Example: `v0.3.0-a.1` is forbidden
- Published tags are immutable

## 5. CI/CD Responsibilities

### V1 scope

Automation implemented in V1:

- branch/version validator
- manual `release-verify.yml`
- protected `publish-pypi.yml`

Intentionally deferred in V1:

- merge-time auto bump on `develop`
- merge-time auto bump on `release/*`
- patch auto bump on `main`

So V1 covers **validation + publish of validated artifacts**, while write-back
automation that mutates merge results is postponed to phase 2.

## 5.1 Continuous CI

Baseline CI is responsible for:

- lint / type / security / tests / build / twine check
- branch-appropriate version suffix validation
- no publishing

Branch/version validation:

- PR into `develop` -> version must be `aN`
- PR into `release/x.y` -> version must be `bN` or `rcN`
- PR into `main` -> version must be stable
- PR into `hotfix/*` -> version must be stable patch
- PR flow gate -> PRs into `main` must originate from `release/*` or
  `hotfix/*` (`feat/* -> main` direct PR blocked)

Recommended automation boundary:

- `develop`: if already on alpha line, automation may increment `aN`
- `release/x.y`: if already on beta line, automation may increment `bN`
- `release/x.y`: if already on RC line, automation may increment `rcN`
- `main`: if `x` and `y` stay unchanged and merge is patch-only,
  automation may increment `z`
- CI must not auto-decide first entry into `a1`, `b1`, `rc1`, or a new stable
  major/minor line

## 5.2 Release Verify

A separate manual workflow runs:

- fresh checkout
- fresh build
- artifact validation
- `twine check`
- smoke install/import
- branch/tag/version preflight

## 5.3 Publish

PyPI publish runs in a separate protected workflow.

Policy:

- `develop`: real PyPI publish is disallowed by default
- `release/x.y`: prerelease publish only
- `main`: stable publish only
- `hotfix/*`: stable patch publish only

Recommended auth:

- primary: PyPI Trusted Publishing (OIDC)
- exception: break-glass token fallback

## 6. Operator Flow

### Alpha

1. Start work on `feat/*`
2. Reflect alpha bump in PR to `develop`
3. CI validates the `aN` rule
4. After merge, that develop build has a unique alpha version

### Beta

1. Cut `release/x.y` from `develop`
2. Enter `x.y.0b1`
3. Increment `bN` on each release-branch PR

### RC

1. Maintainer runs manual release action
2. `bN` -> `rc1`
3. In the same cycle, only increment `rcN`

### Stable

1. Reflect release-ready state into `main`
2. Set stable version
3. Create canonical tag
4. Run protected publish workflow
5. Merge results back into `develop`
6. Immediately bump `develop` to next alpha

V1 note:

- Next alpha bump on `develop` is still applied manually in PRs.
- Merge-time auto-bump is not implemented yet.

### Hotfix

1. Cut `hotfix/x.y.z` from `main`
2. Apply stable patch bump
3. verify + publish
4. Merge back to `main` + `develop`
5. If needed, explicitly decide whether to backport to active `release/x.y`

## 7. Where Policy Lives

Policy is intentionally split across the following locations.

### `pyproject.toml`

- record exact current version only
- no policy prose

### `README.md`, `README.ko.md`

- public-facing short summary
- branch flow
- suffix meaning
- alpha bump on every merge to `develop`
- `main` stable-only

### `docs/operations/release-versioning-policy.md`

- this document
- canonical policy prose source

### `docs/operations/release-readiness.md`

- release-gate checklist
- references this policy instead of duplicating full policy prose

### `.github/workflows/*.yml`

- executable enforcement
- branch/version/tag validation
- artifact verification
- publish protection

### `CHANGELOG.md`

- release history only

## 8. Rollback Rule

If an incorrect release is published to PyPI:

- do not overwrite existing version
- do not reuse existing tag
- use `yank + forward fix`

In other words, yank the bad version and publish a corrected new version.

For step-by-step operator upgrade/rollback procedures, see
`docs/operations/upgrade-rollback-guide.md`.
