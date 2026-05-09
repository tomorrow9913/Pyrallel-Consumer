# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_registry.py
# Role: Verifies worker registry identity matching, pending dispatch accounting, and backpressure slot behavior.
# Extend here for focused process execution engine regression coverage in this area.

from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    ExecutionConfig,
    ExecutionMode,
    Mock,
    PendingDispatchRecovery,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteIdentity,
    TopicPartition,
    WorkerExecutionIdentity,
    WorkerPipesProcessTransport,
    WorkItem,
    _BrokenPipeSender,
    _CountingAliveWorker,
    _PipeSender,
    _RequeueRecordingTransport,
    _work_item_from_dict,
    _work_item_to_dict,
    cast,
    deque,
    logging,
    logical_work_identity_from_payload,
    pytest,
    queue,
    threading,
    time,
)


def test_registry_start_event_ignores_older_identity_when_identity_differs() -> None:
    # Given: worker registry state is prepared for the registry start event ignores older identity when identity differs scenario.
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

    # When: the relevant worker registry event or operation is applied.
    # Then: the test asserts that registry start event ignores older identity when identity differs.
    assert engine_any._in_flight_registry == {(0, "topic", 1, 42): current_payload}


def test_registry_batch_start_seeds_in_flight_from_parent_manifest() -> None:
    # Given: the parent has recorded a route-batch manifest before worker ack.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    parent_payloads = [
        {
            "id": f"work-{offset}",
            "topic": "topic",
            "partition": 1,
            "offset": offset,
            "epoch": 7,
            "key": b"key",
            "payload": b"parent",
            "requeue_attempts": 0,
        }
        for offset in (42, 43)
    ]
    child_payloads = [dict(payload, payload=b"child") for payload in parent_payloads]
    engine_any._in_flight_registry = {}
    engine_any._process_batch_manifests = {
        "batch-parent": {
            "worker_index": 0,
            "items": parent_payloads,
        }
    }
    engine_any._transport = Mock()
    engine_any._initialize_runtime_timing_state = lambda: None  # type: ignore[method-assign]
    engine_any._record_main_to_worker_ipc = lambda *_args: None  # type: ignore[method-assign]
    engine_any._record_worker_exec = lambda *_args: None  # type: ignore[method-assign]

    engine._apply_registry_event(
        {
            "kind": "batch_start",
            "batch_id": "batch-parent",
            "worker_index": 0,
            "item_ids": [payload["id"] for payload in child_payloads],
            "item_count": len(child_payloads),
        }
    )

    # Then: recovery state is seeded from the parent manifest, not child payloads.
    assert engine_any._in_flight_registry == {
        (0, "topic", 1, 42): parent_payloads[0],
        (0, "topic", 1, 43): parent_payloads[1],
    }
    assert engine_any._process_batch_manifests == {}


def test_expired_batch_start_ack_requeues_parent_manifest() -> None:
    # Given: a parent batch manifest has not received batch_start before its deadline.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    payload = {
        "id": "work-42",
        "topic": "topic",
        "partition": 1,
        "offset": 42,
        "epoch": 7,
        "key": b"key",
        "payload": b"parent",
        "requeue_attempts": 0,
    }
    transport = _RequeueRecordingTransport()
    engine_any._transport = transport
    engine_any._process_batch_manifests = {
        "batch-timeout": {
            "worker_index": 0,
            "items": [payload],
            "start_ack_deadline_at": 10.0,
        }
    }

    recovered = engine._recover_expired_batch_start_acks(now=11.0)

    # Then: the bounded ack path recovers from the parent manifest exactly once.
    assert recovered == 1
    assert transport.requeued_payloads == [[payload]]
    assert engine_any._process_batch_manifests == {}


def test_registry_start_event_overwrites_stale_identity_when_epoch_advances() -> None:
    # Given: worker registry identity metadata and registry entries are prepared for registry start event overwrites stale identity when epoch advances.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that registry start event overwrites stale identity when epoch advances.
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


