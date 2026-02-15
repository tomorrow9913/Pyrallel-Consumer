# E2E Test Isolation & KEY_HASH Ordering Fix — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Fix two E2E test failures: (1) message leakage between tests due to shared topic/consumer-group, (2) KEY_HASH ordering violation where same-key messages execute concurrently instead of serially.

**Architecture:** Move `OrderingMode` enum to `dto.py` to avoid circular imports. Add `ordering_mode` parameter to `WorkManager.__init__()` with per-key in-flight tracking in `schedule()` and cleanup in `poll_completed_events()`. Change E2E fixture to per-function scope with unique consumer groups.

**Tech Stack:** Python 3.12, asyncio, confluent-kafka, pytest, pytest-asyncio

---

## Task 1: Move `OrderingMode` to `dto.py`

**Files:**
- Modify: `pyrallel_consumer/dto.py` (add enum)
- Modify: `pyrallel_consumer/control_plane/broker_poller.py` (import from dto instead of defining locally)

**Step 1: Add `OrderingMode` to `dto.py`**

Add after the `CompletionStatus` enum (around line 17):

```python
class OrderingMode(Enum):
    """Ordering guarantees supported by the consumer."""

    KEY_HASH = "key_hash"
    PARTITION = "partition"
    UNORDERED = "unordered"
```

**Step 2: Update `broker_poller.py` imports**

Remove the local `OrderingMode` class definition (lines 25-30). Add import:

```python
from ..dto import CompletionStatus, OrderingMode, PartitionMetrics, SystemMetrics
from ..dto import TopicPartition as DtoTopicPartition
```

Keep the existing `self.ORDERING_MODE = OrderingMode.KEY_HASH` line unchanged.

**Step 3: Run tests to verify no regressions**

Run: `pytest tests/unit/ -v --tb=short`
Expected: All 91 unit tests pass (same as before).

**Step 4: Commit**

```bash
git add pyrallel_consumer/dto.py pyrallel_consumer/control_plane/broker_poller.py
git commit -m "refactor: move OrderingMode enum to dto.py to avoid circular imports

Co-authored-by: Sisyphus <sisyphus@opencode.ai>"
```

---

## Task 2: Add per-key serialization to `WorkManager.schedule()`

**Files:**
- Modify: `pyrallel_consumer/control_plane/work_manager.py`
- Create: `tests/unit/control_plane/test_work_manager_ordering.py`

### Step 1: Write the failing tests

Create `tests/unit/control_plane/test_work_manager_ordering.py`:

```python
"""Tests for WorkManager ordering mode behavior."""

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    OrderingMode,
    OffsetRange,
    TopicPartition as DtoTopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def tp():
    return DtoTopicPartition(topic="test-topic", partition=0)


def _make_tracker_mock(tp):
    mock = MagicMock(
        spec=OffsetTracker(topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500)
    )
    mock.get_gaps.return_value = []
    mock.advance_high_water_mark.return_value = None
    return mock


def _setup_wm(engine, tp, ordering_mode, max_in_flight=100):
    """Create a WorkManager with ordering_mode, assign tp, wire tracker mock."""
    wm = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=max_in_flight,
        ordering_mode=ordering_mode,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker
    return wm, tracker


# --- KEY_HASH ordering tests ---


@pytest.mark.asyncio
async def test_key_hash_blocks_same_key_concurrent(mock_engine, tp):
    """KEY_HASH mode: second item with same key must NOT be submitted while first is in-flight."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    # Only one submit — the second is blocked because same key is in-flight
    assert mock_engine.submit.await_count == 1
    submitted = mock_engine.submit.call_args_list[0].args[0]
    assert submitted.offset == 0


@pytest.mark.asyncio
async def test_key_hash_allows_different_keys_concurrent(mock_engine, tp):
    """KEY_HASH mode: items with different keys CAN run concurrently."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_key_hash_unblocks_after_completion(mock_engine, tp):
    """KEY_HASH mode: after completion, the next item for that key is eligible."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    # One submitted
    assert mock_engine.submit.await_count == 1
    first_item_id = mock_engine.submit.call_args_list[0].args[0].id

    # Simulate completion
    completion = CompletionEvent(
        id=first_item_id, tp=tp, offset=0, epoch=1,
        status=CompletionStatus.SUCCESS, error=None,
    )
    mock_engine.poll_completed_events.return_value = [completion]
    await wm.poll_completed_events()

    # poll_completed_events calls schedule() internally after each completion,
    # so the second item should now be submitted
    assert mock_engine.submit.await_count == 2
    second_submitted = mock_engine.submit.call_args_list[1].args[0]
    assert second_submitted.offset == 1


@pytest.mark.asyncio
async def test_unordered_allows_same_key_concurrent(mock_engine, tp):
    """UNORDERED mode: same-key items CAN run concurrently — no restriction."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.UNORDERED)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_default_ordering_mode_is_unordered(mock_engine):
    """WorkManager default ordering_mode should be UNORDERED for backward compat."""
    wm = WorkManager(execution_engine=mock_engine)
    assert wm._ordering_mode == OrderingMode.UNORDERED


@pytest.mark.asyncio
async def test_key_hash_on_revoke_clears_keys_in_flight(mock_engine, tp):
    """on_revoke must clear keys_in_flight for revoked partitions."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1

    # key-A is now in _keys_in_flight
    wm.on_revoke([tp])

    # After revoke, keys_in_flight for this tp should be cleared
    # Re-assign tp and submit same key — should work
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        new_tracker = _make_tracker_mock(tp)
        MockOT.return_value = new_tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = new_tracker

    mock_engine.submit.reset_mock()
    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1
```

