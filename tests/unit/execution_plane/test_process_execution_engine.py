import asyncio
import time
from multiprocessing import Process
from types import SimpleNamespace
from typing import cast

import msgpack
import pytest
import pytest_asyncio

from pyrallel_consumer.config import AsyncConfig, ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _decode_incoming_item,
    _work_item_to_dict,
)
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)


# Dummy synchronous worker function for testing
def sync_worker_fn(work_item: WorkItem):
    if work_item.payload == b"fail":
        raise ValueError("Simulated worker failure")
    elif work_item.payload == b"sleep":
        time.sleep(0.1)  # Simulate some work
    else:
        pass


class TestProcessExecutionEngine(BaseExecutionEngineContractTest):
    @pytest.fixture
    def config(self):
        return ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_in_flight=2,
            max_retries=3,
            retry_backoff_ms=50,
            exponential_backoff=True,
            max_retry_backoff_ms=200,
            retry_jitter_ms=10,
            process_config=ProcessConfig(
                process_count=2,
                queue_size=10,
                require_picklable_worker=True,
            ),
            async_config=AsyncConfig(task_timeout_ms=1000),
        )

    @pytest_asyncio.fixture  # type: ignore[override]
    async def engine(self, config):  # type: ignore[override]
        # ProcessExecutionEngine needs a synchronous worker function
        eng = ProcessExecutionEngine(config=config, worker_fn=sync_worker_fn)
        yield eng
        await eng.shutdown()

    @pytest.fixture  # type: ignore[override]
    def mock_work_item(self):
        return WorkItem(
            id="test-id-1",
            tp=TopicPartition(topic="test", partition=0),
            offset=0,
            epoch=0,
            key="key",
            payload=b"payload",
        )

    @pytest.fixture
    def mock_sleeping_work_item(self):
        return WorkItem(
            id="test-id-sleeping",
            tp=TopicPartition(topic="test", partition=0),
            offset=2,
            epoch=0,
            key="key",
            payload=b"sleep",
        )

    @pytest.mark.asyncio
    async def test_shutdown_waits_for_in_flight_tasks(
        self, engine: ProcessExecutionEngine, mock_sleeping_work_item: WorkItem
    ):
        await engine.submit(mock_sleeping_work_item)
        await engine.submit(mock_sleeping_work_item)
        await asyncio.sleep(0.1)  # Give workers time to pick up tasks

        # Ensure tasks are in-flight
        assert engine.get_in_flight_count() == 2

        start_time = asyncio.get_event_loop().time()
        await engine.shutdown()
        end_time = asyncio.get_event_loop().time()

        # Check that shutdown waited for tasks to complete
        assert (
            end_time - start_time
        ) >= 0.1 * 2 - 0.05  # 2 tasks, each sleeps for 0.1s. Allow for some variance.

        completed_events = await engine.poll_completed_events()
        assert len(completed_events) == 2
        assert all(e.status == CompletionStatus.SUCCESS for e in completed_events)
        assert engine.get_in_flight_count() == 0

    def test_worker_died_emits_failure_event(self):
        config = ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_in_flight=2,
            max_retries=3,
            retry_backoff_ms=10,
            exponential_backoff=False,
            max_retry_backoff_ms=50,
            retry_jitter_ms=0,
            process_config=ProcessConfig(
                process_count=0,
                queue_size=10,
                require_picklable_worker=True,
            ),
            async_config=AsyncConfig(task_timeout_ms=1000),
        )

        engine = ProcessExecutionEngine(config=config, worker_fn=sync_worker_fn)

        try:
            engine._workers = [
                cast(Process, SimpleNamespace(is_alive=lambda: False, exitcode=1))
            ]
            key = (0, "test", 0, 5)
            engine._in_flight_registry[key] = {
                "id": "dead-item",
                "topic": "test",
                "partition": 0,
                "offset": 5,
                "epoch": 0,
                "requeue_attempts": config.max_retries,
            }

            engine._ensure_workers_alive()

            assert key not in engine._in_flight_registry

            raw_event = engine._completion_queue.get_nowait()
            event_dict = msgpack.unpackb(raw_event, raw=False)
            assert event_dict["status"] == CompletionStatus.FAILURE.value
            assert event_dict["error"] == "worker_died_max_retries"
            assert event_dict["attempt"] == config.max_retries
            assert event_dict["offset"] == 5
            assert event_dict["partition"] == 0
        finally:
            asyncio.run(engine.shutdown())

    def test_decode_incoming_item_size_guard(self):
        sample_item = WorkItem(
            id="w1",
            tp=TopicPartition(topic="test", partition=0),
            offset=1,
            epoch=0,
            key=None,
            payload=b"val",
        )
        packed = cast(
            bytes, msgpack.packb([_work_item_to_dict(sample_item)], use_bin_type=True)
        )
        result = _decode_incoming_item(packed, max_bytes=len(packed) + 10)
        assert len(result) == 1
        assert result[0].offset == 1

        big_payload = b"0" * 5
        with pytest.raises(ValueError):
            _decode_incoming_item(big_payload, max_bytes=4)
