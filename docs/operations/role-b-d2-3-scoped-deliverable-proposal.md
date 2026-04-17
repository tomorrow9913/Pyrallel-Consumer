# Role B D2-3 Scoped Deliverable Proposal

## Objective

Define the first Role B deliverable that is small, reversible, and test-backed,
without touching platform-core internals.

This proposal stays inside the boundary contract finalized in
[MQU-141](/MQU/issues/MQU-141):

- In scope: `benchmarks/*`, `benchmarks/tui/*`, `docs/operations/*`,
  workflow/tooling surfaces
- Out of scope: `pyrallel_consumer/control_plane/*`,
  `pyrallel_consumer/execution_plane/*`
- Shared edge (`consumer.py`, `metrics_exporter.py`) untouched in this delta

## Proposed Deliverable

Introduce a single preflight entry point for Role B lane validation so execution
does not depend on ad-hoc command memory:

- script: `scripts/role_b_preflight.py`
- capability:
  - list the canonical validation commands (`--list`)
  - run the canonical validation commands in sequence (`--run`)
  - stop immediately on first failure by default (fail-fast)

## Explicit File List

This scoped deliverable is limited to:

- `docs/operations/role-b-d2-3-scoped-deliverable-proposal.md` (this proposal)
- `scripts/role_b_preflight.py` (preflight runner)
- `tests/unit/test_role_b_preflight.py` (unit coverage for runner behavior)

## Reversibility Plan

The delta is reversible with no runtime behavior change:

1. remove `scripts/role_b_preflight.py`
2. remove `tests/unit/test_role_b_preflight.py`
3. remove this proposal doc (or keep as historical note)

No production runtime module is modified.

## Validation Commands (Canonical)

The preflight script fixes and centralizes the runnable command set:

1. `python -m pytest tests/unit/test_role_b_preflight.py -q`
2. `python -m pytest tests/unit/benchmarks/test_benchmark_runtime.py -q`
3. `python -m pytest tests/unit/test_release_policy.py -q`

When executed via script:

- list only: `uv run python -m scripts.role_b_preflight --list`
- run preflight: `uv run python -m scripts.role_b_preflight --run`

## Done Criteria for MQU-142

- scoped proposal documented with explicit file list
- preflight command entry point implemented and unit-tested
- validation commands executed with evidence posted to
  [MQU-142](/MQU/issues/MQU-142) and parent
  [MQU-140](/MQU/issues/MQU-140)
