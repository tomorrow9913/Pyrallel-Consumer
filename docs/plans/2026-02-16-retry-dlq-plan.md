# Retry + DLQ Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add configurable per-item retries with backoff and DLQ publishing before committing failed offsets.

**Architecture:** Retries live inside execution engines (async/process) using tenacity-like backoff; BrokerPoller publishes final failures to a DLQ topic (suffix from config) preserving key/value and failure headers, then commits. WorkManager remains unchanged.

**Tech Stack:** Python 3.12, asyncio, multiprocessing, confluent-kafka, pydantic-settings, pytest, tenacity.

---

### Task 1: Add configuration fields

**Files:**
- Modify: `pyrallel_consumer/config.py`
- Modify: `pyproject.toml` (if needed for tenacity pin change; likely no change)
- Test: `tests/` config parsing (add new tests)

**Step 1: Write the failing test**
- Add config parsing test to `tests/unit/test_config.py` (or create) covering defaults for new fields: `max_retries=3`, `retry_backoff_ms=1000`, `exponential_backoff=True`, `max_retry_backoff_ms=30000`, `retry_jitter_ms=200`, `dlq_enabled=True`, `dlq_topic_suffix='.dlq'`.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/test_config.py -k retry_dlq -v`
- Expected: fails because fields undefined.

**Step 3: Write minimal implementation**
- Add fields to `ExecutionConfig` and `KafkaConfig` with defaults; ensure `dump_to_rdkafka` excludes the new app-level fields (don’t leak to rdkafka).

**Step 4: Run test to verify it passes**
- Run: `pytest tests/unit/test_config.py -k retry_dlq -v`
- Expected: pass.

**Step 5: Commit**
- `git add pyrallel_consumer/config.py tests/unit/test_config.py`
- `git commit -m "feat(config): add retry and dlq flags"`

### Task 2: Extend CompletionEvent for attempts

**Files:**
- Modify: `pyrallel_consumer/dto.py`
- Tests: `tests/unit/execution_plane/test_execution_engine_contract.py`, async/process engine tests

**Step 1: Write the failing test**
- In engine contract test, assert `CompletionEvent` has `attempt` attribute and reflects attempt counts (success and failure paths).

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/execution_plane/test_execution_engine_contract.py -k attempt -v`

**Step 3: Write minimal implementation**
- Add `attempt: int` to `CompletionEvent`; update existing code references if needed (constructor calls).

**Step 4: Run test to verify it passes**
- Run: `pytest tests/unit/execution_plane/test_execution_engine_contract.py -k attempt -v`

**Step 5: Commit**
- `git add pyrallel_consumer/dto.py tests/unit/execution_plane/test_execution_engine_contract.py`
- `git commit -m "feat(dto): track attempt on completion"`

### Task 3: Add retry/backoff in AsyncExecutionEngine

**Files:**
- Modify: `pyrallel_consumer/execution_plane/async_engine.py`
- Tests: `tests/unit/execution_plane/test_async_execution_engine.py`

**Step 1: Write the failing test**
- Add tests: success on first attempt; success on retry; failure after `max_retries`; timeout counts as attempt. Assert `CompletionEvent.attempt` and error text.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/execution_plane/test_async_execution_engine.py -k retry -v`

**Step 3: Write minimal implementation**
- Wrap worker call with retry loop using config fields; backoff computation with exponential + cap + jitter; update completion event with attempt count; ensure semaphore release per attempt.

**Step 4: Run test to verify it passes**
- Run: `pytest tests/unit/execution_plane/test_async_execution_engine.py -k retry -v`

**Step 5: Commit**
- `git add pyrallel_consumer/execution_plane/async_engine.py tests/unit/execution_plane/test_async_execution_engine.py`
- `git commit -m "feat(async-engine): add configurable retries"`

### Task 4: Add retry/backoff in ProcessExecutionEngine

**Files:**
- Modify: `pyrallel_consumer/execution_plane/process_engine.py`
- Tests: `tests/unit/execution_plane/test_process_engine_batching.py`

**Step 1: Write the failing test**
- Add tests mirroring async: success on retry, final failure includes attempt, timeout equivalent (simulate via worker raising after sleep), ensure in-flight count decrements.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/unit/execution_plane/test_process_engine_batching.py -k retry -v`

