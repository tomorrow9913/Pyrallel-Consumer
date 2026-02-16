"""Tests for ProcessExecutionEngine micro-batching."""

import asyncio

import pytest

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import CompletionStatus, TopicPartition, WorkItem
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
    pass


def _failing_worker(item: WorkItem) -> None:
    if item.offset == 2:
        raise ValueError("Intentional failure")


@pytest.fixture
def small_batch_config() -> ExecutionConfig:
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
