import asyncio
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator
from multiprocessing.connection import Connection
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import msgpack
import pytest
import pytest_asyncio

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    ExecutionMode,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _serialize_batch_payload,
    _work_item_from_dict,
    _work_item_to_dict,
    _worker_loop,
)
from pyrallel_consumer.execution_plane.process_transport import (
    PendingDispatchRecovery,
    RouteIdentity,
    WorkerExecutionIdentity,
    logical_work_identity_from_payload,
)
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


class _JoinedWorker:
    pid = 9876
    exitcode = 0

    def __init__(self) -> None:
        self.join_calls = 0

    def join(self, timeout: float | None = None) -> None:
        del timeout
        self.join_calls += 1

    def is_alive(self) -> bool:
        return False


class _Closable:
    def __init__(self) -> None:
        self.closed = False
        self.stopped = False

    def close(self) -> None:
        self.closed = True

    def stop(self) -> None:
        self.stopped = True


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

    def recover_pending_dispatches(self, idx: int) -> list[PendingDispatchRecovery]:
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


def test_worker_pipe_transport_creation_does_not_reuse_task_timeout(
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
        assert not hasattr(transport, "_slot_acquire_timeout_ms")
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
    pending_key = (0, "topic", 1, 42, "work-42", 1)
    cast(Any, transport._pending_dispatch)[pending_key] = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 1,
    }

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

    assert pending_key not in transport._pending_dispatch
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_slot_wait_blocks_until_start_event_releases_capacity() -> None:
    sender = _PipeSender()
    liveness_checks = 0

    def record_liveness_check() -> None:
        nonlocal liveness_checks
        liveness_checks += 1

    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=record_liveness_check,
        slot_wait_timeout_seconds=0.01,
    )
    first_payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload-1",
        )
    )
    second_payload = _work_item_to_dict(
        WorkItem(
            id="work-43",
            tp=TopicPartition("topic", 1),
            offset=43,
            epoch=7,
            key=b"same-key",
            payload=b"payload-2",
        )
    )

    transport.dispatch_payload(
        first_payload,
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )

    errors: list[BaseException] = []

    def dispatch_second() -> None:
        try:
            transport.dispatch_payload(
                second_payload,
                route_identity=RouteIdentity("topic", 1, b"same-key"),
                count_in_flight=False,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=dispatch_second)
    thread.start()

    released_by_start_event = False
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if liveness_checks > 0:
                break
            time.sleep(0.01)

        assert liveness_checks > 0
        assert len(sender.payloads) == 1

        transport.handle_registry_event(
            {"kind": "start", "key": (0, "topic", 1, 42), "payload": first_payload}
        )
        released_by_start_event = True
        thread.join(timeout=1.0)
    finally:
        if thread.is_alive():
            if not released_by_start_event:
                transport.handle_registry_event(
                    {
                        "kind": "start",
                        "key": (0, "topic", 1, 42),
                        "payload": first_payload,
                    }
                )
            thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert len(sender.payloads) == 2
    assert transport._pending_dispatch == {
        (0, "topic", 1, 43, "work-43", 7): second_payload,
    }


def test_worker_pipe_start_event_requires_matching_identity_to_release_capacity() -> (
    None
):
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    original_payload = _work_item_to_dict(
        WorkItem(
            id="work-original",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload-original",
        )
    )
    redelivered_payload = {
        **original_payload,
        "id": "work-redelivered",
        "epoch": 8,
    }

    transport.dispatch_payload(
        original_payload,
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )
    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": redelivered_payload,
        }
    )

    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-original", 7): original_payload
    }
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is False

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 43),
            "payload": original_payload,
        }
    )

    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-original", 7): original_payload
    }
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is False

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": original_payload,
        }
    )

    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_prefetch_completion_discards_only_matching_in_flight_identity() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-first",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        },
        (1, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        },
    }
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    completion = CompletionEvent(
        id="work-first",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )

    assert engine._prefetch_completed_events_from_queue() == 1

    assert list(engine_any._prefetched_completion_events) == [completion]
    assert engine_any._in_flight_registry == {
        (1, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        }
    }


