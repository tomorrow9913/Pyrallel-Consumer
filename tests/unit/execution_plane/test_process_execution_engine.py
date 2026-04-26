import asyncio
import logging
import queue
import threading
import time
from collections.abc import AsyncGenerator
from multiprocessing.connection import Connection
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

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
from pyrallel_consumer.execution_plane.process_transport import RouteIdentity
from pyrallel_consumer.execution_plane.process_transport_shared_queue import (
    SharedQueueProcessTransport,
)
from pyrallel_consumer.execution_plane.process_transport_worker_pipes import (
    WorkerPipesProcessTransport,
)
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)


class _DeadWorker:
    exitcode = 1

    def is_alive(self) -> bool:
        return False


class _CountingAliveWorker:
    exitcode = None

    def __init__(self) -> None:
        self.is_alive_calls = 0

    def is_alive(self) -> bool:
        self.is_alive_calls += 1
        return True


class _BrokenPipeSender:
    def send_bytes(self, _payload: bytes) -> None:
        raise BrokenPipeError("boom")


async def _async_worker(_item) -> None:
    return None


def _sync_worker(_item) -> None:
    return None


def _contract_worker(item: WorkItem) -> None:
    if item.payload == b"fail":
        raise ValueError("simulated worker failure")


class _PipeSender:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.closed = False

    def send_bytes(self, payload: bytes) -> None:
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed = True


class _ScriptedSemaphore:
    def __init__(self, acquire_results: list[bool]) -> None:
        self.acquire_results = acquire_results
        self.acquire_calls: list[tuple[bool, float | None]] = []
        self.release_calls = 0

    def acquire(
        self,
        blocking: bool = True,
        timeout: float | None = None,
    ) -> bool:
        self.acquire_calls.append((blocking, timeout))
        if self.acquire_results:
            return self.acquire_results.pop(0)
        return False

    def release(self) -> None:
        self.release_calls += 1


class _ExplodingSerializer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __call__(self, _batch: list[WorkItem], _flush_enqueued_at: float) -> bytes:
        return self.payload


class _FakeProcess:
    def __init__(self, target=None, args=()) -> None:
        self.target = target
        self.args = args
        self.pid = 4321
        self.started = False

    def start(self) -> None:
        self.started = True


class _RequeueRecordingTransport:
    def __init__(self) -> None:
        self.requeued_payloads: list[list[dict[str, Any]]] = []

    async def submit_work_item(
        self,
        work_item: WorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        del work_item, route_identity, count_in_flight

    def dispatch_payload(
        self,
        payload: dict[str, Any],
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        del payload, route_identity, count_in_flight

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        del idx
        return object(), False

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        del event

    def recover_pending_dispatches(self, idx: int) -> list[dict[str, Any]]:
        del idx
        return []

    def signal_shutdown(self, worker_count: int) -> None:
        del worker_count

    def close(self) -> None:
        return None

    def requeue_payloads(self, payloads: list[dict[str, Any]]) -> None:
        self.requeued_payloads.append(payloads)

    def clear_pending_dispatches(self) -> None:
        return None


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


def test_process_execution_engine_rejects_worker_pipe_batching_configs() -> None:
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=7,
            transport_mode="worker_pipes",
            batch_size=2,
        ),
    )

    with pytest.raises(ValueError, match="worker_pipes"):
        ProcessExecutionEngine(config=config, worker_fn=_sync_worker)


@pytest.mark.parametrize(
    ("process_kwargs", "match"),
    [
        (
            {
                "batch_size": 1,
                "max_batch_wait_ms": 1,
            },
            "rejects timer batching",
        ),
        (
            {
                "batch_size": 1,
                "max_batch_wait_ms": 0,
                "flush_policy": "demand",
            },
            "rejects flush_policy=demand",
        ),
        (
            {
                "batch_size": 1,
                "max_batch_wait_ms": 0,
                "demand_flush_min_residence_ms": 1,
            },
            "demand_flush_min_residence_ms>0",
        ),
        (
            {
                "batch_size": 1,
                "max_batch_wait_ms": 0,
                "max_tasks_per_child": 1,
            },
            "max_tasks_per_child",
        ),
        (
            {
                "batch_size": 1,
                "max_batch_wait_ms": 0,
                "recycle_jitter_ms": 1,
            },
            "recycle_jitter_ms",
        ),
    ],
)
def test_process_execution_engine_rejects_unsupported_worker_pipe_slice_combinations(
    process_kwargs: dict[str, Any],
    match: str,
) -> None:
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=7,
            transport_mode="worker_pipes",
            **process_kwargs,
        ),
    )

    with pytest.raises(ValueError, match=match):
        ProcessExecutionEngine(config=config, worker_fn=_sync_worker)