def test_registry_start_event_ignores_equal_epoch_identity_mismatch() -> None:
    # Given: worker registry state is prepared for the registry start event ignores equal epoch identity mismatch scenario.
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
                "epoch": 8,
            },
        }
    )

    # When: the relevant worker registry event or operation is applied.
    # Then: the test asserts that registry start event ignores equal epoch identity mismatch.
    assert engine_any._in_flight_registry == {(0, "topic", 1, 42): current_payload}


def test_dead_worker_recovery_uses_superseding_start_identity() -> None:
    # Given: worker registry identity metadata and registry entries are prepared for dead worker recovery uses superseding start identity.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that dead worker recovery uses superseding start identity.
    assert len(to_requeue) == 1
    assert to_requeue[0]["id"] == "work-redelivered"
    assert to_requeue[0]["epoch"] == 8
    assert to_requeue[0]["requeue_attempts"] == 1
    assert engine_any._in_flight_registry == {}


def test_registry_done_event_removes_only_matching_identity() -> None:
    # Given: worker registry identity metadata and registry entries are prepared for registry done event removes only matching identity.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that registry done event removes only matching identity.
    assert engine_any._in_flight_registry == {(1, "topic", 1, 42): redelivered_payload}


def test_registry_timeout_event_keeps_in_flight_when_identity_differs() -> None:
    # Given: worker registry state is prepared for the registry timeout event keeps in-flight when identity differs scenario.
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

    # When: the relevant worker registry event or operation is applied.
    # Then: the test asserts that registry timeout event keeps in-flight when identity differs.
    assert "timed_out" not in engine_any._in_flight_registry[(0, "topic", 1, 42)]
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["id"] == (
        "work-redelivered"
    )
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["epoch"] == 8


def test_registry_timeout_event_marks_only_matching_identity() -> None:
    # Given: worker registry identity metadata and registry entries are prepared for registry timeout event marks only matching identity.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that registry timeout event marks only matching identity.
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["timed_out"] is True
    assert engine_any._in_flight_registry[(0, "topic", 1, 42)]["attempt"] == 2
    assert "timed_out" not in engine_any._in_flight_registry[(1, "topic", 1, 42)]


def test_worker_pipe_pending_dispatch_key_preserves_redelivered_same_offset() -> None:
    # Given: worker registry inputs and fakes are prepared for worker pipe pending dispatch key preserves redelivered same offset.
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

    # When: the relevant worker registry code path is exercised.
    # Then: the assertions confirm that worker pipe pending dispatch key preserves redelivered same offset.
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
    # Given: worker registry state is prepared for the worker pipe start event keeps pending dispatch when identity differs scenario.
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
    # When: the relevant worker registry event or operation is applied.
    # Then: the test asserts that worker pipe start event keeps pending dispatch when identity differs.
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
    # Given: worker registry inputs and fakes are prepared for worker pipe transport blocks for slot without reentrant recovery.
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
    # When: the relevant worker registry code path is exercised.
    # Then: the assertions confirm that worker pipe transport blocks for slot without reentrant recovery.
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
    # Given: worker registry inputs and fakes are prepared for worker pipe backpressure waits for healthy slot without recovery.
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
    # When: the relevant worker registry code path is exercised.
    # Then: the assertions confirm that worker pipe backpressure waits for healthy slot without recovery.
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
    # Given: worker registry inputs and fakes are prepared for worker pipe transport releases pending slot when send fails.
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

    # When: the relevant worker registry code path is exercised.
    # Then: the assertions confirm that worker pipe transport releases pending slot when send fails.
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
    # Given: worker registry identity metadata and registry entries are prepared for worker pipe recover pending dispatches returns identity metadata.
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
    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that worker pipe recover pending dispatches returns identity metadata.
    assert transport.capabilities.pending_dispatch_recovery is True
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True

    recovered = transport.recover_pending_dispatches(0)

    assert recovered == [
        PendingDispatchRecovery(
            identity=WorkerExecutionIdentity(
                worker_index=0,
                work=logical_work_identity_from_payload(payload),
            ),
            payload=payload,
        )
    ]
    assert transport._pending_dispatch == {}
    assert transport.recover_pending_dispatches(0) == []
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is False