### Step 2: Run tests to verify they fail

Run: `pytest tests/unit/control_plane/test_work_manager_ordering.py -v --tb=short`
Expected: FAIL — `WorkManager.__init__()` does not accept `ordering_mode` parameter.

### Step 3: Implement per-key serialization in `WorkManager`

Modify `pyrallel_consumer/control_plane/work_manager.py`:

**3a. Add import for OrderingMode:**

```python
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, OrderingMode, OffsetRange
```

**3b. Update `__init__` signature:**

```python
def __init__(
    self,
    execution_engine: BaseExecutionEngine,
    max_in_flight_messages: int = 1000,
    metrics_exporter: Optional[MetricsExporter] = None,
    ordering_mode: OrderingMode = OrderingMode.UNORDERED,
):
    # ... existing fields ...
    self._ordering_mode = ordering_mode
    # KEY_HASH: set of (tp, key) tuples currently in-flight
    self._keys_in_flight: set[tuple[DtoTopicPartition, Any]] = set()
```

**3c. Update `schedule()` — add per-key skip logic:**

In the inner loop where we iterate `for key, queue in virtual_partition_queues.items()`:

```python
for key, queue in virtual_partition_queues.items():
    if queue.empty():
        continue

    # KEY_HASH: skip this key if it already has an in-flight item
    if (
        self._ordering_mode == OrderingMode.KEY_HASH
        and (tp, key) in self._keys_in_flight
    ):
        continue

    # ... existing peek logic ...
```

After the submit succeeds (after `self._current_in_flight_count += 1`):

```python
if self._ordering_mode == OrderingMode.KEY_HASH:
    self._keys_in_flight.add((item_to_submit.tp, item_to_submit.key))
```

**3d. Update `poll_completed_events()` — cleanup on completion:**

Inside the `if event.id in self._in_flight_work_items:` block, before deleting the work item:

```python
if self._ordering_mode == OrderingMode.KEY_HASH:
    completed_item = self._in_flight_work_items[event.id]
    self._keys_in_flight.discard((completed_item.tp, completed_item.key))
```

**3e. Update `on_revoke()` — clear keys_in_flight for revoked partitions:**

After the existing stale_ids cleanup loop:

```python
if self._ordering_mode == OrderingMode.KEY_HASH:
    self._keys_in_flight = {
        (tp_key, key) for tp_key, key in self._keys_in_flight
        if tp_key not in revoked_tp_set
    }
```

### Step 4: Run ordering tests to verify they pass

Run: `pytest tests/unit/control_plane/test_work_manager_ordering.py -v --tb=short`
Expected: All 6 tests PASS.

### Step 5: Run full unit test suite to verify no regressions

Run: `pytest tests/unit/ -v --tb=short`
Expected: All tests pass. Existing `test_work_manager.py` tests use the default `ordering_mode=UNORDERED` so behavior is unchanged.

### Step 6: Commit

```bash
git add pyrallel_consumer/control_plane/work_manager.py tests/unit/control_plane/test_work_manager_ordering.py
git commit -m "feat(control-plane): add per-key serialization in WorkManager for KEY_HASH ordering

WorkManager now accepts ordering_mode parameter. In KEY_HASH mode, schedule()
skips keys that already have an in-flight item, ensuring same-key messages
execute serially. UNORDERED mode (default) is unchanged for backward compat.

Co-authored-by: Sisyphus <sisyphus@opencode.ai>"
```

---

## Task 3: Wire `ordering_mode` through BrokerPoller → WorkManager

**Files:**
- Modify: `pyrallel_consumer/control_plane/broker_poller.py`

### Step 1: Pass ordering_mode to WorkManager

In `BrokerPoller.__init__()`, where `WorkManager` is created (around line 64):

```python
self._work_manager = work_manager or WorkManager(
    execution_engine=self._execution_engine,
    ordering_mode=self.ORDERING_MODE,
)
```

Also update: when `work_manager` is provided externally (e.g., in tests), its ordering_mode should already be set by the caller. No need to override.

### Step 2: Run unit tests

Run: `pytest tests/unit/ -v --tb=short`
Expected: All tests pass.

### Step 3: Commit