def test_process_execution_engine_defaults_to_shared_queue_transport_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(process_count=1, queue_size=7),
        ),
        worker_fn=_sync_worker,
    )

    try:
        assert isinstance(cast(Any, engine)._transport, SharedQueueProcessTransport)
    finally:
        asyncio.run(engine.shutdown())


def test_requeue_recovered_payloads_uses_shared_queue_transport_seam() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._transport_mode = "shared_queue"
    engine_any._task_queue = None
    transport = _RequeueRecordingTransport()
    engine_any._transport = transport

    payloads = [
        {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
        }
    ]

    engine._requeue_recovered_payloads(payloads)

    assert transport.requeued_payloads == [payloads]


def test_worker_pipe_transport_slot_timeout_is_independent_of_task_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(
                process_count=1,
                queue_size=1,
                transport_mode="worker_pipes",
                batch_size=1,
                max_batch_wait_ms=0,
                task_timeout_ms=0,
            ),
        ),
        worker_fn=_sync_worker,
    )

    try:
        transport = cast(Any, engine)._transport
        assert isinstance(transport, WorkerPipesProcessTransport)
        assert transport._slot_acquire_timeout_ms == 30000
    finally:
        asyncio.run(engine.shutdown())


def test_process_execution_engine_selects_worker_pipe_transport_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(
                process_count=2,
                queue_size=7,
                transport_mode="worker_pipes",
                batch_size=1,
                max_batch_wait_ms=0,
            ),
        ),
        worker_fn=_sync_worker,
    )

    try:
        assert isinstance(cast(Any, engine)._transport, WorkerPipesProcessTransport)
    finally:
        asyncio.run(engine.shutdown())


@pytest.mark.parametrize("transport_mode", ["shared_queue", "worker_pipes"])
def test_start_worker_keeps_single_parent_completion_queue(
    monkeypatch: pytest.MonkeyPatch,
    transport_mode: str,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._completion_queue = object()
    engine_any._registry_event_queue = object()
    engine_any._worker_fn = _sync_worker
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=7,
            transport_mode=cast(Any, transport_mode),
            batch_size=1 if transport_mode == "worker_pipes" else 64,
            max_batch_wait_ms=0 if transport_mode == "worker_pipes" else 5,
        ),
    )
    engine_any._log_queue = object()
    engine_any._worker_pid_by_index = {}
    engine_any._logger = logging.getLogger(__name__)

    task_source = object()
    engine_any._transport = Mock()
    engine_any._transport.start_worker_task_source.return_value = (task_source, False)

    created_processes: list[_FakeProcess] = []

    def _fake_process(*, target, args):
        process = _FakeProcess(target=target, args=args)
        created_processes.append(process)
        return process

    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.Process",
        _fake_process,
    )

    worker = cast(_FakeProcess, engine._start_worker(0))

    assert worker is created_processes[0]
    assert worker.started is True
    assert worker.args[1] is engine_any._completion_queue


@pytest.mark.asyncio
async def test_submit_resolves_route_identity_before_transport_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(process_count=1, queue_size=7),
        ),
        worker_fn=_sync_worker,
    )
    captured: list[tuple[RouteIdentity, bool]] = []

    class _FakeTransport:
        async def submit_work_item(
            self,
            work_item: WorkItem,
            *,
            route_identity: RouteIdentity,
            count_in_flight: bool,
        ) -> None:
            del work_item
            captured.append((route_identity, count_in_flight))

        def signal_shutdown(self, worker_count: int) -> None:
            del worker_count

        def clear_pending_dispatches(self) -> None:
            return None

        def close(self) -> None:
            return None

    cast(Any, engine)._transport = _FakeTransport()
    item = WorkItem(
        id="work-route",
        tp=TopicPartition("topic", 3),
        offset=11,
        epoch=2,
        key=b"route-key",
        payload=b"payload",
    )

    try:
        await engine.submit(item)
    finally:
        await engine.shutdown()

    assert captured == [(RouteIdentity("topic", 3, b"route-key"), True)]


