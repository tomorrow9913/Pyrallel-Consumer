import logging
import queue
from collections.abc import AsyncGenerator
from typing import Any, cast
from unittest.mock import Mock

import msgpack
import pytest
import pytest_asyncio

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _work_item_from_dict,
    _work_item_to_dict,
)
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)


class _DeadWorker:
    exitcode = 1

    def is_alive(self) -> bool:
        return False


async def _async_worker(_item) -> None:
    return None


def _sync_worker(_item) -> None:
    return None


def _contract_worker(item: WorkItem) -> None:
    if item.payload == b"fail":
        raise ValueError("simulated worker failure")


class TestProcessExecutionEngineContract(BaseExecutionEngineContractTest):
    """Shared execution-engine contract coverage for process mode."""

    @pytest.fixture
    def config(self) -> ExecutionConfig:
        return ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_in_flight=2,
            max_retries=1,
            process_config=ProcessConfig(process_count=1, queue_size=8),
        )

    @pytest_asyncio.fixture
    async def engine(
        self, config: ExecutionConfig
    ) -> AsyncGenerator[ProcessExecutionEngine, None]:
        engine = ProcessExecutionEngine(config=config, worker_fn=_contract_worker)
        try:
            yield engine
        finally:
            await engine.shutdown()


def test_process_work_item_serialization_preserves_poison_key() -> None:
    item = WorkItem(
        id="work-1",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=3,
        key=1,
        payload=b"payload",
        poison_key=b"original-key",
    )

    decoded = _work_item_from_dict(_work_item_to_dict(item))

    assert decoded.poison_key == b"original-key"


def test_ensure_workers_alive_does_not_requeue_timed_out_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
            "timed_out": True,
        }
    }
    engine_any._task_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)

    replacement_worker = Mock()
    monkeypatch.setattr(engine, "_start_worker", lambda idx: replacement_worker)

    engine._ensure_workers_alive()

    assert engine_any._task_queue.empty()
    assert (0, "topic", 1, 42) not in engine_any._in_flight_registry
    assert engine_any._workers == [replacement_worker]

    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "task_timeout"
    assert event.attempt == 1
    assert event.tp == TopicPartition("topic", 1)
    assert event.offset == 42


def test_process_execution_engine_bounds_log_queue_to_process_queue_size(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = {}

    class _FakeListener:
        def start(self) -> None:
            created["started"] = True

        def stop(self) -> None:
            created["stopped"] = True

    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    def _fake_create_queue_listener(log_queue, handlers=()):
        created["queue"] = log_queue
        created["handlers"] = handlers
        return _FakeListener()

    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.LogManager.create_queue_listener",
        _fake_create_queue_listener,
    )

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(process_count=1, queue_size=7),
    )

    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)

    assert cast(Any, engine._log_queue)._maxsize == 7
    assert created["queue"] is engine._log_queue
    assert created["started"] is True


def test_process_execution_engine_rejects_async_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(process_count=1, queue_size=7),
    )

    with pytest.raises(TypeError, match="synchronous picklable worker"):
        ProcessExecutionEngine(config=config, worker_fn=_async_worker)


def test_ensure_workers_alive_stops_requeueing_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 3,
        }
    }
    engine_any._task_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)

    replacement_worker = Mock()
    monkeypatch.setattr(engine, "_start_worker", lambda idx: replacement_worker)

    engine._ensure_workers_alive()

    assert engine_any._task_queue.empty()
    assert (0, "topic", 1, 42) not in engine_any._in_flight_registry

    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_died_max_retries"
    assert event.attempt == 3


def test_drain_registry_events_applies_start_and_timeout_sequence() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()

    key = (0, "topic", 1, 42)
    engine_any._registry_event_queue.put(
        {
            "kind": "start",
            "key": key,
            "payload": {
                "id": "work-42",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
            },
        }
    )
    engine_any._registry_event_queue.put(
        {
            "kind": "timeout",
            "key": key,
            "attempt": 2,
            "timeout_error": "task timed out",
        }
    )

    engine._drain_registry_events()

    assert engine_any._in_flight_registry == {
        key: {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "requeue_attempts": 0,
            "timed_out": True,
            "timeout_error": "task timed out",
            "attempt": 2,
        }
    }


