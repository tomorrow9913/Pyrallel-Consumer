# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_route_batch_dispatch.py
# Role: Verifies worker-pipe route-batch dispatch, not-started recovery, and identity-aware completion handling.
# Extend here for focused process execution engine regression coverage in this area.

from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    CompletionEvent,
    CompletionStatus,
    ExecutionConfig,
    ExecutionMode,
    Mock,
    PendingDispatchRecovery,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteBatch,
    RouteIdentity,
    TopicPartition,
    WorkerExecutionIdentity,
    WorkerPipesProcessTransport,
    WorkItem,
    _completion_event_to_dict,
    _PipeSender,
    _RequeueRecordingTransport,
    _serialize_batch_payload,
    _work_item_from_dict,
    _work_item_to_dict,
    _worker_loop,
    cast,
    deque,
    logging,
    logical_work_identity_from_payload,
    msgpack,
    pytest,
    queue,
    threading,
    time,
)


def test_worker_pipe_route_batch_slot_acquire_uses_representative_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: route-batch dispatch inputs and fakes are prepared for worker pipe route batch slot acquire uses representative payload.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    item = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"same-key",
        payload=b"a",
    )
    acquired_payloads: list[dict[str, Any]] = []
    original_acquire = transport._acquire_worker_pipe_queue_slot

    def capture_acquire(worker_idx: int, payload: dict[str, Any]) -> None:
        acquired_payloads.append(payload)
        original_acquire(worker_idx=worker_idx, payload=payload)

    monkeypatch.setattr(transport, "_acquire_worker_pipe_queue_slot", capture_acquire)

    transport.dispatch_route_batch(
        RouteBatch(
            batch_id="batch-representative",
            route_identity=("topic", 1, b"same-key"),
            worker_index=None,
            items=[item],
        ),
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )

    # When: the relevant route-batch dispatch code path is exercised.
    # Then: the assertions confirm that worker pipe route batch slot acquire uses representative payload.
    assert acquired_payloads == [_work_item_to_dict(item)]


def test_worker_pipe_route_batch_start_keeps_unstarted_tail_recoverable() -> None:
    # Given: route-batch dispatch state is prepared for the worker pipe route batch start keeps unstarted tail recoverable scenario.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    first_item = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"same-key",
        payload=b"a",
    )
    tail_item = WorkItem(
        id="work-b",
        tp=TopicPartition("topic", 1),
        offset=43,
        epoch=7,
        key=b"same-key",
        payload=b"b",
    )
    transport.dispatch_route_batch(
        RouteBatch(
            batch_id="batch-tail",
            route_identity=("topic", 1, b"same-key"),
            worker_index=None,
            items=[first_item, tail_item],
        ),
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )
    first_payload = _work_item_to_dict(first_item)
    tail_payload = _work_item_to_dict(tail_item)

    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )

    # When: the relevant route-batch dispatch event or operation is applied.
    # Then: the test asserts that worker pipe route batch start keeps unstarted tail recoverable.
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True
    recovered = transport.recover_pending_dispatches(0)
    assert recovered == [
        PendingDispatchRecovery(
            identity=WorkerExecutionIdentity(
                worker_index=0,
                work=logical_work_identity_from_payload(tail_payload),
            ),
            payload=tail_payload,
        )
    ]
    assert transport._pending_dispatch == {}


def test_worker_pipe_batch_start_ack_clears_pending_route_batch() -> None:
    # Given: a route-batch dispatch is pending worker acknowledgment.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (42, 43)
    ]
    transport.dispatch_route_batch(
        RouteBatch("batch-start", ("topic", 1, b"key"), None, items),
        route_identity=RouteIdentity("topic", 1, b"key"),
        count_in_flight=False,
    )

    transport.handle_registry_event(
        {
            "kind": "batch_start",
            "batch_id": "batch-start",
            "worker_index": 0,
            "item_ids": [item.id for item in items],
            "item_count": len(items),
        }
    )

    # Then: batch_start, not per-item start, releases the pending route-batch slot.
    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True


def test_worker_pipe_not_started_event_clears_pending_route_batch_tail() -> None:
    # Given: route-batch dispatch inputs and fakes are prepared for worker pipe not-started event clears pending route batch tail.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    first_item = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"same-key",
        payload=b"a",
    )
    tail_item = WorkItem(
        id="work-b",
        tp=TopicPartition("topic", 1),
        offset=43,
        epoch=7,
        key=b"same-key",
        payload=b"b",
    )
    transport.dispatch_route_batch(
        RouteBatch(
            batch_id="batch-tail",
            route_identity=("topic", 1, b"same-key"),
            worker_index=None,
            items=[first_item, tail_item],
        ),
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )
    first_payload = _work_item_to_dict(first_item)
    tail_payload = _work_item_to_dict(tail_item)
    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )

    transport.handle_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": [tail_payload],
        }
    )

    # When: the relevant route-batch dispatch code path is exercised.
    # Then: the assertions confirm that worker pipe not-started event clears pending route batch tail.
    assert transport._pending_dispatch == {}