@pytest.mark.asyncio
async def test_submit_routes_matching_identities_to_same_worker_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=2,
            queue_size=4,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    senders = [_PipeSender(), _PipeSender()]
    engine_any = cast(Any, engine)
    engine_any._worker_pipe_senders.clear()
    engine_any._worker_pipe_senders.extend(senders)

    item_a = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 0),
        offset=1,
        epoch=1,
        key=b"same-key",
        payload=b"a",
    )
    item_b = WorkItem(
        id="work-b",
        tp=TopicPartition("topic", 0),
        offset=2,
        epoch=1,
        key=b"same-key",
        payload=b"b",
    )

    try:
        await engine.submit(item_a)
        await engine.submit(item_b)

        payload_counts = [len(sender.payloads) for sender in senders]
        assert payload_counts in ([2, 0], [0, 2])
        assert engine.get_in_flight_count() == 2
    finally:
        await engine.shutdown()


def test_worker_pipe_start_event_releases_pending_dispatch_capacity() -> None:
    engine = cast(
        ProcessExecutionEngine,
        ProcessExecutionEngine.__new__(ProcessExecutionEngine),
    )
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {}
    engine_any._transport_mode = "worker_pipes"
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    engine_any._transport = transport
    cast(Any, transport._pending_dispatch)[(0, "topic", 1, 42)] = {"offset": 42}

    acquired = transport._worker_pipe_queue_slots.acquire(blocking=False)
    assert acquired is True
    acquired_again = transport._worker_pipe_queue_slots.acquire(blocking=False)
    assert acquired_again is False

    engine._apply_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-42",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 1,
            },
        }
    )

    assert (0, "topic", 1, 42) not in transport._pending_dispatch
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_pending_dispatch_key_preserves_redelivered_same_offset() -> None:
    senders = [_PipeSender()]
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=2,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    first_payload = _work_item_to_dict(
        WorkItem(
            id="work-first",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload",
        )
    )
    redelivered_payload = _work_item_to_dict(
        WorkItem(
            id="work-redelivered",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=8,
            key=b"same-key",
            payload=b"payload",
        )
    )

    for payload in (first_payload, redelivered_payload):
        transport.dispatch_payload(
            payload,
            route_identity=RouteIdentity("topic", 1, b"same-key"),
            count_in_flight=False,
        )

    assert len(transport._pending_dispatch) == 2

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )

    assert list(transport._pending_dispatch.values()) == [redelivered_payload]
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_transport_retries_slot_acquire_after_liveness_check() -> None:
    senders = [_PipeSender()]
    ensure_calls: list[str] = []
    semaphore = _ScriptedSemaphore([False, True])
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        ensure_workers_alive=lambda: ensure_calls.append("called"),
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_acquire_timeout_ms=100,
    )
    transport._worker_pipe_queue_slots = semaphore  # type: ignore[assignment]

    payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload",
        )
    )

    transport.dispatch_payload(
        payload,
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )

    assert ensure_calls == ["called"]
    assert semaphore.acquire_calls[0][0] is True
    assert senders[0].payloads == [b"packed"]
    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-42", 7): payload,
    }


def test_worker_pipe_transport_fails_fast_when_slot_never_opens() -> None:
    senders = [_PipeSender()]
    ensure_calls: list[str] = []
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        ensure_workers_alive=lambda: ensure_calls.append("called"),
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_acquire_timeout_ms=0,
    )
    transport._worker_pipe_queue_slots = _ScriptedSemaphore([False])  # type: ignore[assignment]

    payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload",
        )
    )

    with pytest.raises(TimeoutError, match="worker pipe slot worker=0 offset=42"):
        transport.dispatch_payload(
            payload,
            route_identity=RouteIdentity("topic", 1, b"same-key"),
            count_in_flight=False,
        )

    assert ensure_calls == []
    assert senders[0].payloads == []
    assert transport._pending_dispatch == {}


