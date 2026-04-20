# Development Verification Workflow

This document is the canonical local workflow for agents and maintainers who
need fast, repeatable validation. Prefer these commands over ad hoc shell
aliases so issue comments, release evidence, and handoff notes stay comparable.

## Tooling Baseline

- Python: 3.12+.
- Dependency manager: `uv`.
- Current local tool availability verified on 2026-04-20:
  - `uv 0.10.2`
  - `pytest 9.0.2`
  - `ruff 0.15.8`
  - `mypy 1.19.1`
  - `bandit 1.9.3`
  - `twine 6.2.0`
  - `pre-commit 4.5.1` through `uv run pre-commit`

## Setup Commands

```bash
pip install uv
uv sync
uv sync --group dev
uv run python -V
.venv/bin/python -V
```

Use `uv sync` as the tracked dependency path. `requirements.txt and dev-requirements.txt are not tracked`, so `uv pip install -r requirements.txt`
and `uv pip install -r dev-requirements.txt` are legacy guidance for this
worktree and will fail until those files are intentionally restored.

`uv sync` creates the project `.venv`. Prefer `uv run ...` for repeatable
commands, and use `.venv/bin/python` or `.venv/bin/<tool>` when a direct
interpreter/tool path is required. Do not rely on the system Python for project verification.

No Makefile, justfile, or task runner is currently tracked. Use the explicit
commands below instead of assuming `make test`, `just test`, or similar aliases
exist.

## Fast Inner Loop

Run the narrowest command that proves the current change:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/<path> -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/<path>::<test_name> -q
UV_CACHE_DIR=.uv-cache uv run ruff check <changed-files>
```

For documentation-only changes, add an asset test under `tests/unit/` whenever
the document becomes a project contract or entrypoint. Keep the test focused on
links, required commands, and durable phrases rather than prose formatting.

## Parallel Local Worktrees

Use separate git worktrees for local parallel work whenever two agents,
terminals, or verification runs may edit files, mutate `.venv`, write artifacts,
or otherwise compete for the same workspace state. Do not run parallel agents in the same worktree when more than one actor can modify files.

Recommended setup:

```bash
git worktree list
git worktree add ../Pyrallel-Consumer-<issue-or-task> <base-ref>
cd ../Pyrallel-Consumer-<issue-or-task>
uv sync --group dev
```

Run `uv sync --group dev` inside each worktree so every parallel lane gets its
own project `.venv`. Keep branch names and folder names tied to the issue or
task identifier when possible.

Every parallel worktree handoff must state the owner, issue id, branch name, base ref, and cleanup decision. The cleanup decision should be one of:

- keep: active work remains;
- merge/extract: changes must be integrated elsewhere before removal;
- remove: no useful uncommitted work remains.

Cleanup rules:

```bash
git worktree list
git -C <worktree-path> status --short
git worktree remove <worktree-path>
git worktree prune
```

Never remove a worktree with uncommitted work unless the owner explicitly
confirms that the work is disposable. Stop local servers, benchmark runs, and
watch processes before removal. After cleanup, run `git worktree list` again and
confirm stale paths are gone.

## Standard Verification Checklist

Use this order for normal code work:

```bash
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit -q --ignore=tests/unit/benchmarks
UV_CACHE_DIR=.uv-cache uv run pytest tests/unit/benchmarks -q
UV_CACHE_DIR=.uv-cache uv run pytest tests/integration -q
UV_CACHE_DIR=.uv-cache uv run ruff check .
UV_CACHE_DIR=.uv-cache uv run ruff format --check .
UV_CACHE_DIR=.uv-cache uv run mypy pyrallel_consumer
UV_CACHE_DIR=.uv-cache uv run bandit -q -lll -r pyrallel_consumer
```

Run E2E only when Kafka is available:

```bash
UV_CACHE_DIR=.uv-cache PYRALLEL_E2E_REQUIRE_BROKER=1 uv run pytest tests/e2e -q
```

Run package checks when changing packaging, releases, dependencies, or public
metadata:

```bash
UV_CACHE_DIR=.uv-cache uv build
UV_CACHE_DIR=.uv-cache uv run twine check dist/*
```

Use pre-commit before pushing broad changes:

```bash
UV_CACHE_DIR=.uv-cache uv run pre-commit run --all-files
pre-commit run --all-files
```

The bare `pre-commit run --all-files` form is valid only when the command is on
PATH, for example inside an activated environment. In this worktree, use the
`uv run pre-commit ...` form by default.

## Conditional Or Expensive Commands

- `tests/e2e`: requires a local Kafka broker. Use the Docker Compose broker
  setup from the README and set `PYRALLEL_E2E_REQUIRE_BROKER=1` when the run
  should fail instead of skip on missing broker.
- Benchmarks: use `UV_CACHE_DIR=.uv-cache uv run python -m
  benchmarks.run_parallel_benchmark --help` to inspect options. Full process
  strict-on soak runs can exceed the fast inner-loop budget.
- Full `uv run pre-commit run --all-files`: useful as a final gate, but it is
  broader than the fastest issue-level verification. Prefer targeted pytest and
  ruff first, then full pre-commit near handoff.

## Evidence Standard

When closing or handing off an issue, report:

- commands run;
- pass/fail result;
- skipped commands and concrete reason, such as missing Kafka broker;
- known unrelated failures, with file/test names.

Do not report a command as passing unless it was run in the current worktree.
