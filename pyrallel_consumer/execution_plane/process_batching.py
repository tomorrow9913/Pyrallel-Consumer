from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Optional

from pyrallel_consumer.dto import ProcessBatchMetrics, WorkItem
from pyrallel_consumer.execution_plane.process_codec import serialize_batch_payload as _serialize_batch_payload

_logger = logging.getLogger(__name__)


class BatchAccumulator:
    """Buffers WorkItems and flushes as batches to reduce IPC overhead.

    Flush triggers:
    - ``batch_size`` items accumulated (eager flush)
    - ``max_batch_wait_ms`` elapsed since first buffered item (timer flush)
    """

    def __init__(
        self,
        task_queue: Any,
        batch_size: int,
        max_batch_wait_ms: int,
        flush_policy: str = "size_or_timer",
        demand_flush_min_residence_ms: int = 0,
    ):
        self._task_queue = task_queue
        self._batch_size = batch_size
        self._max_batch_wait_sec = max_batch_wait_ms / 1000.0
        self._flush_policy = flush_policy
        self._demand_flush_min_residence_sec = demand_flush_min_residence_ms / 1000.0
        self._buffer: list[WorkItem] = []
        self._first_item_time: Optional[float] = None
        self._lock = threading.Lock()
        self._flush_timer: Optional[threading.Timer] = None
        self._closed = False
        self._size_flush_count = 0
        self._timer_flush_count = 0
        self._close_flush_count = 0
        self._demand_flush_count = 0
        self._total_flushed_items = 0
        self._last_flush_size = 0
        self._last_flush_wait_seconds = 0.0

    def add_nowait_fast_path(self, work_item: WorkItem) -> bool:
        """Flush single-item batches inline when the process queue has capacity."""
        if self._batch_size != 1:
            return False

        with self._lock:
            if self._closed or self._buffer:
                return False

            flush_enqueued_at = time.monotonic()
            packed = _serialize_batch_payload([work_item], flush_enqueued_at)
            put_nowait = getattr(self._task_queue, "put_nowait", None)
            try:
                if callable(put_nowait):
                    put_nowait(packed)
                else:
                    self._task_queue.put(packed, block=False)
            except queue.Full:
                return False

            self._size_flush_count += 1
            self._total_flushed_items += 1
            self._last_flush_size = 1
            self._last_flush_wait_seconds = 0.0
            if _logger.isEnabledFor(logging.DEBUG):
                _logger.debug("Batch flush (%s): size=%d", "size", 1)
            return True

    def add(self, work_item: WorkItem) -> None:
        with self._lock:
            if self._closed:
                return
            if self._should_flush_on_demand_locked():
                self._flush_locked(reason="demand")
            self._buffer.append(work_item)
            if self._first_item_time is None:
                self._first_item_time = time.monotonic()
                self._start_flush_timer()
            if len(self._buffer) >= self._batch_size:
                self._flush_locked(reason="size")

    def _should_flush_on_demand_locked(self) -> bool:
        if not self._buffer:
            return False
        if self._flush_policy == "demand":
            return True
        if self._flush_policy != "demand_min_residence":
            return False
        if self._first_item_time is None:
            return False
        oldest_age = max(0.0, time.monotonic() - self._first_item_time)
        return oldest_age >= self._demand_flush_min_residence_sec

    def _start_flush_timer(self) -> None:
        if self._flush_timer is not None:
            self._flush_timer.cancel()
        self._flush_timer = threading.Timer(self._max_batch_wait_sec, self._timer_flush)
        self._flush_timer.daemon = True
        self._flush_timer.start()

    def _timer_flush(self) -> None:
        with self._lock:
            if self._buffer and not self._closed:
                self._flush_locked(reason="timer")

    def _flush_locked(self, *, reason: str = "manual") -> None:
        if not self._buffer:
            return
        wait_seconds = (
            max(0.0, time.monotonic() - self._first_item_time)
            if self._first_item_time is not None
            else 0.0
        )
        batch = list(self._buffer)
        self._buffer.clear()
        self._first_item_time = None
        if self._flush_timer is not None:
            self._flush_timer.cancel()
            self._flush_timer = None
        if reason == "size":
            self._size_flush_count += 1
        elif reason == "timer":
            self._timer_flush_count += 1
        elif reason == "close":
            self._close_flush_count += 1
        elif reason == "demand":
            self._demand_flush_count += 1
        self._total_flushed_items += len(batch)
        self._last_flush_size = len(batch)
        self._last_flush_wait_seconds = wait_seconds
        if _logger.isEnabledFor(logging.DEBUG):
            _logger.debug("Batch flush (%s): size=%d", reason, len(batch))
        flush_enqueued_at = time.monotonic()
        packed = _serialize_batch_payload(batch, flush_enqueued_at)
        self._task_queue.put(packed)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._flush_timer is not None:
                self._flush_timer.cancel()
                self._flush_timer = None
            if self._buffer:
                self._flush_locked(reason="close")

    def snapshot(self) -> ProcessBatchMetrics:
        with self._lock:
            buffered_age_seconds = (
                max(0.0, time.monotonic() - self._first_item_time)
                if self._first_item_time is not None
                else 0.0
            )
            return ProcessBatchMetrics(
                size_flush_count=self._size_flush_count,
                timer_flush_count=self._timer_flush_count,
                close_flush_count=self._close_flush_count,
                total_flushed_items=self._total_flushed_items,
                last_flush_size=self._last_flush_size,
                last_flush_wait_seconds=self._last_flush_wait_seconds,
                buffered_items=len(self._buffer),
                buffered_age_seconds=buffered_age_seconds,
                demand_flush_count=self._demand_flush_count,
            )


class NoOpBatchAccumulator:
    """No-op accumulator used when transport bypasses shared-queue batching."""

    def add_nowait_fast_path(self, work_item: WorkItem) -> bool:
        del work_item
        return False

    def add(self, work_item: WorkItem) -> None:
        del work_item
        return None

    def close(self) -> None:
        return None

    def snapshot(self) -> ProcessBatchMetrics:
        return ProcessBatchMetrics(
            size_flush_count=0,
            timer_flush_count=0,
            close_flush_count=0,
            total_flushed_items=0,
            last_flush_size=0,
            last_flush_wait_seconds=0.0,
            buffered_items=0,
            buffered_age_seconds=0.0,
            demand_flush_count=0,
        )

_BatchAccumulator = BatchAccumulator
_NoOpBatchAccumulator = NoOpBatchAccumulator