def test_process_engine_not_started_requeues_tail_without_new_in_flight_count() -> None:
    # Given: route-batch dispatch registry and worker state are prepared for process engine not-started requeues tail without new in-flight count.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    transport = _RequeueRecordingTransport()
    tail_payload = {
        "id": "work-b",
        "topic": "topic",
        "partition": 1,
        "offset": 43,
        "epoch": 7,
        "key": b"same-key",
        "payload": b"b",
        "requeue_attempts": 0,
    }
    engine_any._transport = transport
    engine_any._in_flight_registry = {}
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 2

    engine._apply_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": [tail_payload],
        }
    )

    # When: the route-batch dispatch recovery path is exercised.
    # Then: the registry, queue, or completion state confirms that process engine not-started requeues tail without new in-flight count.
    assert transport.requeued_payloads == [[tail_payload]]
    assert engine.get_in_flight_count() == 2


def test_process_engine_not_started_requeue_failure_emits_terminal_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: route-batch dispatch inputs are prepared to exercise process engine not-started requeue failure emits terminal attempt.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    tail_payload = {
        "id": "work-b",
        "topic": "topic",
        "partition": 1,
        "offset": 43,
        "epoch": 7,
        "key": b"same-key",
        "payload": b"b",
        "requeue_attempts": 0,
    }
    emitted: list[tuple[dict[str, Any], int]] = []
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._transport = None
    engine_any._logger = logging.getLogger(__name__)
    monkeypatch.setattr(
        engine,
        "_requeue_recovered_payloads",
        Mock(side_effect=RuntimeError("pipe closed")),
    )
    monkeypatch.setattr(
        engine,
        "_emit_worker_recovery_failure",
        lambda _idx, payload, *, error, attempt: emitted.append((payload, attempt)),
    )

    engine._apply_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": [tail_payload],
        }
    )

    # When: the route-batch dispatch path produces diagnostics or completion output.
    # Then: the emitted output confirms that process engine not-started requeue failure emits terminal attempt.
    assert emitted == [(tail_payload, 3)]


def test_process_engine_not_started_partial_requeue_failure_keeps_requeued_prefix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: route-batch dispatch state is prepared for the process engine not-started partial requeue failure keeps requeued prefix scenario.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    tail_payloads = [
        {
            "id": "work-b",
            "topic": "topic",
            "partition": 1,
            "offset": 43,
            "epoch": 7,
            "key": b"same-key",
            "payload": b"b",
            "requeue_attempts": 0,
        },
        {
            "id": "work-c",
            "topic": "topic",
            "partition": 1,
            "offset": 44,
            "epoch": 7,
            "key": b"same-key",
            "payload": b"c",
            "requeue_attempts": 0,
        },
        {
            "id": "work-d",
            "topic": "topic",
            "partition": 1,
            "offset": 45,
            "epoch": 7,
            "key": b"same-key",
            "payload": b"d",
            "requeue_attempts": 0,
        },
    ]
    requeued: list[str] = []
    emitted: list[tuple[str, int]] = []
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        max_retries=3,
        process_config=ProcessConfig(process_count=1),
    )
    engine_any._transport = None
    engine_any._logger = logging.getLogger(__name__)

    # When: the relevant route-batch dispatch event or operation is applied.
    # Then: the test asserts that process engine not-started partial requeue failure keeps requeued prefix.
    def requeue_one(payloads: list[dict[str, Any]]) -> None:
        assert len(payloads) == 1
        if payloads[0]["id"] != "work-b":
            raise RuntimeError("pipe closed")
        requeued.append(payloads[0]["id"])

    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeue_one)
    monkeypatch.setattr(
        engine,
        "_emit_worker_recovery_failure",
        lambda _idx, payload, *, error, attempt: emitted.append(
            (payload["id"], attempt)
        ),
    )

    engine._apply_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": tail_payloads,
        }
    )

    assert requeued == ["work-b"]
    assert emitted == [("work-c", 3), ("work-d", 3)]