def test_prefetch_completion_keeps_in_flight_when_identity_differs() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        }
    }
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    completion = CompletionEvent(
        id="work-first",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )

    assert engine._prefetch_completed_events_from_queue() == 1

    assert list(engine_any._prefetched_completion_events) == [completion]
    assert engine_any._in_flight_registry == {
        (0, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        }
    }


def test_registry_done_event_keeps_in_flight_when_identity_differs() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    current_payload = {
        "id": "work-redelivered",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 8,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {(0, "topic", 1, 42): current_payload}
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "done",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-first",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
            },
        }
    )

    assert engine_any._in_flight_registry == {(0, "topic", 1, 42): current_payload}


def test_worker_done_registry_event_uses_identity_payload_only() -> None:
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    work_item = WorkItem(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"large-key",
        payload=b"large-payload",
    )
    task_source.put([_work_item_to_dict(work_item)])
    task_source.put(None)

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        lambda _item: None,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    registry_events: list[dict[str, Any]] = []
    while not registry_event_queue.empty():
        registry_events.append(cast(dict[str, Any], registry_event_queue.get_nowait()))
    done_events = [event for event in registry_events if event.get("kind") == "done"]

    assert len(done_events) == 1
    assert done_events[0]["payload"] == {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
    }
    assert "key" not in done_events[0]["payload"]
    assert "payload" not in done_events[0]["payload"]


def test_registry_start_event_ignores_older_identity_when_identity_differs() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    current_payload = {
        "id": "work-redelivered",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 8,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {(0, "topic", 1, 42): current_payload}
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-first",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
            },
        }
    )

    assert engine_any._in_flight_registry == {(0, "topic", 1, 42): current_payload}


def test_registry_start_event_overwrites_stale_identity_when_epoch_advances() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    stale_payload = {
        "id": "work-first",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {(0, "topic", 1, 42): stale_payload}
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-redelivered",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 8,
            },
        }
    )

    assert engine_any._in_flight_registry == {
        (0, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        }
    }


def test_dead_worker_recovery_uses_superseding_start_identity() -> None:
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
            "id": "work-first",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        }
    }
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-redelivered",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 8,
            },
        }
    )

    to_requeue = engine._recover_dead_worker_items(0)

    assert len(to_requeue) == 1
    assert to_requeue[0]["id"] == "work-redelivered"
    assert to_requeue[0]["epoch"] == 8
    assert to_requeue[0]["requeue_attempts"] == 1
    assert engine_any._in_flight_registry == {}


def test_registry_done_event_removes_only_matching_identity() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    redelivered_payload = {
        "id": "work-redelivered",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 8,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-first",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        },
        (1, "topic", 1, 42): redelivered_payload,
    }
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "done",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-first",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
            },
        }
    )

    assert engine_any._in_flight_registry == {(1, "topic", 1, 42): redelivered_payload}


def test_registry_timeout_event_keeps_in_flight_when_identity_differs() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        }
    }
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "timeout",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-first",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
            },
            "attempt": 2,
            "timeout_error": "task_timeout",
        }
    )

    assert "timed_out" not in engine_any._in_flight_registry[(0, "topic", 1, 42)]
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["id"] == (
        "work-redelivered"
    )
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["epoch"] == 8


def test_registry_timeout_event_marks_only_matching_identity() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-first",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        },
        (1, "topic", 1, 42): {
            "id": "work-redelivered",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 8,
            "requeue_attempts": 0,
        },
    }
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "timeout",
            "key": (0, "topic", 1, 42),
            "payload": {
                "id": "work-first",
                "topic": "topic",
                "partition": 1,
                "offset": 42,
                "epoch": 7,
            },
            "attempt": 2,
            "timeout_error": "task_timeout",
        }
    )

    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["timed_out"] is True
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["attempt"] == 2
    assert "timed_out" not in engine_any._in_flight_registry[(1, "topic", 1, 42)]


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


def test_worker_pipe_start_event_keeps_pending_dispatch_when_identity_differs() -> None:
    senders = [_PipeSender()]
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
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
    cast(Any, transport._pending_dispatch)[
        (0, "topic", 1, 42, "work-redelivered", 8)
    ] = redelivered_payload
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )

    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-redelivered", 8): redelivered_payload
    }
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is False


