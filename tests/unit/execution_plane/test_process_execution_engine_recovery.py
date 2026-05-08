# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_recovery.py
# Role: Verifies dead-worker recovery, retry caps, registry draining, and worker-pipe requeue behavior.
# Extend here for focused process execution engine regression coverage in this area.

from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    CompletionEvent,
    CompletionStatus,
    ExecutionConfig,
    ExecutionMode,
    Mock,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteIdentity,
    TopicPartition,
    WorkerPipesProcessTransport,
    WorkItem,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _CountingAliveWorker,
    _DeadWorker,
    _PipeSender,
    _RequeueRecordingTransport,
    _work_item_from_dict,
    _work_item_to_dict,
    cast,
    deque,
    logging,
    msgpack,
    pytest,
    queue,
    threading,
    time,
)


def test_ensure_workers_alive_stops_requeueing_after_max_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery inputs and fakes are prepared for ensure workers alive stops requeueing after max retries.
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

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that ensure workers alive stops requeueing after max retries.
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
    # Given: dead-worker recovery registry and worker state are prepared for ensure workers alive requeues pending worker pipe dispatch.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
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
    # When: the dead-worker recovery recovery path is exercised.
    # Then: the registry, queue, or completion state confirms that ensure workers alive requeues pending worker pipe dispatch.
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
    # Given: dead-worker recovery registry and worker state are prepared for ensure workers alive restarts dead worker before pipe requeue.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
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
    # When: the dead-worker recovery recovery path is exercised.
    # Then: the registry, queue, or completion state confirms that ensure workers alive restarts dead worker before pipe requeue.
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
    # Given: dead-worker recovery inputs are prepared to exercise ensure workers alive emits failures when restart fails after recovery.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
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
    # When: the dead-worker recovery path produces diagnostics or completion output.
    # Then: the emitted output confirms that ensure workers alive emits failures when restart fails after recovery.
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


def test_publish_recovered_worker_payloads_emits_failure_when_requeue_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery inputs are prepared to exercise publish recovered worker payloads emits failure when requeue fails.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._completion_queue = queue.Queue()
    engine_any._logger = logging.getLogger(__name__)
    payload = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 2,
    }

    def fail_requeue(_payloads: list[dict[str, Any]]) -> None:
        raise RuntimeError("queue full")

    monkeypatch.setattr(engine, "_requeue_recovered_payloads", fail_requeue)

    engine._publish_recovered_worker_payloads(0, [payload])

    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    # When: the dead-worker recovery path produces diagnostics or completion output.
    # Then: the emitted output confirms that publish recovered worker payloads emits failure when requeue fails.
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_requeue_failed: queue full"
    assert event.offset == 42
    assert event.attempt == 3


def test_publish_recovered_worker_payloads_emits_only_failed_partial_requeues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery inputs are prepared to exercise publish recovered worker payloads emits only failed partial requeues.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._completion_queue = queue.Queue()
    engine_any._logger = logging.getLogger(__name__)
    payloads = [
        {
            "id": "work-41",
            "topic": "topic",
            "partition": 1,
            "offset": 41,
            "epoch": 7,
            "requeue_attempts": 1,
        },
        {
            "id": "work-42",
            "topic": "topic",
            "partition": 1,
            "offset": 42,
            "epoch": 7,
            "requeue_attempts": 1,
        },
    ]
    requeued: list[list[dict[str, Any]]] = []

    def maybe_requeue(batch: list[dict[str, Any]]) -> None:
        requeued.append(batch)
        if batch[0]["offset"] == 42:
            raise RuntimeError("pipe closed")

    monkeypatch.setattr(engine, "_requeue_recovered_payloads", maybe_requeue)

    engine._publish_recovered_worker_payloads(0, payloads)

    # When: the dead-worker recovery path produces diagnostics or completion output.
    # Then: the emitted output confirms that publish recovered worker payloads emits only failed partial requeues.
    assert requeued == [[payloads[0]], [payloads[1]]]
    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_requeue_failed: pipe closed"
    assert event.offset == 42
    with pytest.raises(queue.Empty):
        engine_any._completion_queue.get_nowait()


def test_ensure_workers_alive_emits_failures_for_in_flight_work_when_restart_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery inputs are prepared to exercise ensure workers alive emits failures for in-flight work when restart fails.
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

    # When: the dead-worker recovery path produces diagnostics or completion output.
    # Then: the emitted output confirms that ensure workers alive emits failures for in-flight work when restart fails.
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
    # Given: dead-worker recovery inputs and fakes are prepared for ensure workers alive prefetches completion before dead worker requeue.
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

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that ensure workers alive prefetches completion before dead worker requeue.
    assert requeued == []
    assert engine_any._in_flight_registry == {}
    assert list(engine_any._prefetched_completion_events) == [completion]