def test_worker_pipe_transport_releases_pending_slot_when_send_fails() -> None:
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [_BrokenPipeSender()],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_acquire_timeout_ms=0,
    )

    payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload",
        )
    )

    with pytest.raises(
        RuntimeError, match="Failed to dispatch worker pipe payload worker=0 offset=42"
    ):
        transport.dispatch_payload(
            payload,
            route_identity=RouteIdentity("topic", 1, b"same-key"),
            count_in_flight=False,
        )

    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


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


def test_ensure_workers_alive_requeues_pending_worker_pipe_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._drain_registry_events = lambda: None  # type: ignore[method-assign]
    engine_any._drain_registry_event_queue = lambda: 0  # type: ignore[method-assign]
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    engine_any._transport = transport
    transport._pending_dispatch[(0, "topic", 1, 42, "work-42", 7)] = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 0,
    }
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

    requeued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(
        engine,
        "_requeue_recovered_payloads",
        lambda payloads: requeued.append(payloads),
    )
    replacement_worker = Mock()
    monkeypatch.setattr(engine, "_start_worker", lambda idx: replacement_worker)

    engine._ensure_workers_alive()

    assert requeued == [
        [
            {
                "id": "work-42",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
                "requeue_attempts": 1,
            }
        ]
    ]
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True
    assert engine_any._workers == [replacement_worker]


def test_ensure_workers_alive_caps_pending_worker_pipe_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._completion_queue = queue.Queue()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._drain_registry_events = lambda: None  # type: ignore[method-assign]
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    engine_any._transport = transport
    transport._pending_dispatch[(0, "topic", 1, 42, "work-42", 7)] = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 3,
    }
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

    requeued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(
        engine,
        "_requeue_recovered_payloads",
        lambda payloads: requeued.append(payloads),
    )
    monkeypatch.setattr(engine, "_start_worker", lambda idx: Mock())

    engine._ensure_workers_alive()

    assert requeued == []
    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_died_max_retries"
    assert event.attempt == 3


def test_ensure_workers_alive_throttles_liveness_scan_but_drains_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    worker = _CountingAliveWorker()
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._last_worker_liveness_check = 100.0
    engine_any._worker_liveness_check_interval_seconds = 1.0

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
    monkeypatch.setattr(time, "monotonic", lambda: 100.5)

    engine._ensure_workers_alive()

    assert key in engine_any._in_flight_registry
    assert worker.is_alive_calls == 0

    monkeypatch.setattr(time, "monotonic", lambda: 101.1)
    engine._ensure_workers_alive()

    assert worker.is_alive_calls == 1


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


@pytest.mark.asyncio
async def test_wait_for_completion_ignores_false_empty_signal() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._prefetched_completion_events = []
    engine_any._drain_registry_events = Mock()
    engine_any._ensure_workers_alive = Mock()

    class _LyingEmptyQueue:
        def empty(self) -> bool:
            return False

        def get_nowait(self):
            raise queue.Empty()

        def get(self, *_args, **_kwargs):
            raise queue.Empty()

    engine_any._completion_queue = _LyingEmptyQueue()

    completed = await engine.wait_for_completion(timeout_seconds=0)

    assert completed is False


