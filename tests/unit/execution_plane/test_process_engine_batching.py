"""Tests for ProcessExecutionEngine micro-batching."""

import asyncio
import time
from typing import Dict

import pytest

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


class _RetryCounter:
    def __init__(self):
        self.attempts: Dict[int, int] = {}

    def record_attempt(self, offset: int) -> None:
        self.attempts[offset] = self.attempts.get(offset, 0) + 1

    def get_attempts(self, offset: int) -> int:
        return self.attempts.get(offset, 0)


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
    pass


def _failing_worker(item: WorkItem) -> None:
    if item.offset == 2:
        raise ValueError("Intentional failure")


_retry_counter = _RetryCounter()


def _worker_succeeds_on_second_attempt(item: WorkItem) -> None:
    _retry_counter.record_attempt(item.offset)
    if _retry_counter.get_attempts(item.offset) < 2:
        raise RuntimeError("Simulated transient failure")


def _worker_always_fails(item: WorkItem) -> None:
    _retry_counter.record_attempt(item.offset)
    raise RuntimeError("Permanent failure")


@pytest.fixture
def small_batch_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="process",
        max_in_flight=100,
        max_retries=3,
        retry_backoff_ms=100,
        exponential_backoff=True,
        max_retry_backoff_ms=1000,
        retry_jitter_ms=0,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=256,
            batch_size=4,
            max_batch_wait_ms=50,
            worker_join_timeout_ms=5000,
        ),
    )


@pytest.fixture
def retry_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="process",
        max_in_flight=100,
        max_retries=3,
        retry_backoff_ms=50,
        exponential_backoff=True,
        max_retry_backoff_ms=500,
        retry_jitter_ms=10,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=256,
            batch_size=2,
            max_batch_wait_ms=10,
            worker_join_timeout_ms=5000,
        ),
    )


class TestMicroBatching:
    @pytest.mark.asyncio
    async def test_batch_flush_on_size(self, small_batch_config):
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            for i in range(4):
                await engine.submit(_make_work_item(i))

            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 4
            assert all(e.status == CompletionStatus.SUCCESS for e in events)
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_batch_flush_on_timeout(self, small_batch_config):
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            await engine.submit(_make_work_item(0))
            await engine.submit(_make_work_item(1))

            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 2
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_completion_events_per_item(self, small_batch_config):
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            for i in range(8):
                await engine.submit(_make_work_item(i))

            await asyncio.sleep(1.0)

            events = await engine.poll_completed_events()
            assert len(events) == 8
            offsets = sorted(e.offset for e in events)
            assert offsets == list(range(8))
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_in_flight_count_tracks_items(self, small_batch_config):
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_sync_worker
        )
        try:
            await engine.submit(_make_work_item(0))
            await engine.submit(_make_work_item(1))
            assert engine.get_in_flight_count() == 2

            await asyncio.sleep(0.5)
            await engine.poll_completed_events()
            assert engine.get_in_flight_count() == 0
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_worker_failure_in_batch(self, small_batch_config):
        engine = ProcessExecutionEngine(
            config=small_batch_config, worker_fn=_failing_worker
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


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_success_on_retry(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_succeeds_on_second_attempt
        )
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(1.0)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.SUCCESS
            assert event.attempt == 2
            assert event.error is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_always_fails
        )
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(2.0)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.FAILURE
            assert event.attempt == retry_config.max_retries
            assert event.error is not None
            assert "Permanent failure" in event.error
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_always_fails
        )
        try:
            start_time = time.time()
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(2.5)

            events = await engine.poll_completed_events()
            elapsed = time.time() - start_time

            assert len(events) == 1
            assert events[0].attempt == 3

            expected_min_delay = (50 + 100) / 1000.0
            assert elapsed >= expected_min_delay * 0.9
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_backoff_cap_enforced(self):
        config = ExecutionConfig(
            mode="process",
            max_in_flight=100,
            max_retries=5,
            retry_backoff_ms=100,
            exponential_backoff=True,
            max_retry_backoff_ms=200,
            retry_jitter_ms=0,
            process_config=ProcessConfig(
                process_count=1,
                queue_size=256,
                batch_size=1,
                max_batch_wait_ms=10,
                worker_join_timeout_ms=5000,
            ),
        )

        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(config=config, worker_fn=_worker_always_fails)
        try:
            start_time = time.time()
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(1.5)

            events = await engine.poll_completed_events()
            elapsed = time.time() - start_time

            assert len(events) == 1
            assert events[0].attempt == 5

            expected_total_backoff = (100 + 200 + 200 + 200) / 1000.0
            assert elapsed >= expected_total_backoff * 0.9
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_attempt_count_on_immediate_success(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(config=retry_config, worker_fn=_sync_worker)
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.SUCCESS
            assert event.attempt == 1
        finally:
            await engine.shutdown()