```bash
git add pyrallel_consumer/control_plane/broker_poller.py
git commit -m "fix(control-plane): pass ordering_mode from BrokerPoller to WorkManager

Co-authored-by: Sisyphus <sisyphus@opencode.ai>"
```

---

## Task 4: Fix E2E test isolation

**Files:**
- Modify: `tests/e2e/test_ordering.py`

### Step 1: Change fixture scope and add per-test consumer group

**4a. Change `create_e2e_topic` from `scope="module"` to default `scope="function"`:**

```python
@pytest.fixture(autouse=True)
def create_e2e_topic(kafka_admin_client: AdminClient):
    """각 테스트 전에 토픽을 삭제/재생성하여 격리합니다."""
    topic_name = E2E_TOPIC

    # Delete existing topic
    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
        time.sleep(2)  # Wait for metadata propagation
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise

    # Create fresh topic
    num_partitions = 8
    topic = NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
    kafka_admin_client.create_topics([topic])[topic_name].result()
    time.sleep(1)  # Wait for topic to be fully ready

    yield

    # Cleanup after each test
    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise
```

**4b. Change `kafka_admin_client` from `scope="module"` to default `scope="function"`:**

```python
@pytest.fixture
def kafka_admin_client():
    """테스트용 Kafka AdminClient fixture."""
    return AdminClient({"bootstrap.servers": E2E_CONF["bootstrap.servers"]})
```

**4c. Update `base_kafka_config` to use unique consumer group per test:**

```python
import uuid

@pytest.fixture
def base_kafka_config() -> KafkaConfig:
    """테스트용 기본 KafkaConfig 객체를 생성합니다 (고유 consumer group)."""
    return KafkaConfig(
        BOOTSTRAP_SERVERS=[E2E_CONF["bootstrap.servers"]],
        CONSUMER_GROUP=f"e2e_test_{uuid.uuid4().hex[:8]}",
        AUTO_OFFSET_RESET=E2E_CONF["auto.offset.reset"],
        ENABLE_AUTO_COMMIT=E2E_CONF["enable.auto.commit"],
    )
```

### Step 2: Also clean up the `message_processor` references

Since Task 1 of the previous plan removed `message_processor` from `BrokerPoller`, but commit `60a1a3f` (pre-existing) re-introduced it, the e2e tests still pass `message_processor=_dummy_message_processor`. Remove these references:

- Remove the `_dummy_message_processor` function definition (lines 23-24)
- Remove `message_processor` kwarg from `run_ordering_test()` signature and the `BrokerPoller()` call inside it
- Remove `message_processor=_dummy_message_processor` from `test_backpressure` and `test_offset_commit_correctness` `BrokerPoller()` calls

**NOTE**: This requires first removing `message_processor` from `BrokerPoller.__init__()` again (Task 1 cleanup that was undone by `60a1a3f`). Handle this in the same commit since it's the same logical change.

### Step 3: Update E2E tests to pass `ordering_mode` to WorkManager

In `run_ordering_test()` and the manual BrokerPoller setups in `test_backpressure` and `test_offset_commit_correctness`, when constructing `WorkManager`, pass `ordering_mode`:

```python
from pyrallel_consumer.dto import OrderingMode

work_manager = WorkManager(
    execution_engine=engine,
    max_in_flight_messages=execution_config.max_in_flight,
    ordering_mode=ordering_mode,  # Pass from test parameter
)
```

For `test_backpressure` and `test_offset_commit_correctness` which manually create `WorkManager`:

```python
work_manager = WorkManager(
    execution_engine=engine,
    max_in_flight_messages=max_in_flight,
    ordering_mode=OrderingMode.KEY_HASH,  # Match the poller's mode
)
```

### Step 4: Run E2E tests

Run: `pytest tests/e2e/test_ordering.py -v --tb=long`
Expected: All 5 tests PASS.

### Step 5: Run full test suite

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

### Step 6: Commit

```bash
git add tests/e2e/test_ordering.py pyrallel_consumer/control_plane/broker_poller.py
git commit -m "fix(e2e): isolate tests with per-function topic recreation and unique consumer groups

- Change create_e2e_topic fixture to function scope for full test isolation
- Use UUID-based consumer group per test to prevent offset leakage
- Remove re-introduced message_processor parameter from BrokerPoller
- Pass ordering_mode to WorkManager in all E2E test setups

Co-authored-by: Sisyphus <sisyphus@opencode.ai>"
```

---

## Task 5: Verify and push

### Step 1: Run full test suite with coverage

Run: `pytest --cov=pyrallel_consumer --cov-report=term-missing tests/ -v`
Expected: All tests pass.

### Step 2: Run pre-commit

Run: `pre-commit run --all-files`
Expected: All checks pass (except pre-existing mypy issues if any).

### Step 3: Update GEMINI.md

Add entry documenting the fixes per GEMINI rules.

### Step 4: Push

```bash
git push origin develop
```