@pytest.mark.asyncio
async def test_wait_for_completion_detects_item_even_when_empty_lies_true() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._prefetched_completion_events = []
    engine_any._drain_registry_events = Mock()
    engine_any._ensure_workers_alive = Mock()

    packed_event = msgpack.packb(
        _completion_event_to_dict(
            _completion_event_from_dict(
                {
                    "id": "work-42",
                    "topic": "topic",
                    "partition": 1,
                    "offset": 42,
                    "epoch": 7,
                    "status": "success",
                    "attempt": 1,
                }
            )
        ),
        use_bin_type=True,
    )

    class _HiddenItemQueue:
        def __init__(self, raw_event: bytes) -> None:
            self._raw_event = raw_event
            self._drained = False

        def empty(self) -> bool:
            return True

        def get_nowait(self):
            if self._drained:
                raise queue.Empty()
            self._drained = True
            return self._raw_event

        def get(self, *_args, **_kwargs):
            raise AssertionError(
                "blocking get should not be used when get_nowait succeeds"
            )

    engine_any._completion_queue = _HiddenItemQueue(packed_event)

    completed = await engine.wait_for_completion(timeout_seconds=0)

    assert completed is True
    assert len(engine_any._prefetched_completion_events) == 1
    assert engine_any._prefetched_completion_events[0].offset == 42


@pytest.mark.asyncio
async def test_submit_checks_worker_liveness_before_transport_dispatch() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._drain_registry_events = Mock()
    engine_any._ensure_workers_alive = Mock()
    engine_any._transport_mode = "worker_pipes"
    engine_any._transport = Mock()
    engine_any._transport.submit_work_item = AsyncMock()

    item = WorkItem(
        id="work-1",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=3,
        key=b"key",
        payload=b"payload",
    )

    await engine.submit(item)

    engine_any._ensure_workers_alive.assert_called_once_with(force=True)
    engine_any._transport.submit_work_item.assert_awaited_once()


def test_ensure_workers_alive_force_bypasses_liveness_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    worker = _CountingAliveWorker()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._last_worker_liveness_check = 100.0
    engine_any._worker_liveness_check_interval_seconds = 1.0
    monkeypatch.setattr(time, "monotonic", lambda: 100.5)

    engine._ensure_workers_alive(force=True)

    assert worker.is_alive_calls == 1
    assert engine_any._last_worker_liveness_check == 100.5


def test_worker_pipe_shutdown_ignores_broken_senders() -> None:
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [cast(Connection, _BrokenPipeSender())],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )

    transport.signal_shutdown(1)


def test_worker_pipe_dispatch_rejects_oversized_payload_before_send() -> None:
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=2,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_ExplodingSerializer(b"abc"),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [cast(Connection, sender)],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )

    with pytest.raises(ValueError, match="payload_too_large"):
        transport.dispatch_payload(
            _work_item_to_dict(
                WorkItem(
                    id="work-1",
                    tp=TopicPartition("topic", 1),
                    offset=42,
                    epoch=3,
                    key=b"key",
                    payload=b"payload",
                )
            ),
            route_identity=RouteIdentity("topic", 1, b"key"),
            count_in_flight=True,
        )

    assert sender.payloads == []
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_dispatch_rejects_invalid_payload_before_send() -> None:
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_ExplodingSerializer(b"\xc1"),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [cast(Connection, sender)],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )

    with pytest.raises(ValueError, match="invalid_worker_pipe_payload"):
        transport.dispatch_payload(
            _work_item_to_dict(
                WorkItem(
                    id="work-1",
                    tp=TopicPartition("topic", 1),
                    offset=42,
                    epoch=3,
                    key=b"key",
                    payload=b"payload",
                )
            ),
            route_identity=RouteIdentity("topic", 1, b"key"),
            count_in_flight=True,
        )

    assert sender.payloads == []
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


@pytest.mark.asyncio
async def test_shutdown_delegates_signal_and_close_through_transport_seam() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = [Mock()]
    engine_any._in_flight_registry = {(0, "topic", 1, 42): {"offset": 42}}
    engine_any._workers = [Mock(), Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = None
    engine_any._registry_event_queue = None
    engine_any._batch_accumulator = Mock()
    engine_any._drain_registry_events = Mock()
    engine_any._drain_shutdown_ipc_once = Mock(return_value=(0, 0))
    engine_any._join_worker_with_escalation = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 3
    engine_any._worker_pid_by_index = {}
    transport = Mock()
    engine_any._transport = transport

    await engine.shutdown()

    transport.signal_shutdown.assert_called_once_with(2)
    transport.close.assert_called_once_with()
    engine_any._batch_accumulator.close.assert_called_once_with()
    assert engine_any._prefetched_completion_events == []
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 0