def test_worker_pipe_transport_blocks_for_slot_without_reentrant_recovery() -> None:
    senders = [_PipeSender()]
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

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
    errors: list[BaseException] = []

    def dispatch() -> None:
        try:
            transport.dispatch_payload(
                payload,
                route_identity=RouteIdentity("topic", 1, b"same-key"),
                count_in_flight=False,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=dispatch)
    thread.start()
    time.sleep(0.15)

    assert thread.is_alive()
    assert not hasattr(transport, "_ensure_workers_alive")
    assert senders[0].payloads == []

    transport._worker_pipe_queue_slots.release()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert senders[0].payloads == [b"packed"]
    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-42", 7): payload,
    }


def test_worker_pipe_backpressure_waits_for_healthy_slot_without_recovery() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    worker = _CountingAliveWorker()
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    engine_any._start_worker = Mock()  # type: ignore[method-assign]
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=engine._signal_worker_pipe_slot_wait,
        slot_wait_timeout_seconds=0.01,
    )
    engine_any._transport = transport
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

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
    errors: list[BaseException] = []

    def dispatch() -> None:
        try:
            transport.dispatch_payload(
                payload,
                route_identity=RouteIdentity("topic", 1, b"same-key"),
                count_in_flight=False,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=dispatch)
    thread.start()

    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if worker.is_alive_calls > 0:
                break
            time.sleep(0.01)

        assert thread.is_alive()
        assert errors == []
        assert senders[0].payloads == []
        assert worker.is_alive_calls > 0
        engine_any._start_worker.assert_not_called()
        assert engine_any._in_flight_registry == {}
        assert list(engine_any._prefetched_completion_events) == []
        assert engine_any._completion_queue.empty()

        transport._worker_pipe_queue_slots.release()
        thread.join(timeout=1.0)
    finally:
        if thread.is_alive():
            transport._worker_pipe_queue_slots.release()
            thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert senders[0].payloads == [b"packed"]
    assert transport._pending_dispatch == {
        (0, "topic", 1, 42, "work-42", 7): payload,
    }


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


def test_worker_pipe_recover_pending_dispatches_returns_identity_metadata() -> None:
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [_PipeSender()],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
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
    transport._pending_dispatch[(0, "topic", 1, 42, "work-42", 7)] = payload
    assert transport.capabilities.pending_dispatch_recovery is True
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

    recovered = transport.recover_pending_dispatches(0)

    assert recovered == [
        PendingDispatchRecovery(
            identity=WorkerExecutionIdentity(
                worker_index=0,
                work=logical_work_identity_from_payload(payload),
            ),
            payload={**payload, "requeue_attempts": 1},
        )
    ]
    assert transport._pending_dispatch == {}
    assert transport.recover_pending_dispatches(0) == []
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is False


def test_shared_queue_transport_declares_no_pending_dispatch_recovery() -> None:
    transport = SharedQueueProcessTransport(
        task_queue=cast(Any, queue.Queue()),
        get_batch_accumulator=Mock(),
        work_item_from_dict=_work_item_from_dict,
        increment_in_flight=lambda: None,
        sentinel=b"sentinel",
    )

    assert transport.capabilities.pending_dispatch_recovery is False
    assert transport.recover_pending_dispatches(0) == []


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
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
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


def test_ensure_workers_alive_restarts_dead_worker_before_pipe_requeue(
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
    engine_any._prefetched_completion_events = deque()
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
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
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

    order: list[str] = []

    def start_worker(_idx: int) -> Mock:
        order.append("restart")
        return Mock()

    def requeue_payloads(_payloads: list[dict[str, Any]]) -> None:
        order.append("requeue")
        assert order == ["restart", "requeue"]

    monkeypatch.setattr(engine, "_start_worker", start_worker)
    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeue_payloads)

    engine._ensure_workers_alive()

    assert order == ["restart", "requeue"]


def test_ensure_workers_alive_emits_failures_when_restart_fails_after_recovery(
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
    engine_any._prefetched_completion_events = deque()
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
        serialize_batch_payload=lambda _batch, _flush_enqueued_at: b"packed",
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
    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeued.append)

    def fail_start(_idx: int) -> None:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(engine, "_start_worker", fail_start)

    engine._ensure_workers_alive()

    assert requeued == []
    assert transport._pending_dispatch == {}
    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_restart_failed: spawn failed"
    assert event.offset == 42
    assert event.attempt == 3


