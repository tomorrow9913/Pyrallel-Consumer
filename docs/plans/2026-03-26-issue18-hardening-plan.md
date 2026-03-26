# Issue 18 Hardening Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Close the still-open parts of issue `#18` by tightening Kafka-backed E2E workflow behavior, making public config semantics honest, and reducing `ProcessExecutionEngine` refactor risk.

**Architecture:** Keep the existing dedicated E2E workflow and improve its trigger coverage. Normalize config semantics through docs and tests instead of a breaking rename. Refactor `process_engine.py` by extracting authoritative registry-drain and dead-worker recovery helpers inside the existing module first.

**Tech Stack:** Python 3.12, pytest, GitHub Actions, multiprocessing, pydantic-settings

---

### Task 1: Lock E2E workflow and local execution path

**Files:**
- Modify: `.github/workflows/e2e.yml`
- Modify: `README.md`
- Modify: `README.ko.md`

**Step 1: Write the failing test**
- There is no executable unit test for GitHub Actions YAML in this repository.
- Instead, define a diff-based acceptance check:
  - `.github/workflows/e2e.yml` must include `synchronize` under `pull_request.types`.
  - README files must include a local E2E section with `docker compose` and `uv run pytest tests/e2e -q`.

**Step 2: Verify the gap exists**
- Run: `sed -n '1,40p' .github/workflows/e2e.yml`
- Expected: `synchronize` is missing from `pull_request.types`.

**Step 3: Implement the minimal change**
- Add `synchronize` to `.github/workflows/e2e.yml`.
- Add short local E2E instructions to `README.md` and `README.ko.md`.

**Step 4: Verify the change**
- Run: `rg -n "synchronize|tests/e2e|docker compose" .github/workflows/e2e.yml README.md README.ko.md`
- Expected: all required strings are present.

### Task 2: Make public config semantics explicit

**Files:**
- Modify: `README.md`
- Modify: `README.ko.md`
- Test: `tests/unit/control_plane/test_broker_poller.py`

**Step 1: Write the failing test**
- Add a unit test asserting that `BrokerPoller` key-hash partition routing uses `parallel_consumer.worker_pool_size`.
- Name it so the behavior is explicit, for example: `test_get_partition_index_uses_worker_pool_size_for_key_hash_shards`.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/control_plane/test_broker_poller.py -k worker_pool_size_for_key_hash_shards -q`
- Expected: FAIL because the test does not exist yet.

**Step 3: Write the minimal implementation/docs**
- Add the test.
- Adjust README wording so `worker_pool_size` is described as key-hash shard width, not process concurrency.

**Step 4: Run test to verify it passes**
- Run: `pytest tests/unit/control_plane/test_broker_poller.py -k worker_pool_size_for_key_hash_shards -q`
- Expected: PASS.

### Task 3: Extract registry draining helpers in ProcessExecutionEngine

**Files:**
- Modify: `pyrallel_consumer/execution_plane/process_engine.py`
- Test: `tests/unit/execution_plane/test_process_execution_engine.py`

**Step 1: Write the failing test**
- Add focused tests around the extracted helper boundaries before refactoring:
  - registry drain helper returns drained count and applies `_apply_registry_event`
  - shutdown IPC path reuses the same registry drain helper
  - dead-worker recovery emits the expected synthetic failure/requeue behavior through one helper

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/execution_plane/test_process_execution_engine.py -k "registry or dead_worker" -q`
- Expected: FAIL because the new tests do not exist yet.

**Step 3: Write minimal refactor**
- Extract one helper for draining registry events.
- Extract one helper for handling one dead worker's in-flight payloads.
- Keep `_ensure_workers_alive()` and `_drain_shutdown_ipc_once()` as orchestration wrappers.

**Step 4: Run targeted tests**
- Run: `pytest tests/unit/execution_plane/test_process_execution_engine.py -q`
- Expected: PASS.

### Task 4: Regressions and handoff log

**Files:**
- Modify: `GEMINI.md`

**Step 1: Update handoff log**
- Add an incremental entry describing:
  - E2E workflow trigger fix
  - local E2E docs
  - `worker_pool_size` semantics clarification
  - `process_engine.py` helper extraction

**Step 2: Run focused verification**
- Run: `pytest tests/unit/control_plane/test_broker_poller.py -q`
- Run: `pytest tests/unit/execution_plane/test_process_execution_engine.py -q`

**Step 3: Run broader verification**
- Run: `pytest tests/unit/test_config.py tests/unit/test_consumer.py tests/unit/execution_plane/test_process_engine_batching.py -q`

**Step 4: Commit**
- `git add .`
- `git commit -m "refactor(process): harden issue18 workflow and supervisor paths"`

---

Plan complete. Selected execution mode: subagent-driven in this session, with local integration and verification after each slice.
