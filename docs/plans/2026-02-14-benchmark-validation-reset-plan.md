# Benchmark Validation & Kafka Reset Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Guarantee the repo is clean (pytest + pre-commit), add a Confluent AdminClient-based topic/group reset helper, and integrate it into the benchmark harness so full baseline/async/process runs start from fresh Kafka state.

**Architecture:** Validation runs precede code changes. A reusable helper in `benchmarks/` issues delete/create operations via AdminClient with retries and logging. The benchmark CLI invokes the helper before executing producer/baseline/async/process runs, with an opt-out flag and GEMINI logging.

**Tech Stack:** Python 3.12, `confluent-kafka` AdminClient, pytest, pre-commit, uv, existing benchmark scripts.

---

### Task 1: Full Validation Pass (Pytest + Pre-commit)

**Files:**
- No code changes expected; logs go to `GEMINI.md`

**Step 1: Run full pytest suite**

```bash
uv run pytest
```

Expected: PASS. If it fails, capture the failure in `GEMINI.md`, fix code (outside this task), and rerun until green.

**Step 2: Run all pre-commit hooks**

```bash
pre-commit run --all-files
```

Expected: PASS with zero modifications. Capture result in `GEMINI.md`.

**Step 3: Record validation status**

Document both commands + outcomes in `GEMINI.md` per GEMINI rules.

### Task 2: Kafka Reset Helper Module

**Files:**
- Create: `benchmarks/kafka_admin.py`
- Modify: `pyproject.toml` (ensure no extra deps needed)
- Tests: `tests/unit/benchmarks/test_kafka_admin.py`

**Step 1: Write unit tests for helper**

Create `tests/unit/benchmarks/test_kafka_admin.py` with mocked AdminClient verifying:
- Topics delete tolerates `UNKNOWN_TOPIC_OR_PARTITION`
- Consumer group delete tolerates `GROUP_ID_NOT_FOUND`
- Retries triggered on `KafkaException` and eventually raise after retries

Use `unittest.mock` to simulate future objects.

**Step 2: Run new test to see it fail**

```bash
uv run pytest tests/unit/benchmarks/test_kafka_admin.py -v
```

Expected: FAIL (helper not implemented yet).

**Step 3: Implement helper in `benchmarks/kafka_admin.py`**

Code outline:

```python
from __future__ import annotations

import time
import logging
from dataclasses import dataclass
from typing import Iterable

from confluent_kafka.admin import AdminClient, NewTopic

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class TopicConfig:
    num_partitions: int
    replication_factor: int = 1
    configs: dict[str, str] | None = None

def reset_topics_and_groups(...):
    admin = AdminClient({"bootstrap.servers": bootstrap_servers})
    _delete_with_retries(admin.delete_topics, topics, retries, backoff)
    _delete_groups_with_retries(...)
    _create_with_retries(admin.create_topics(...))
```

Helper functions handle retries/backoff with `time.sleep(backoff * attempt)` and swallow allowed errors.

**Step 4: Re-run targeted tests**

```bash
uv run pytest tests/unit/benchmarks/test_kafka_admin.py -v
```

Expected: PASS.

### Task 3: Wire Helper Into Benchmark Harness

**Files:**
- Modify: `benchmarks/run_parallel_benchmark.py`
- Modify: `pyrparallel_consumer_test.py`
- Modify: `benchmarks/stats.py` (if needed for CLI args/output)
- Docs: `README.md`, `GEMINI.md`

**Step 1: Add CLI flag + plumbing**

In `benchmarks/run_parallel_benchmark.py` CLI parser, add `--skip-reset` (bool, default False). Determine topics/groups to reset using existing naming convention.

**Step 2: Invoke helper before producing data**

If not skipping, call `reset_topics_and_groups(...)` with bootstrap servers, topic config (partitions from CLI), and consumer group list (baseline group, Pyrallel group, process group). Log success/failure.

**Step 3: Update `pyrparallel_consumer_test.py`**

If that script orchestrates runs separately, ensure it calls the helper when `--skip-reset` not set, keeping options consistent.

**Step 4: Documentation updates**

In `README.md` benchmark section, describe automatic resets and the `--skip-reset` flag. Update `GEMINI.md` after implementation/testing.

**Step 5: Targeted tests**

Run relevant unit tests:

```bash
uv run pytest tests/unit/benchmarks/test_kafka_admin.py tests/unit/control_plane/test_work_manager.py -v
```

Ensure CLI help still works: `uv run python benchmarks/run_parallel_benchmark.py --help`.

### Task 4: Final Validation + Benchmark Dry Run

**Files:**
- `GEMINI.md` (log results)
- Benchmark result JSONs under `benchmarks/results/`

**Step 1: Re-run full pytest + pre-commit** (same commands as Task 1) to ensure no regressions.

**Step 2: Execute benchmark sequence with resets**

```bash
uv run python benchmarks/run_parallel_benchmark.py \
  --bootstrap-servers localhost:9092 \
  --num-messages 1000 \
  --num-keys 50 \
  --num-partitions 4
```

Verify it deletes/recreates topics/groups successfully and produces JSON results. If Kafka unavailable, note the blocker in `GEMINI.md`.

**Step 3: Record evidence**

Update `GEMINI.md` with validation + benchmark outcomes and mention new helper behavior.

**Step 4: Prepare for commits**

Stage new/modified files, ready for Conventional Commit message when user requests.

---

Plan complete and saved to `docs/plans/2026-02-14-benchmark-validation-reset-plan.md`. Two execution options:

1. Subagent-Driven (this session) — I dispatch a fresh subagent per task with superpowers:subagent-driven-development.
2. Parallel Session — start a new session using superpowers:executing-plans for batch execution.

Which approach should we take?