def test_drain_registry_event_queue_returns_drained_count() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()

    key = (0, "topic", 1, 42)
    engine_any._registry_event_queue.put(
        {
            "kind": "start",
            "key": key,
            "payload": {
                "id": "work-42",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
            },
        }
    )
    engine_any._registry_event_queue.put(
        {
            "kind": "timeout",
            "key": key,
            "attempt": 2,
            "timeout_error": "task timed out",
        }
    )

    drained = engine._drain_registry_event_queue()

    assert drained == 2
    assert engine_any._in_flight_registry[key]["timed_out"] is True
    assert engine_any._in_flight_registry[key]["attempt"] == 2


def test_recover_dead_worker_items_emits_timeout_failure_and_requeues_retryable_work():
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._completion_queue = queue.Queue()
    engine_any._logger = logging.getLogger(__name__)
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
            "timed_out": True,
            "timeout_error": "task_timeout",
            "attempt": 1,
        },
        (0, "topic", 1, 43): {
            "id": "work-43",
            "topic": "topic",
            "partition": 1,
            "offset": 43,
            "epoch": 7,
            "requeue_attempts": 1,
        },
    }

    to_requeue = engine._recover_dead_worker_items(0)

    assert len(to_requeue) == 1
    assert to_requeue[0]["offset"] == 43
    assert to_requeue[0]["requeue_attempts"] == 2
    assert engine_any._in_flight_registry == {}

    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "task_timeout"
    assert event.offset == 42


def test_drain_shutdown_ipc_once_reuses_registry_event_rules_and_prefetches_completion():
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {}
    engine_any._prefetched_completion_events = []
    engine_any._registry_event_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()

    key = (0, "topic", 1, 42)
    engine_any._registry_event_queue.put(
        {
            "kind": "start",
            "key": key,
            "payload": {
                "id": "work-42",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
            },
        }
    )
    engine_any._registry_event_queue.put(
        {
            "kind": "timeout",
            "key": key,
            "attempt": 3,
            "timeout_error": "task timed out",
        }
    )

    packed_event = msgpack.packb(
        _completion_event_to_dict(
            _completion_event_from_dict(
                {
                    "id": "work-42",
                    "topic": "topic",
                    "partition": 1,
                    "offset": 42,
                    "epoch": 7,
                    "status": "failure",
                    "error": "task timed out",
                    "attempt": 3,
                }
            )
        ),
        use_bin_type=True,
    )
    engine_any._completion_queue.put(packed_event)

    drained_registry, drained_completion = engine._drain_shutdown_ipc_once()

    assert drained_registry == 2
    assert drained_completion == 1
    assert engine_any._in_flight_registry == {
        key: {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "requeue_attempts": 0,
            "timed_out": True,
            "timeout_error": "task timed out",
            "attempt": 3,
        }
    }
    assert len(engine_any._prefetched_completion_events) == 1
    prefetched = engine_any._prefetched_completion_events[0]
    assert prefetched.status == CompletionStatus.FAILURE
    assert prefetched.offset == 42


@pytest.mark.asyncio
async def test_poll_completed_events_ignores_queue_empty_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._prefetched_completion_events = []
    engine_any._logger = logging.getLogger(__name__)
    engine_any._in_flight_count = 0
    engine_any._in_flight_lock = Mock()
    engine_any._drain_registry_events = Mock()
    engine_any._ensure_workers_alive = Mock()

    class _RacyQueue:
        def empty(self) -> bool:
            return False

        def get_nowait(self):
            raise queue.Empty()

    engine_any._completion_queue = _RacyQueue()

    error_messages: list[str] = []

    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine._logger.error",
        lambda message, *args, **kwargs: error_messages.append(
            message % args if args else message
        ),
    )

    completed = await engine.poll_completed_events()

    assert completed == []
    assert error_messages == []
