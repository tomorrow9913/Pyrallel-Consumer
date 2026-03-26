# Issue 18 Hardening Design

## Summary

This design narrows issue `#18` to the still-relevant gaps in the current repository state:

1. Keep Kafka-backed E2E trustworthy by making the existing E2E workflow re-run on PR updates and by documenting the local execution path.
2. Normalize the public configuration story by clarifying that `worker_pool_size` controls key-hash sharding width, not process concurrency.
3. Reduce `process_engine.py` change risk by extracting registry-drain and dead-worker recovery bookkeeping behind smaller helpers without changing behavior.

## Current State

- Kafka-backed E2E already exists in GitHub Actions via `.github/workflows/e2e.yml`.
- Local `tests/e2e` already skip when Kafka is unavailable.
- `ordering_mode` and `max_revoke_grace_ms` are already real, wired public knobs.
- `worker_pool_size` is still a confusing public knob because it sounds like process concurrency but is actually used for key-hash shard selection in the control plane.
- `pyrallel_consumer/execution_plane/process_engine.py` still mixes batching, worker lifecycle, registry bookkeeping, and shutdown coordination in one file.

## Chosen Approach

### 1. E2E trustworthiness

Keep the existing dedicated E2E workflow instead of merging E2E into the main `ci` workflow.

Changes:
- Add `pull_request.synchronize` to `.github/workflows/e2e.yml`.
- Add short user-facing local E2E instructions to `README.md` and `README.ko.md`.

Why:
- The Kafka-backed path already exists and works.
- This closes the real gap without duplicating workflow logic.
- It preserves the current "skip when Kafka is absent locally, fail when Kafka-backed CI is expected" split.

### 2. Public config normalization

Do not rename `worker_pool_size` in this slice. Instead, explicitly lock its meaning in docs and tests.

Changes:
- Document that `worker_pool_size` affects key-hash shard width and only matters for ordered key-hash routing.
- Keep process concurrency guidance centered on `execution.process_config.process_count`.
- Add or extend tests so the semantics are not implicit.

Why:
- A rename would create a compatibility migration and widen scope.
- The actual risk is misunderstanding, not missing plumbing.
- This is enough to make the public surface honest while keeping the branch small.

### 3. Process engine decomposition

Refactor `ProcessExecutionEngine` by extracting helper methods inside the same module first, not by splitting files yet.

Changes:
- Extract registry queue draining into one authoritative helper used by both steady-state and shutdown paths.
- Extract synthetic completion emission and dead-worker recovery decision logic into focused helpers.
- Keep external behavior and tests unchanged.

Why:
- This is the smallest safe decomposition seam.
- Existing tests already pin the behavior of `_drain_registry_events()`, `_drain_shutdown_ipc_once()`, and `_ensure_workers_alive()`.
- Pulling helpers first lowers future refactor risk without forcing a module split in one step.

## Rejected Alternatives

### Move E2E into the main `ci` workflow now

Rejected because the repository already has a dedicated Kafka-backed workflow. Duplicating it would increase maintenance surface without improving confidence.

### Rename `worker_pool_size` immediately

Rejected for this slice because it would create a public migration problem and likely require compatibility aliases. The misleading semantics can be fixed first with docs and tests.

### Split `process_engine.py` into multiple modules now

Rejected because the current tests pin internal helper behavior directly. A file split before helper extraction would add review noise and make regressions harder to isolate.

## Testing Strategy

- Workflow/docs: no behavior change in runtime code; verify with targeted file diff review.
- Config semantics: add targeted unit coverage around `worker_pool_size` meaning.
- Process engine refactor: write or extend focused unit tests around extracted helper behavior before moving code.

## Acceptance Criteria For This Slice

- Kafka-backed E2E reruns when a PR receives new commits.
- README and README.ko explain how to run `tests/e2e` locally and that Kafka absence results in skip.
- Public docs no longer imply that `worker_pool_size` controls process worker count.
- `ProcessExecutionEngine` dead-worker recovery and registry draining are delegated to smaller helpers with unit coverage preserved.