**Step 3: Write minimal implementation**
- Implement retry loop inside worker process path; include backoff (sleep) before re-running worker; on final failure emit attempt=max_retries.

**Step 4: Run test to verify it passes**
- Run: `pytest tests/unit/execution_plane/test_process_engine_batching.py -k retry -v`

**Step 5: Commit**
- `git add pyrallel_consumer/execution_plane/process_engine.py tests/unit/execution_plane/test_process_engine_batching.py`
- `git commit -m "feat(process-engine): add configurable retries"`

### Task 5: BrokerPoller DLQ publishing

**Files:**
- Modify: `pyrallel_consumer/control_plane/broker_poller.py`
- Tests: `tests/integration/test_broker_poller_integration.py` or new unit with mocked producer

**Step 1: Write the failing test**
- Add test ensuring: on FAILURE with retries exhausted and `dlq_enabled=True`, producer.produce called with topic+suffix, original key/value, headers (`x-error-reason`, `x-retry-attempt`, `source-topic`, `partition`, `offset`, `epoch`); offset committed only after publish succeeds; publish failure triggers retry and prevents commit.

**Step 2: Run test to verify it fails**
- Run: `pytest tests/integration/test_broker_poller_integration.py -k dlq -v`

**Step 3: Write minimal implementation**
- Add `_publish_to_dlq` helper using producer; apply retry/backoff; gate commit on publish success; honor `dlq_enabled=False` path matching current behavior.

**Step 4: Run test to verify it passes**
- Run: `pytest tests/integration/test_broker_poller_integration.py -k dlq -v`

**Step 5: Commit**
- `git add pyrallel_consumer/control_plane/broker_poller.py tests/integration/test_broker_poller_integration.py`
- `git commit -m "feat(control-plane): publish failures to dlq before commit"`

### Task 6: Wiring and cleanup

**Files:**
- Modify: `pyrallel_consumer/execution_plane/engine_factory.py` (ensure config fields propagated)
- Modify: any shared helpers if needed

**Step 1: Sanity tests**
- Run: `pytest tests/unit/execution_plane/test_execution_engine_contract.py -v`
- Run: `pytest tests/integration/test_broker_poller_integration.py -v`

**Step 2: Adjust code if broken**
- Fix any wiring issues; re-run relevant tests.

**Step 3: Commit**
- `git add pyrallel_consumer/execution_plane/engine_factory.py`
- `git commit -m "chore: wire retry config through factory"`

### Task 7: Full suite + docs

**Files:**
- Modify: `README.md` (document retry/DLQ knobs)
- Modify: `prd_dev.md` if configuration schema referenced

**Step 1: Run full tests**
- `pytest`

**Step 2: Update docs**
- Document new config fields and behavior; note default DLQ headers.

**Step 3: Commit**
- `git add README.md prd_dev.md`
- `git commit -m "docs: document retry and dlq options"`

### Task 8: Final verification

**Step 1: Pre-commit**
- Run: `pre-commit run --all-files`

**Step 2: Summary**
- Ensure no production asserts, logging uses percent formatting, DLQ enabled path tested.

**Step 3: Commit**
- If further changes from lint, commit: `git commit -am "chore: apply lint fixes"`

---

Plan complete and saved to `docs/plans/2026-02-16-retry-dlq-plan.md`. Two execution options:
1. Subagent-Driven (this session) — dispatch fresh subagent per task with reviews.
2. Parallel Session — new session using superpowers:executing-plans.
Which approach?
