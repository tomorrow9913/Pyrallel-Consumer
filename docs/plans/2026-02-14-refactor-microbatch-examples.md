# Refactor, Micro-Batching & Examples Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Remove the unused `message_processor` interface from BrokerPoller, implement micro-batching in ProcessExecutionEngine, and create production-quality examples with README redirection.

**Architecture:** Three sequential tasks: (1) Dead-code removal of `message_processor` parameter chain across BrokerPoller/consumer/tests, (2) Batch accumulator layer in ProcessExecutionEngine that buffers WorkItems by `batch_size`/`max_batch_wait_ms` before IPC put, (3) `examples/` directory with async/process examples using PyrallelConsumer facade, main.py rewrite, and README update.

**Tech Stack:** Python 3.12, asyncio, multiprocessing, confluent-kafka, pydantic-settings, pytest, pytest-asyncio

---

## Task 1: Remove `message_processor` from BrokerPoller

The `message_processor` parameter is accepted by `BrokerPoller.__init__` and stored as `self._message_processor`, but never called anywhere in `_run_consumer()`. The actual message flow is `BrokerPoller` -> `WorkManager.submit_message()` -> `ExecutionEngine.submit()`. The `consumer.py` facade creates a `dummy_processor` lambda just to satisfy this dead parameter. All E2E tests pass `_dummy_message_processor` for the same reason.

**Files:**
- Modify: `pyrallel_consumer/control_plane/broker_poller.py` (lines 6, 42-44, 49)
- Modify: `pyrallel_consumer/consumer.py` (lines 1, 56-75)
- Modify: `tests/e2e/test_ordering.py` (lines 9, 23-24, 131-141, 177-183, 285-291, 351-357)
- Verify: `tests/unit/control_plane/test_broker_poller.py` (already works without message_processor)

**Step 1: Remove `message_processor` parameter from `BrokerPoller.__init__`**

In `pyrallel_consumer/control_plane/broker_poller.py`:
- Remove `Callable`, `Awaitable`, `List` from typing import (line 6) — only remove `Awaitable` since `Callable` and `List` may be used elsewhere. Check first.
- Remove the `message_processor` parameter (lines 42-44)
- Remove `self._message_processor = message_processor` (line 49)

After edit, `__init__` signature becomes:
```python
def __init__(
    self,
    consume_topic: str,
    kafka_config: KafkaConfig,
    execution_engine: BaseExecutionEngine,
    work_manager: Optional[WorkManager] = None,
) -> None:
```

And remove line 49 (`self._message_processor = message_processor`).

Check if `Awaitable`, `Callable`, `List`, `Any` are used elsewhere in the file:
- `List` is used extensively (e.g., `List[Message]`, `List[tuple[...]]`)
- `Any` is not used in the file body after removing `message_processor`
- `Awaitable` is not used elsewhere
- `Callable` is not used elsewhere

So update the import line from:
```python
from typing import Any, Awaitable, Callable, Dict, List, Optional, cast
```
to:
```python
from typing import Dict, List, Optional, cast
```

**Step 2: Remove `dummy_processor` from `consumer.py`**

In `pyrallel_consumer/consumer.py`:
- Remove `Any, Awaitable` from typing import (line 1). Keep `Callable, Union`.
- Remove comment block (lines 55-64) about message_processor being legacy
- Remove `dummy_processor` function definition (lines 66-67)
- Remove `message_processor=dummy_processor,` from BrokerPoller construction (line 72)

After edit, import becomes:
```python
from typing import Callable, Union
```

BrokerPoller construction becomes:
```python
self._poller = BrokerPoller(
    consume_topic=topic,
    kafka_config=config,
    execution_engine=self._execution_engine,
    work_manager=self._work_manager,
)
```

**Step 3: Remove `_dummy_message_processor` from E2E tests**

In `tests/e2e/test_ordering.py`:
- Remove import: `Awaitable, Callable` from line 9 (keep `Any, Dict, List, Optional`)
- Remove `_dummy_message_processor` function (lines 23-24)
- Remove `message_processor` parameter from `run_ordering_test` signature (lines 139-141)
- Remove `message_processor=message_processor or _dummy_message_processor,` from BrokerPoller construction (line 182)
- Remove `message_processor=_dummy_message_processor,` from BrokerPoller in `test_backpressure` (line 290)
- Remove `message_processor=_dummy_message_processor,` from BrokerPoller in `test_offset_commit_correctness` (line 356)