def test_ensure_workers_alive_emits_failures_for_in_flight_work_when_restart_fails(
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
        }
    }
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._drain_registry_events = lambda: None  # type: ignore[method-assign]
    engine_any._drain_registry_event_queue = lambda: 0  # type: ignore[method-assign]
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    transport = _RequeueRecordingTransport()
    engine_any._transport = transport

    def fail_start(_idx: int) -> None:
        raise RuntimeError("spawn failed")

    monkeypatch.setattr(engine, "_start_worker", fail_start)

    engine._ensure_workers_alive()

    assert transport.requeued_payloads == []
    assert engine_any._in_flight_registry == {}
    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_restart_failed: spawn failed"
    assert event.offset == 42
    assert event.attempt == 3


def test_ensure_workers_alive_prefetches_completion_before_dead_worker_requeue(
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
        }
    }
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._workers = [_DeadWorker()]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._drain_registry_events = lambda: None  # type: ignore[method-assign]
    engine_any._drain_registry_event_queue = lambda: 0  # type: ignore[method-assign]
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )

    requeued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeued.append)
    monkeypatch.setattr(engine, "_start_worker", lambda _idx: Mock())

    engine._ensure_workers_alive()

    assert requeued == []
    assert engine_any._in_flight_registry == {}
    assert list(engine_any._prefetched_completion_events) == [completion]


def test_worker_pipe_slot_wait_signals_engine_recovery_for_dead_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    replacement_worker = Mock()
    senders = [_PipeSender()]

    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
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
        serialize_batch_payload=lambda batch, _flush_enqueued_at: (
            f"packed:{batch[0].offset}".encode()
        ),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=engine._signal_worker_pipe_slot_wait,
        slot_wait_timeout_seconds=0.01,
    )
    engine_any._transport = transport
    monkeypatch.setattr(engine, "_start_worker", lambda idx: replacement_worker)

    first_payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload-1",
        )
    )
    second_payload = _work_item_to_dict(
        WorkItem(
            id="work-43",
            tp=TopicPartition("topic", 1),
            offset=43,
            epoch=7,
            key=b"same-key",
            payload=b"payload-2",
        )
    )

    transport.dispatch_payload(
        first_payload,
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )

    errors: list[BaseException] = []

    def dispatch_second() -> None:
        try:
            transport.dispatch_payload(
                second_payload,
                route_identity=RouteIdentity("topic", 1, b"same-key"),
                count_in_flight=False,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=dispatch_second)
    thread.start()

    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if engine_any._workers == [replacement_worker]:
            break
        time.sleep(0.01)

    assert engine_any._workers == [replacement_worker]
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        if len(senders[0].payloads) >= 2:
            break
        time.sleep(0.01)
    assert senders[0].payloads[:2] == [b"packed:42", b"packed:42"]

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {
                **first_payload,
                "requeue_attempts": 1,
            },
        }
    )

    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert senders[0].payloads == [b"packed:42", b"packed:42", b"packed:43"]
    assert transport._pending_dispatch == {
        (0, "topic", 1, 43, "work-43", 7): second_payload,
    }