def test_worker_pipe_slot_wait_signals_engine_recovery_for_dead_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery inputs and fakes are prepared for worker pipe slot wait signals engine recovery for dead worker.
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

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that worker pipe slot wait signals engine recovery for dead worker.
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
    # Given: dead-worker recovery inputs and fakes are prepared for worker pipe slot wait blocks healthy worker until start event.
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
    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that worker pipe slot wait blocks healthy worker until start event.
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
    # Given: dead-worker recovery state is prepared for worker pipe slot wait reentrant owner drains events and releases slot.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that worker pipe slot wait reentrant owner drains events and releases slot.
    assert engine_any._registry_event_queue.empty()
    assert list(engine_any._prefetched_completion_events) == [completion]
    assert worker.is_alive_calls == 1
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_slot_wait_cross_thread_contention_noops_until_owner_releases() -> (
    None
):
    # Given: dead-worker recovery inputs and fakes are prepared for worker pipe slot wait cross thread contention noops until owner releases.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    senders = [_PipeSender()]
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=1,
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

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that worker pipe slot wait cross thread contention noops until owner releases.
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
    # Given: dead-worker recovery inputs and fakes are prepared for ensure workers alive caps pending worker pipe retries.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(
            process_count=1,
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
    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that ensure workers alive caps pending worker pipe retries.
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


def test_pending_worker_pipe_filter_caps_retry_boundary() -> None:
    # Given: dead-worker recovery inputs and fakes are prepared for pending worker pipe filter caps retry boundary.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._completion_queue = queue.Queue()
    engine_any._logger = logging.getLogger(__name__)
    payload = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 3,
    }

    recoverable = engine._filter_recoverable_pending_pipe_dispatches(0, [payload])

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that pending worker pipe filter caps retry boundary.
    assert recoverable == []
    raw_event = engine_any._completion_queue.get_nowait()
    event = _completion_event_from_dict(msgpack.unpackb(raw_event, raw=False))
    assert event.status == CompletionStatus.FAILURE
    assert event.error == "worker_died_max_retries"
    assert event.attempt == 3


def test_pending_worker_pipe_filter_allows_final_retry() -> None:
    # Given: dead-worker recovery inputs and fakes are prepared for pending worker pipe filter allows final retry.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._completion_queue = queue.Queue()
    engine_any._logger = logging.getLogger(__name__)
    payload = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "requeue_attempts": 2,
    }

    recoverable = engine._filter_recoverable_pending_pipe_dispatches(0, [payload])

    # When: the relevant dead-worker recovery code path is exercised.
    # Then: the assertions confirm that pending worker pipe filter allows final retry.
    assert recoverable == [{**payload, "requeue_attempts": 3}]
    assert engine_any._completion_queue.empty()


def test_ensure_workers_alive_throttles_liveness_scan_but_drains_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: dead-worker recovery state is prepared for ensure workers alive throttles liveness scan but drains registry.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that ensure workers alive throttles liveness scan but drains registry.
    assert key in engine_any._in_flight_registry
    assert worker.is_alive_calls == 0

    monkeypatch.setattr(time, "monotonic", lambda: 101.1)
    engine._ensure_workers_alive()

    assert worker.is_alive_calls == 1


def test_drain_registry_events_applies_start_and_timeout_sequence() -> None:
    # Given: dead-worker recovery state is prepared for drain registry events applies start and timeout sequence.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that drain registry events applies start and timeout sequence.
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
    # Given: dead-worker recovery state is prepared for drain registry event queue returns drained count.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that drain registry event queue returns drained count.
    assert drained == 2
    assert engine_any._in_flight_registry[key]["timed_out"] is True
    assert engine_any._in_flight_registry[key]["attempt"] == 2


def test_recover_dead_worker_items_emits_timeout_failure_and_requeues_retryable_work():
    # Given: dead-worker recovery inputs are prepared to exercise recover dead worker items emits timeout failure and requeues retryable work.
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

    # When: the dead-worker recovery path produces diagnostics or completion output.
    # Then: the emitted output confirms that recover dead worker items emits timeout failure and requeues retryable work.
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
    # Given: dead-worker recovery state is prepared for drain shutdown IPC once reuses registry event rules and prefetches completion.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that drain shutdown IPC once reuses registry event rules and prefetches completion.
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