def test_process_engine_not_started_requeues_tail_still_pending_in_worker_pipes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: route-batch dispatch registry and worker state are prepared for process engine not-started requeues tail still pending in worker pipes.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    first_item = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"same-key",
        payload=b"a",
    )
    tail_item = WorkItem(
        id="work-b",
        tp=TopicPartition("topic", 1),
        offset=43,
        epoch=7,
        key=b"same-key",
        payload=b"b",
    )
    first_payload = _work_item_to_dict(first_item)
    tail_payload = _work_item_to_dict(tail_item)
    transport.dispatch_route_batch(
        RouteBatch(
            batch_id="batch-tail",
            route_identity=("topic", 1, b"same-key"),
            worker_index=None,
            items=[first_item, tail_item],
        ),
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )
    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._transport = transport
    engine_any._in_flight_registry = {}
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 2
    requeued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeued.append)

    engine._apply_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": [tail_payload],
        }
    )

    # When: the route-batch dispatch recovery path is exercised.
    # Then: the registry, queue, or completion state confirms that process engine not-started requeues tail still pending in worker pipes.
    assert requeued == [[tail_payload]]
    assert transport._pending_dispatch == {}


def test_process_engine_ignores_stale_not_started_tail_after_pending_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: route-batch dispatch state is prepared for the process engine ignores stale not-started tail after pending recovery scenario.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [sender],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    first_item = WorkItem(
        id="work-a",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        key=b"same-key",
        payload=b"a",
    )
    tail_item = WorkItem(
        id="work-b",
        tp=TopicPartition("topic", 1),
        offset=43,
        epoch=7,
        key=b"same-key",
        payload=b"b",
    )
    first_payload = _work_item_to_dict(first_item)
    tail_payload = _work_item_to_dict(tail_item)
    transport.dispatch_route_batch(
        RouteBatch(
            batch_id="batch-tail",
            route_identity=("topic", 1, b"same-key"),
            worker_index=None,
            items=[first_item, tail_item],
        ),
        route_identity=RouteIdentity("topic", 1, b"same-key"),
        count_in_flight=False,
    )
    transport.handle_registry_event(
        {
            "kind": "start",
            "key": (0, "topic", 1, 42),
            "payload": first_payload,
        }
    )
    recovered = transport.recover_pending_dispatches(0)
    # When: the relevant route-batch dispatch event or operation is applied.
    # Then: the test asserts that process engine ignores stale not-started tail after pending recovery.
    assert [entry.payload for entry in recovered] == [tail_payload]
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._transport = transport
    engine_any._in_flight_registry = {}
    engine_any._in_flight_lock = threading.Lock()
    engine_any._in_flight_count = 2
    requeued: list[list[dict[str, Any]]] = []
    monkeypatch.setattr(engine, "_requeue_recovered_payloads", requeued.append)

    engine._apply_registry_event(
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-tail",
            "payloads": [tail_payload],
        }
    )

    assert requeued == []


def test_worker_pipe_start_event_releases_pending_dispatch_capacity() -> None:
    # Given: route-batch dispatch inputs and fakes are prepared for worker pipe start event releases pending dispatch capacity.
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
    # When: the relevant route-batch dispatch code path is exercised.
    # Then: the assertions confirm that worker pipe start event releases pending dispatch capacity.
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
    # Given: route-batch dispatch inputs and fakes are prepared for worker pipe slot wait blocks until start event releases capacity.
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
    # When: the relevant route-batch dispatch code path is exercised.
    # Then: the assertions confirm that worker pipe slot wait blocks until start event releases capacity.
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
    # Given: route-batch dispatch identity metadata and registry entries are prepared for worker pipe start event requires matching identity to release capacity.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that worker pipe start event requires matching identity to release capacity.
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
    # Given: route-batch dispatch identity metadata and registry entries are prepared for prefetch completion discards only matching in-flight identity.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that prefetch completion discards only matching in-flight identity.
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
    # Given: route-batch dispatch state is prepared for the prefetch completion keeps in-flight when identity differs scenario.
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

    # When: the relevant route-batch dispatch event or operation is applied.
    # Then: the test asserts that prefetch completion keeps in-flight when identity differs.
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
    # Given: route-batch dispatch state is prepared for the registry done event keeps in-flight when identity differs scenario.
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

    # When: the relevant route-batch dispatch event or operation is applied.
    # Then: the test asserts that registry done event keeps in-flight when identity differs.
    assert engine_any._in_flight_registry == {(0, "topic", 1, 42): current_payload}


def test_worker_done_registry_event_uses_identity_payload_only() -> None:
    # Given: route-batch dispatch identity metadata and registry entries are prepared for worker done registry event uses identity payload only.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that worker done registry event uses identity payload only.
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