def test_worker_pipe_slot_wait_blocks_healthy_worker_until_start_event() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    worker = _CountingAliveWorker()

    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._last_worker_liveness_check = 0.0
    engine_any._worker_liveness_check_interval_seconds = 0.0
    engine_any._record_main_to_worker_ipc = lambda *_args, **_kwargs: None
    engine_any._record_worker_exec = lambda *_args, **_kwargs: None
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda batch, _flush_enqueued_at: (
            f"packed:{batch[0].offset}".encode()
        ),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=engine._signal_worker_pipe_slot_wait,
        slot_wait_timeout_seconds=0.01,
    )
    engine_any._transport = transport

    first_payload = _work_item_to_dict(
        WorkItem(
            id="work-42",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"same-key",
            payload=b"payload-1",
        )
    )
    second_payload = _work_item_to_dict(
        WorkItem(
            id="work-43",
            tp=TopicPartition("topic", 1),
            offset=43,
            epoch=7,
            key=b"same-key",
            payload=b"payload-2",
        )
    )
    transport.dispatch_payload(
        first_payload,
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )

    errors: list[BaseException] = []

    def dispatch_second() -> None:
        try:
            transport.dispatch_payload(
                second_payload,
                route_identity=RouteIdentity("topic", 1, b"same-key"),
                count_in_flight=False,
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=dispatch_second)
    thread.start()

    start_event_enqueued = False
    try:
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            if worker.is_alive_calls > 0:
                break
            time.sleep(0.01)

        assert worker.is_alive_calls > 0
        assert thread.is_alive()
        assert errors == []
        assert senders[0].payloads == [b"packed:42"]
        assert transport._pending_dispatch == {
            (0, "topic", 1, 42, "work-42", 7): first_payload,
        }

        engine_any._registry_event_queue.put(
            {
                "kind": "start",
                "key": (0, "topic", 1, 42),
                "payload": {**first_payload, "requeue_attempts": 0},
            }
        )
        start_event_enqueued = True
        thread.join(timeout=1.0)
    finally:
        if thread.is_alive():
            if not start_event_enqueued:
                engine_any._registry_event_queue.put(
                    {
                        "kind": "start",
                        "key": (0, "topic", 1, 42),
                        "payload": {**first_payload, "requeue_attempts": 0},
                    }
                )
            thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert senders[0].payloads == [b"packed:42", b"packed:43"]
    assert transport._pending_dispatch == {
        (0, "topic", 1, 43, "work-43", 7): second_payload,
    }
    assert engine_any._registry_event_queue.empty()


def test_worker_pipe_slot_wait_reentrant_owner_drains_events_and_releases_slot() -> (
    None
):
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    worker = _CountingAliveWorker()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    held_lock = threading.RLock()
    held_lock.acquire()
    engine_any._worker_slot_wait_liveness_lock = held_lock
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda batch, _flush_enqueued_at: (
            f"packed:{batch[0].offset}".encode()
        ),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=engine._signal_worker_pipe_slot_wait,
        slot_wait_timeout_seconds=0.01,
    )
    engine_any._transport = transport

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
    engine_any._registry_event_queue.put(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {**payload, "requeue_attempts": 0},
        }
    )
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )

    try:
        engine._signal_worker_pipe_slot_wait()
    finally:
        held_lock.release()

    assert engine_any._registry_event_queue.empty()
    assert list(engine_any._prefetched_completion_events) == [completion]
    assert worker.is_alive_calls == 1
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_slot_wait_cross_thread_contention_noops_until_owner_releases() -> (
    None
):
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
            transport_mode="worker_pipes",
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine_any._transport_mode = "worker_pipes"
    engine_any._in_flight_registry = {}
    engine_any._registry_event_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    worker = _CountingAliveWorker()
    engine_any._workers = [worker]
    engine_any._logger = logging.getLogger(__name__)
    liveness_lock = threading.RLock()
    liveness_lock.acquire()
    engine_any._worker_slot_wait_liveness_lock = liveness_lock
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=lambda batch, _flush_enqueued_at: (
            f"packed:{batch[0].offset}".encode()
        ),
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: senders,
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=engine._signal_worker_pipe_slot_wait,
        slot_wait_timeout_seconds=0.01,
    )
    engine_any._transport = transport

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
    engine_any._registry_event_queue.put(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": {**payload, "requeue_attempts": 0},
        }
    )
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    packed_completion = msgpack.packb(
        _completion_event_to_dict(completion), use_bin_type=True
    )
    engine_any._completion_queue.put(packed_completion)

    errors: list[BaseException] = []

    def signal_from_other_thread() -> None:
        try:
            engine._signal_worker_pipe_slot_wait()
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    thread = threading.Thread(target=signal_from_other_thread)
    thread.start()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert errors == []
    assert not engine_any._registry_event_queue.empty()
    assert list(engine_any._prefetched_completion_events) == []
    assert engine_any._in_flight_registry == {}
    assert worker.is_alive_calls == 0
    assert transport._pending_dispatch != {}

    liveness_lock.release()
    engine._signal_worker_pipe_slot_wait()

    assert engine_any._registry_event_queue.empty()
    assert list(engine_any._prefetched_completion_events) == [completion]
    assert worker.is_alive_calls == 1
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


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