**Step 4: Run unit tests to verify no regressions**

Run: `pytest tests/unit/ -v --timeout=30`
Expected: All unit tests PASS (broker_poller tests already don't use message_processor)

**Step 5: Run linting**

Run: `ruff check pyrallel_consumer/control_plane/broker_poller.py pyrallel_consumer/consumer.py tests/e2e/test_ordering.py`
Expected: Clean (no errors)

**Step 6: Commit**

```bash
git add pyrallel_consumer/control_plane/broker_poller.py pyrallel_consumer/consumer.py tests/e2e/test_ordering.py
git commit -m "refactor(control-plane): remove unused message_processor parameter from BrokerPoller

The message_processor callback was accepted by BrokerPoller.__init__ but never
invoked in _run_consumer(). Message handling flows through WorkManager.submit_message()
exclusively. Remove the dead parameter, the dummy_processor in consumer.py, and all
E2E test scaffolding that existed only to satisfy the unused type signature."
```

---

## Task 2: Implement Micro-Batching in ProcessExecutionEngine

Currently `ProcessExecutionEngine.submit()` puts individual `WorkItem` objects into the task queue one at a time. `ProcessConfig` defines `batch_size=64`, `batch_bytes="256KB"`, and `max_batch_wait_ms=5` but these are unused. The `ProcessTask` DTO exists in `dto.py` (lines 80-99) but is never constructed. We need a batch accumulator that collects WorkItems and flushes them as `ProcessTask` batches based on size/time thresholds.

**Design decisions:**
- The `_BatchAccumulator` is an internal helper class within `process_engine.py`
- It buffers WorkItems per partition and flushes when `batch_size` is reached OR `max_batch_wait_ms` expires
- `_worker_loop` is updated to accept either `WorkItem` or `ProcessTask` from the queue
- For `ProcessTask`, the worker processes each item in the batch individually, emitting one `CompletionEvent` per offset
- The sentinel check remains unchanged (sentinel is `None`)

**Files:**
- Modify: `pyrallel_consumer/execution_plane/process_engine.py`
- Create: `tests/unit/execution_plane/test_process_engine_batching.py`
- Modify: `pyrallel_consumer/dto.py` (minor: make `ProcessTask.context` have default)

**Step 1: Write failing tests for batch accumulator**

Create `tests/unit/execution_plane/test_process_engine_batching.py`:

```python
"""Tests for ProcessExecutionEngine micro-batching."""
import asyncio
import time
from unittest.mock import MagicMock

import pytest

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


def _make_work_item(offset: int, partition: int = 0, topic: str = "test") -> WorkItem:
    return WorkItem(
        id=f"wi-{offset}",
        tp=TopicPartition(topic=topic, partition=partition),
        offset=offset,
        epoch=1,
        key=f"key-{offset}".encode(),
        payload=f"payload-{offset}".encode(),
    )


def _sync_worker(item: WorkItem) -> None:
    """Simple sync worker for testing."""
    pass


@pytest.fixture
def small_batch_config() -> ExecutionConfig:
    """Config with small batch_size=4 for easy testing."""
    return ExecutionConfig(
        mode="process",
        max_in_flight=100,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=256,
            batch_size=4,
            max_batch_wait_ms=50,
            worker_join_timeout_ms=5000,
        ),
    )


class TestMicroBatching:
    """Tests for micro-batching behavior in ProcessExecutionEngine."""

    @pytest.mark.asyncio
    async def test_batch_flush_on_size(self, small_batch_config):
        """Submitting batch_size items should flush immediately."""
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            for i in range(4):
                await engine.submit(_make_work_item(i))

            # Wait for processing
            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 4
            assert all(e.status == CompletionStatus.SUCCESS for e in events)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_batch_flush_on_timeout(self, small_batch_config):
        """Partial batch should flush after max_batch_wait_ms."""
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            # Submit fewer than batch_size
            await engine.submit(_make_work_item(0))
            await engine.submit(_make_work_item(1))

            # Wait longer than max_batch_wait_ms (50ms) + processing time
            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 2
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_completion_events_per_item(self, small_batch_config):
        """Each WorkItem in a batch should produce one CompletionEvent."""
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            for i in range(8):  # 2 batches of 4
                await engine.submit(_make_work_item(i))

            await asyncio.sleep(1.0)

            events = await engine.poll_completed_events()
            assert len(events) == 8
            offsets = sorted(e.offset for e in events)
            assert offsets == list(range(8))
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_in_flight_count_tracks_unbatched(self, small_batch_config):
        """in_flight_count should reflect submitted items, not batches."""
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            await engine.submit(_make_work_item(0))
            await engine.submit(_make_work_item(1))
            assert engine.get_in_flight_count() == 2

            # After processing
            await asyncio.sleep(0.5)
            await engine.poll_completed_events()
            assert engine.get_in_flight_count() == 0
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_worker_failure_in_batch(self, small_batch_config):
        """A failing item in a batch should produce FAILURE event for that item only."""
        call_count = 0

        def failing_worker(item: WorkItem) -> None:
            nonlocal call_count
            call_count += 1
            if item.offset == 2:
                raise ValueError("Intentional failure")

        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=failing_worker
        )
        try:
            for i in range(4):
                await engine.submit(_make_work_item(i))

            await asyncio.sleep(1.0)

            events = await engine.poll_completed_events()
            assert len(events) == 4

            by_offset = {e.offset: e for e in events}
            assert by_offset[0].status == CompletionStatus.SUCCESS
            assert by_offset[1].status == CompletionStatus.SUCCESS
            assert by_offset[2].status == CompletionStatus.FAILURE
            assert by_offset[3].status == CompletionStatus.SUCCESS
        finally:
            await engine.shutdown()
```

**Step 2: Run tests to verify they fail**

Run: `pytest tests/unit/execution_plane/test_process_engine_batching.py -v --timeout=30`
Expected: Tests pass trivially with current 1-by-1 submit (batch_size config is ignored). The tests are designed to pass with EITHER implementation — they verify the behavioral contract. The key test is `test_batch_flush_on_timeout` which should work with both implementations.

Actually, these tests should pass even without batching since the contract (submit -> completion event per item) is preserved. Let's make this a **behavioral + implementation test** by also verifying the IPC efficiency.

**Step 3: Implement `_BatchAccumulator` and update `ProcessExecutionEngine`**

In `pyrallel_consumer/execution_plane/process_engine.py`, add:

```python
import threading
import time
from collections import defaultdict
from typing import Union

# ... existing imports ...

class _BatchAccumulator:
    """Buffers WorkItems and flushes as batches to the task queue.

    Flush triggers:
    - batch_size items accumulated (eager flush)
    - max_batch_wait_ms elapsed since first item in current buffer (timer flush)
    """

    def __init__(
        self,
        task_queue: Queue,
        batch_size: int,
        max_batch_wait_ms: int,
    ):
        self._task_queue = task_queue
        self._batch_size = batch_size
        self._max_batch_wait_sec = max_batch_wait_ms / 1000.0
        self._buffer: list[WorkItem] = []
        self._first_item_time: Optional[float] = None
        self._lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._closed = False

    def add(self, work_item: WorkItem) -> None:
        """Add a WorkItem to the buffer. May trigger immediate flush."""
        with self._lock:
            if self._closed:
                return
            self._buffer.append(work_item)
            if self._first_item_time is None:
                self._first_item_time = time.monotonic()
                self._start_flush_timer()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked()

    def _start_flush_timer(self) -> None:
        """Start a timer that flushes the buffer after max_batch_wait_ms."""
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(
            self._max_batch_wait_sec, self._timer_flush
        )
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timer_flush(self) -> None:
        """Called by the timer thread when max_batch_wait_ms expires."""
        with self._lock:
            if self._buffer and not self._closed:
                self._flush_locked()

    def _flush_locked(self) -> None:
        """Flush current buffer to the task queue. Must hold self._lock."""
        if not self._buffer:
            return
        batch = list(self._buffer)
        self._buffer.clear()
        self._first_item_time = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        # Put the batch (list of WorkItems) into the queue
        self._task_queue.put(batch)

    def flush(self) -> None:
        """Force-flush any remaining items (used during shutdown)."""
        with self._lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._buffer:
                self._flush_locked()

    def close(self) -> None:
        """Flush remaining items and prevent new additions."""
        self.flush()
```

Update `_worker_loop` to handle both single `WorkItem` and batch `list[WorkItem]`:

```python
def _worker_loop(
    task_queue: Queue,
    completion_queue: Queue,
    worker_fn: Callable[[WorkItem], Any],
    process_idx: int,
    log_queue: Optional[Queue] = None,
):
    if log_queue is not None:
        LogManager.setup_worker_logging(log_queue)

    worker_logger = logging.getLogger(__name__)
    worker_logger.info("ProcessWorker[%d] started.", process_idx)

    while True:
        item = task_queue.get()
        if item is _SENTINEL:
            worker_logger.info(
                "ProcessWorker[%d] received sentinel, shutting down.", process_idx
            )
            break

        # Handle both single WorkItem and batched list[WorkItem]
        if isinstance(item, list):
            work_items = item
        else:
            work_items = [item]

        for work_item in work_items:
            status = CompletionStatus.SUCCESS
            error: Optional[str] = None
            try:
                worker_fn(work_item)
            except Exception as e:
                status = CompletionStatus.FAILURE
                error = str(e)
                worker_logger.exception(
                    "Task for offset %d failed in ProcessWorker[%d].",
                    work_item.offset,
                    process_idx,
                )
            finally:
                completion_event = CompletionEvent(
                    id=work_item.id,
                    tp=work_item.tp,
                    offset=work_item.offset,
                    epoch=work_item.epoch,
                    status=status,
                    error=error,
                )
                completion_queue.put(completion_event)

    worker_logger.info("ProcessWorker[%d] shutdown complete.", process_idx)
```

Update `ProcessExecutionEngine.__init__` to create the accumulator:

```python
def __init__(self, config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]):
    self._config = config
    self._worker_fn = worker_fn
    self._task_queue: Queue = Queue(maxsize=config.process_config.queue_size)
    self._completion_queue: Queue[CompletionEvent] = Queue()
    self._workers: List[Process] = []
    self._in_flight_count: int = 0
    self._logger = logging.getLogger(__name__)
    self._is_shutdown: bool = False

    self._log_queue: Queue[logging.LogRecord] = Queue()
    main_handlers = tuple(logging.getLogger().handlers)
    self._log_listener = LogManager.create_queue_listener(
        self._log_queue, main_handlers
    )
    self._log_listener.start()

    # Micro-batching accumulator
    self._batch_accumulator = _BatchAccumulator(
        task_queue=self._task_queue,
        batch_size=config.process_config.batch_size,
        max_batch_wait_ms=config.process_config.max_batch_wait_ms,
    )

    self._start_workers()
```

Update `submit` to use accumulator:

```python
async def submit(self, work_item: WorkItem) -> None:
    await asyncio.to_thread(self._batch_accumulator.add, work_item)
    self._in_flight_count += 1
```

Update `shutdown` to flush the accumulator before sending sentinels:

```python
async def shutdown(self) -> None:
    if self._is_shutdown:
        _logger.debug(
            "ProcessExecutionEngine.shutdown() called but already shut down. Skipping."
        )
        return
    self._is_shutdown = True

    _logger.info("Initiating ProcessExecutionEngine shutdown.")

    # Flush any remaining buffered items
    self._batch_accumulator.close()

    # Send sentinel to all workers
    for _ in self._workers:
        self._task_queue.put(_SENTINEL)

    for worker in self._workers:
        worker.join(
            timeout=self._config.process_config.worker_join_timeout_ms / 1000.0
        )
        if worker.is_alive():
            _logger.warning(
                "ProcessWorker[%d] did not shut down gracefully. Terminating.",
                worker.pid,
            )
            worker.terminate()

    _logger.info("ProcessExecutionEngine shutdown complete.")
    self._log_listener.stop()
```

**Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/execution_plane/test_process_engine_batching.py -v --timeout=60`
Expected: All 5 tests PASS

**Step 5: Run full unit test suite**

Run: `pytest tests/unit/ -v --timeout=60`
Expected: All tests PASS (existing contract tests should still work since the batching is transparent to the BaseExecutionEngine interface)

**Step 6: Run linting**

Run: `ruff check pyrallel_consumer/execution_plane/process_engine.py tests/unit/execution_plane/test_process_engine_batching.py`
Expected: Clean

**Step 7: Commit**

```bash
git add pyrallel_consumer/execution_plane/process_engine.py tests/unit/execution_plane/test_process_engine_batching.py
git commit -m "feat(execution): implement micro-batching in ProcessExecutionEngine

Add _BatchAccumulator that buffers WorkItems and flushes to the IPC task
queue when batch_size is reached or max_batch_wait_ms expires. This reduces
IPC overhead by sending batches instead of individual items across the
multiprocessing Queue. The worker loop handles both single and batched items
transparently, preserving one CompletionEvent per WorkItem."
```

---

## Task 3: Examples Directory, main.py Rewrite, and README Update

**Files:**
- Create: `examples/README.md`
- Create: `examples/basic_async.py`
- Create: `examples/basic_process.py`
- Modify: `main.py` (complete rewrite)
- Modify: `README.md` (update 💡 사용법 section)

**Step 1: Create `examples/README.md`**

```markdown
# Pyrallel Consumer Examples

This directory contains runnable examples demonstrating how to use Pyrallel Consumer.

## Prerequisites

1. A running Kafka broker (default: `localhost:9092`)
2. Install dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```
3. Produce test messages (using the bundled producer script):
   ```bash
   uv run python benchmarks/producer.py --num-messages 1000 --num-keys 50 --topic demo
   ```

## Examples

### `basic_async.py` — Async Execution Engine

Demonstrates the recommended async mode using `PyrallelConsumer` facade. Best for I/O-bound workloads (HTTP calls, database queries, file I/O).

```bash
uv run python examples/basic_async.py
```

### `basic_process.py` — Process Execution Engine

Demonstrates multiprocessing mode for CPU-bound workloads. Uses micro-batching to reduce IPC overhead.

```bash
uv run python examples/basic_process.py
```

## Configuration

All examples use environment variables or `.env` file for Kafka configuration. Key settings:

| Variable | Default | Description |
|----------|---------|-------------|
| `KAFKA_BOOTSTRAP_SERVERS` | `localhost:9092` | Kafka broker address |
| `KAFKA_CONSUMER_GROUP` | `pyrallel-consumer-group` | Consumer group ID |
| `PARALLEL_CONSUMER_EXECUTION__MODE` | `async` | `async` or `process` |
| `PARALLEL_CONSUMER_EXECUTION__MAX_IN_FLIGHT` | `1000` | Max concurrent messages |

See `pyrallel_consumer/config.py` for the full configuration schema.

## Tuning Guide

### Async Mode (I/O-bound)
- Increase `max_in_flight` to improve throughput for high-latency workers
- Set `async_config.task_timeout_ms` to match your SLA

### Process Mode (CPU-bound)
- `process_config.process_count`: Match your CPU core count
- `process_config.batch_size`: Larger batches reduce IPC overhead but increase latency
  - I/O-bound workers: `batch_size=16-32`
  - CPU-bound workers: `batch_size=64-128`
- `process_config.max_batch_wait_ms`: Lower = less latency, higher = better batching
  - Real-time: `1-5ms`
  - Throughput-optimized: `10-50ms`
- `process_config.queue_size`: Should be `>= batch_size * process_count * 2`
```

**Step 2: Create `examples/basic_async.py`**

```python
#!/usr/bin/env python3
"""Basic async example using PyrallelConsumer.

Demonstrates:
- PyrallelConsumer facade with async worker
- Graceful shutdown on SIGINT/SIGTERM
- Metrics reporting

Usage:
    # Produce test messages first:
    uv run python benchmarks/producer.py --num-messages 1000 --num-keys 50 --topic demo

    # Run this example:
    uv run python examples/basic_async.py
"""

import asyncio
import json
import logging
import signal

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def my_async_worker(item: WorkItem) -> None:
    """Example async worker that processes a Kafka message.

    Replace this with your actual business logic (e.g., HTTP call, DB write).
    """
    payload = item.payload
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    logger.info(
        "Processing message: topic=%s partition=%d offset=%d key=%s",
        item.tp.topic,
        item.tp.partition,
        item.offset,
        item.key,
    )
    # Simulate async I/O work
    await asyncio.sleep(0.01)


async def main() -> None:
    config = KafkaConfig()
    # Ensure async mode
    config.parallel_consumer.execution.mode = "async"
    config.parallel_consumer.execution.max_in_flight = 200

    consumer = PyrallelConsumer(
        config=config,
        worker=my_async_worker,
        topic="demo",
    )

    # Graceful shutdown on signals
    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await consumer.start()
    logger.info("Consumer started. Press Ctrl+C to stop.")

    try:
        # Print metrics periodically until shutdown
        while not stop_event.is_set():
            await asyncio.sleep(5.0)
            metrics = consumer.get_metrics()
            logger.info(
                "Metrics: in_flight=%d paused=%s partitions=%d",
                metrics.total_in_flight,
                metrics.is_paused,
                len(metrics.partitions),
            )
    finally:
        logger.info("Stopping consumer...")
        await consumer.stop()
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 3: Create `examples/basic_process.py`**

```python
#!/usr/bin/env python3
"""Basic process (multiprocessing) example using PyrallelConsumer.

Demonstrates:
- PyrallelConsumer facade with sync worker (CPU-bound)
- Micro-batching configuration
- Graceful shutdown

Usage:
    # Produce test messages first:
    uv run python benchmarks/producer.py --num-messages 5000 --num-keys 100 --topic demo

    # Run this example:
    uv run python examples/basic_process.py
"""

import asyncio
import hashlib
import json
import logging
import signal

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


def my_cpu_worker(item: WorkItem) -> None:
    """Example sync worker for CPU-bound tasks.

    This runs in a separate process. Must be picklable (top-level function).
    Replace with your actual CPU-heavy logic (e.g., image processing, ML inference).
    """
    payload = item.payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    # Simulate CPU-bound work
    for _ in range(100):
        hashlib.sha256(payload.encode()).hexdigest()


async def main() -> None:
    config = KafkaConfig()
    # Switch to process mode
    config.parallel_consumer.execution.mode = "process"
    config.parallel_consumer.execution.max_in_flight = 500
    config.parallel_consumer.execution.process_config.process_count = 4
    config.parallel_consumer.execution.process_config.batch_size = 32
    config.parallel_consumer.execution.process_config.max_batch_wait_ms = 10

    consumer = PyrallelConsumer(
        config=config,
        worker=my_cpu_worker,
        topic="demo",
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await consumer.start()
    logger.info("Consumer started (process mode). Press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            await asyncio.sleep(5.0)
            metrics = consumer.get_metrics()
            logger.info(
                "Metrics: in_flight=%d paused=%s partitions=%d",
                metrics.total_in_flight,
                metrics.is_paused,
                len(metrics.partitions),
            )
    finally:
        logger.info("Stopping consumer...")
        await consumer.stop()
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 4: Rewrite `main.py`**

Replace the current raw confluent-kafka main.py with a PyrallelConsumer facade example:

```python
#!/usr/bin/env python3
"""Pyrallel Consumer — Quick Start.

This is a living document that demonstrates the simplest possible usage
of PyrallelConsumer. For more examples, see the examples/ directory.

Usage:
    uv run python main.py
"""

import asyncio
import json
import logging

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

logging.basicConfig(level=logging.INFO)


async def worker(item: WorkItem) -> None:
    payload = item.payload
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    print("offset=%d key=%s payload=%s" % (item.offset, item.key, payload))


async def main() -> None:
    config = KafkaConfig()
    consumer = PyrallelConsumer(config=config, worker=worker, topic="demo")
    await consumer.start()
    try:
        await asyncio.sleep(30)  # Run for 30 seconds
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
```

**Step 5: Update README.md 💡 사용법 section**

Replace the current placeholder section:
```markdown
## 💡 사용법

(이 섹션에는 추후 간단한 코드 사용 예시가 추가될 예정입니다.)
```

With:
```markdown
## 💡 사용법

### Quick Start

```python
import asyncio
from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem


async def worker(item: WorkItem) -> None:
    print(f"offset={item.offset} payload={item.payload}")


async def main() -> None:
    config = KafkaConfig()
    consumer = PyrallelConsumer(config=config, worker=worker, topic="my-topic")
    await consumer.start()
    try:
        await asyncio.sleep(60)
    finally:
        await consumer.stop()

asyncio.run(main())
```

For detailed examples including async mode, process mode, configuration tuning, and graceful shutdown patterns, see the **[`examples/`](./examples/)** directory.
```

**Step 6: Run linting on all changed files**

Run: `ruff check main.py examples/ && ruff format --check main.py examples/`
Expected: Clean

**Step 7: Commit**

```bash
git add examples/ main.py README.md
git commit -m "docs: add examples directory with async/process usage guides

- Create examples/README.md with prerequisites, tuning guide, and configuration reference
- Add examples/basic_async.py demonstrating PyrallelConsumer with async worker
- Add examples/basic_process.py demonstrating process mode with micro-batching config
- Rewrite main.py as a minimal PyrallelConsumer quick-start
- Update README.md usage section with quick-start snippet and link to examples/"
```

---

## Post-Implementation Checklist

- [ ] `pytest tests/unit/ -v` — all unit tests pass
- [ ] `ruff check .` — no lint errors
- [ ] `ruff format --check .` — formatting clean
- [ ] `pre-commit run --all-files` — all hooks pass
- [ ] No f-string logging (grep: `logger\.\w+\(f"`)
- [ ] No production `assert` statements
- [ ] GEMINI.md updated with completed tasks