def _make_shutdown_engine() -> (
    tuple[ProcessExecutionEngine, _RequeueRecordingTransport]
):
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(process_count=1, worker_join_timeout_ms=1),
    )
    engine_any._batch_accumulator = _Closable()
    engine_any._task_queue = queue.Queue()
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._in_flight_registry = {}
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 0
    engine_any._worker_pid_by_index = {0: 9876}
    engine_any._workers = [_JoinedWorker()]
    engine_any._logger = logging.getLogger(__name__)
    engine_any._log_listener = _Closable()
    transport = _RequeueRecordingTransport()
    engine_any._transport = transport
    return engine, transport


@pytest.mark.asyncio
async def test_shutdown_residual_work_is_diagnostic_only(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, _transport = _make_shutdown_engine()
    engine_any = cast(Any, engine)
    residual_payload = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): residual_payload,
    }
    engine_any._in_flight_count = 1

    async def fast_sleep(_delay: float) -> None:
        return None

    monotonic_values = iter([100.0, 100.0, 102.0])
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.time.monotonic",
        lambda: next(monotonic_values, 102.0),
    )
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        fast_sleep,
    )

    with caplog.at_level(logging.WARNING):
        await engine.shutdown()

    assert "Residual in-flight registry after shutdown drain" in caplog.text
    assert "topic-1@42 id=work-42 epoch=7" in caplog.text
    assert engine_any._completion_queue.empty()
    assert list(engine_any._prefetched_completion_events) == []
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_preserves_visible_completion_drained_before_cleanup() -> None:
    engine, _transport = _make_shutdown_engine()
    engine_any = cast(Any, engine)
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )
    engine_any._in_flight_count = 1

    await engine.shutdown()

    assert list(engine_any._prefetched_completion_events) == [completion]
    assert engine.get_in_flight_count() == 1
    assert await engine.wait_for_completion(timeout_seconds=0) is True
    assert await engine.poll_completed_events() == [completion]
    assert engine.get_in_flight_count() == 0


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
async def test_shutdown_delegates_signal_and_close_through_transport_seam(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    prefetched_event = Mock()
    engine_any._prefetched_completion_events = [prefetched_event]
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
        }
    }
    engine_any._workers = [Mock(), Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._drain_registry_events = Mock()
    engine_any._drain_shutdown_ipc_once = Mock(return_value=(0, 0))
    engine_any._join_worker_with_escalation = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 3
    engine_any._worker_pid_by_index = {}
    engine_any._emit_worker_recovery_failure = Mock()
    transport = Mock()
    engine_any._transport = transport

    with caplog.at_level(logging.WARNING):
        await engine.shutdown()

    transport.signal_shutdown.assert_called_once_with(2)
    transport.close.assert_called_once_with()
    engine_any._batch_accumulator.close.assert_called_once_with()
    assert engine_any._prefetched_completion_events == [prefetched_event]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1
    engine_any._emit_worker_recovery_failure.assert_not_called()
    assert engine_any._completion_queue.empty()
    assert "Residual in-flight registry after shutdown drain" in caplog.text
    assert "id=work-42 epoch=7" in caplog.text


@pytest.mark.asyncio
async def test_shutdown_worker_pipes_clears_pending_dispatches_without_requeueing() -> (
    None
):
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = []
    engine_any._in_flight_registry = {}
    engine_any._workers = [Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._join_worker_with_escalation = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 1
    engine_any._worker_pid_by_index = {}
    engine_any._ensure_workers_alive = Mock()
    engine_any._recover_pending_pipe_dispatches = Mock()
    engine_any._requeue_recovered_payloads = Mock()
    engine_any._emit_worker_recovery_failure = Mock()
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    payload = _work_item_to_dict(
        WorkItem(
            id="work-pending",
            tp=TopicPartition("topic", 1),
            offset=42,
            epoch=7,
            key=b"key",
            payload=b"payload",
        )
    )
    transport._pending_dispatch[(0, "topic", 1, 42, "work-pending", 7)] = payload
    engine_any._transport = transport

    await engine.shutdown()

    assert sender.payloads == [b"sentinel"]
    assert transport._pending_dispatch == {}
    engine_any._ensure_workers_alive.assert_not_called()
    engine_any._recover_pending_pipe_dispatches.assert_not_called()
    engine_any._requeue_recovered_payloads.assert_not_called()
    engine_any._emit_worker_recovery_failure.assert_not_called()
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_worker_pipes_drains_completion_before_joining_workers() -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = []
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        }
    }
    engine_any._workers = [Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 1
    engine_any._worker_pid_by_index = {}
    transport = Mock()
    engine_any._transport = transport
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    engine_any._completion_queue.put(
        msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
    )

    def assert_drained_before_join(_worker: object) -> None:
        assert engine_any._in_flight_registry == {}
        assert engine_any._prefetched_completion_events == [completion]

    engine_any._join_worker_with_escalation = Mock(
        side_effect=assert_drained_before_join
    )

    await engine.shutdown()

    transport.signal_shutdown.assert_called_once_with(1)
    engine_any._join_worker_with_escalation.assert_called_once()
    transport.clear_pending_dispatches.assert_called_once_with()
    transport.close.assert_called_once_with()
    assert engine_any._prefetched_completion_events == [completion]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1


@pytest.mark.asyncio
async def test_shutdown_runs_post_join_drain_before_local_cleanup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = []
    engine_any._in_flight_registry = {}
    engine_any._workers = [Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._drain_registry_events = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 0
    engine_any._worker_pid_by_index = {}
    order: list[str] = []

    def drain_once() -> tuple[int, int]:
        order.append("drain")
        if order.count("drain") == 1:
            return (0, 0)
        if order.count("drain") == 2:
            return (0, 1)
        return (0, 0)

    def join_worker(_worker: object) -> None:
        order.append("join")

    transport = Mock()
    transport.clear_pending_dispatches.side_effect = lambda: order.append("clear")
    transport.close.side_effect = lambda: order.append("close")
    engine_any._transport = transport
    engine_any._drain_shutdown_ipc_once = Mock(side_effect=drain_once)
    engine_any._join_worker_with_escalation = Mock(side_effect=join_worker)

    with caplog.at_level(logging.DEBUG):
        await engine.shutdown()

    assert order == ["drain", "join", "drain", "drain", "drain", "clear", "close"]
    assert engine_any._drain_shutdown_ipc_once.call_count == 4
    assert "ProcessExecutionEngine shutdown post-join drain" in caplog.text
    assert "completion_events=1" in caplog.text
    transport.signal_shutdown.assert_called_once_with(1)
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_waits_for_stable_empty_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = []
    engine_any._in_flight_registry = {}
    engine_any._workers = [Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 0
    engine_any._worker_pid_by_index = {}
    drain_results = [
        (0, 0),  # pre-join bounded drain observes no visible IPC
        (0, 0),  # first post-join pass is empty but not yet stable
        (0, 1),  # late completion becomes visible after one empty pass
        (0, 0),
        (0, 0),  # stable-empty post-join exit
    ]
    drain_calls: list[tuple[int, int]] = []

    async def fast_sleep(_delay: float) -> None:
        return None

    def drain_once() -> tuple[int, int]:
        result = drain_results.pop(0) if drain_results else (0, 0)
        drain_calls.append(result)
        return result

    def assert_stable_empty_before_local_cleanup() -> None:
        assert drain_calls == [(0, 0), (0, 0), (0, 1), (0, 0), (0, 0)]

    transport = Mock()
    transport.clear_pending_dispatches.side_effect = (
        assert_stable_empty_before_local_cleanup
    )
    engine_any._transport = transport
    engine_any._drain_shutdown_ipc_once = Mock(side_effect=drain_once)
    engine_any._join_worker_with_escalation = Mock()
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        fast_sleep,
    )

    await engine.shutdown()

    assert drain_calls == [(0, 0), (0, 0), (0, 1), (0, 0), (0, 0)]
    transport.clear_pending_dispatches.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_observes_stable_empty_after_deadline_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    drain_results = [
        (0, 1),
        (0, 0),
        (0, 0),
    ]
    drain_calls: list[tuple[int, int]] = []
    sleep_calls: list[float] = []

    def drain_once() -> tuple[int, int]:
        result = drain_results.pop(0) if drain_results else (0, 0)
        drain_calls.append(result)
        return result

    async def record_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monotonic_values = iter([0.0, 1.0, 1.0, 1.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values, 1.0))
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        record_sleep,
    )
    engine_any._drain_shutdown_ipc_once = Mock(side_effect=drain_once)

    result = await engine._drain_shutdown_ipc_until_stable_empty(
        max_seconds=0.05,
        stable_empty_passes=2,
    )

    assert result == (0, 1, 3)
    assert drain_calls == [(0, 1), (0, 0), (0, 0)]
    assert sleep_calls == [0.0, 0.0]


@pytest.mark.asyncio
async def test_shutdown_post_join_stable_empty_prefetches_real_late_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, transport = _make_shutdown_engine()
    engine_any = cast(Any, engine)
    engine_any._in_flight_count = 1
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    sleep_calls = 0

    async def enqueue_after_first_post_join_empty(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            engine_any._completion_queue.put(
                msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
            )

    def assert_prefetched_before_local_cleanup() -> None:
        assert list(engine_any._prefetched_completion_events) == [completion]

    monkeypatch.setattr(
        transport,
        "clear_pending_dispatches",
        Mock(side_effect=assert_prefetched_before_local_cleanup),
    )
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        enqueue_after_first_post_join_empty,
    )

    await engine.shutdown()

    assert list(engine_any._prefetched_completion_events) == [completion]
    assert await engine.wait_for_completion(timeout_seconds=0) is True
    assert await engine.poll_completed_events() == [completion]
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_post_join_late_completion_keeps_different_identity_registry(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine, transport = _make_shutdown_engine()
    engine_any = cast(Any, engine)
    current_payload = {
        "id": "work-redelivered",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 8,
        "requeue_attempts": 0,
    }
    engine_any._in_flight_registry = {(0, "topic", 1, 42): current_payload}
    engine_any._in_flight_count = 1
    stale_completion = CompletionEvent(
        id="work-first",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    sleep_calls = 0

    async def enqueue_after_first_post_join_empty(_delay: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls == 1:
            engine_any._completion_queue.put(
                msgpack.packb(
                    _completion_event_to_dict(stale_completion),
                    use_bin_type=True,
                )
            )

    monotonic_values = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values, 2.0))
    clear_pending_dispatches = Mock()
    monkeypatch.setattr(transport, "clear_pending_dispatches", clear_pending_dispatches)
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        enqueue_after_first_post_join_empty,
    )

    with caplog.at_level(logging.WARNING):
        await engine.shutdown()

    assert list(engine_any._prefetched_completion_events) == [stale_completion]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1
    assert "topic-1@42 id=work-redelivered epoch=8" in caplog.text
    clear_pending_dispatches.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_reconciles_late_completion_before_cleanup(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = False
    engine_any._prefetched_completion_events = []
    engine_any._in_flight_registry = {
        (0, "topic", 1, 42): {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 0,
        }
    }
    engine_any._workers = [Mock()]
    engine_any._task_queue = None
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._batch_accumulator = Mock()
    engine_any._drain_registry_events = Mock()
    engine_any._log_listener = Mock()
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 1
    engine_any._worker_pid_by_index = {}
    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )

    engine_any._prefetched_completion_events = []

    async def fast_sleep(_delay: float) -> None:
        return None

    monotonic_values = iter([0.0, 2.0])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values, 2.0))
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        fast_sleep,
    )

    def join_worker(_worker: object) -> None:
        engine_any._completion_queue.put(
            msgpack.packb(_completion_event_to_dict(completion), use_bin_type=True)
        )

    def assert_reconciled_before_local_cleanup() -> None:
        assert engine_any._in_flight_registry == {}
        assert engine_any._prefetched_completion_events == [completion]

    transport = Mock()
    transport.clear_pending_dispatches.side_effect = (
        assert_reconciled_before_local_cleanup
    )
    engine_any._transport = transport
    engine_any._join_worker_with_escalation = Mock(side_effect=join_worker)

    with caplog.at_level(logging.DEBUG):
        await engine.shutdown()

    engine_any._join_worker_with_escalation.assert_called_once()
    transport.clear_pending_dispatches.assert_called_once_with()
    transport.close.assert_called_once_with()
    assert engine_any._prefetched_completion_events == [completion]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1
    assert "ProcessExecutionEngine shutdown post-join drain" in caplog.text
    assert "completion_events=1" in caplog.text
    assert "residual_in_flight_registry=0" in caplog.text
