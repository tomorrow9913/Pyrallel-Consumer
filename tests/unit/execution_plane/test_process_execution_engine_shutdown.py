# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_shutdown.py
# Role: Verifies shutdown, completion draining, late completion reconciliation, and worker-pipe cleanup behavior.
# Extend here for focused process execution engine regression coverage in this area.

from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    AsyncMock,
    CompletionEvent,
    CompletionStatus,
    Connection,
    ExecutionConfig,
    ExecutionMode,
    Mock,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteIdentity,
    TopicPartition,
    WorkerPipesProcessTransport,
    WorkItem,
    _BrokenPipeSender,
    _Closable,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _CountingAliveWorker,
    _ExplodingSerializer,
    _JoinedWorker,
    _PipeSender,
    _RequeueRecordingTransport,
    _serialize_batch_payload,
    _work_item_from_dict,
    _work_item_to_dict,
    cast,
    deque,
    logging,
    msgpack,
    process_engine_module,
    pytest,
    queue,
    threading,
    time,
)


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
    # Given: shutdown and completion drain state is prepared for shutdown residual work is diagnostic only.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown residual work is diagnostic only.
    assert "Residual in-flight registry after shutdown drain" in caplog.text
    assert "topic-1@42 id=work-42 epoch=7" in caplog.text
    assert engine_any._completion_queue.empty()
    assert list(engine_any._prefetched_completion_events) == []
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_preserves_visible_completion_drained_before_cleanup() -> None:
    # Given: shutdown and completion drain state is prepared for shutdown preserves visible completion drained before cleanup.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown preserves visible completion drained before cleanup.
    assert list(engine_any._prefetched_completion_events) == [completion]
    assert engine.get_in_flight_count() == 1
    assert await engine.wait_for_completion(timeout_seconds=0) is True
    assert await engine.poll_completed_events() == [completion]
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_poll_completed_events_ignores_queue_empty_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for the poll completed events ignores queue empty race scenario.
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

    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that poll completed events ignores queue empty race.
    assert completed == []
    assert error_messages == []


@pytest.mark.asyncio
async def test_wait_for_completion_ignores_false_empty_signal() -> None:
    # Given: shutdown and completion drain state is prepared for the wait for completion ignores false empty signal scenario.
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

    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that wait for completion ignores false empty signal.
    assert completed is False


@pytest.mark.asyncio
async def test_wait_for_completion_detects_item_even_when_empty_lies_true() -> None:
    # Given: shutdown and completion drain inputs and fakes are prepared for wait for completion detects item even when empty lies true.
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

    # When: the relevant shutdown and completion drain code path is exercised.
    # Then: the assertions confirm that wait for completion detects item even when empty lies true.
    assert completed is True
    assert len(engine_any._prefetched_completion_events) == 1
    assert engine_any._prefetched_completion_events[0].offset == 42


@pytest.mark.asyncio
async def test_wait_for_completion_ignores_duplicate_only_queue_item() -> None:
    # Given: shutdown and completion drain state is prepared for the wait for completion ignores duplicate only queue item scenario.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._prefetched_completion_events = deque()
    engine_any._drain_registry_events = Mock()
    engine_any._ensure_workers_alive = Mock()
    engine_any._completion_queue = queue.Queue()

    completion = CompletionEvent(
        id="work-42",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    packed_event = msgpack.packb(
        _completion_event_to_dict(completion),
        use_bin_type=True,
    )

    engine_any._completion_queue.put(packed_event)
    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that wait for completion ignores duplicate only queue item.
    assert await engine.wait_for_completion(timeout_seconds=0) is True
    assert list(engine_any._prefetched_completion_events) == [completion]

    engine_any._prefetched_completion_events.clear()
    engine_any._completion_queue.put(packed_event)

    assert await engine.wait_for_completion(timeout_seconds=0) is False
    assert list(engine_any._prefetched_completion_events) == []


@pytest.mark.asyncio
async def test_submit_checks_worker_liveness_before_transport_dispatch() -> None:
    # Given: shutdown and completion drain inputs and fakes are prepared for submit checks worker liveness before transport dispatch.
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
    # When: the relevant shutdown and completion drain code path is exercised.
    # Then: the assertions confirm that submit checks worker liveness before transport dispatch.
    engine_any._transport.submit_work_item.assert_awaited_once()


def test_ensure_workers_alive_force_bypasses_liveness_throttle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain inputs and fakes are prepared for ensure workers alive force bypasses liveness throttle.
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

    # When: the relevant shutdown and completion drain code path is exercised.
    # Then: the assertions confirm that ensure workers alive force bypasses liveness throttle.
    assert worker.is_alive_calls == 1
    assert engine_any._last_worker_liveness_check == 100.5


def test_ensure_workers_alive_does_not_restart_workers_after_shutdown_starts() -> None:
    # Given: shutdown and completion drain state is prepared for the ensure workers alive does not restart workers after shutdown starts scenario.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = True
    engine_any._drain_visible_worker_events = Mock()
    engine_any._should_run_worker_liveness_scan = Mock(return_value=True)
    engine_any._collect_dead_worker_recovery_candidates = Mock(return_value=[(0, 1)])
    engine_any._restart_dead_worker = Mock()

    engine._ensure_workers_alive(force=True)

    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that ensure workers alive does not restart workers after shutdown starts.
    engine_any._restart_dead_worker.assert_not_called()


@pytest.mark.asyncio
async def test_submit_rejects_work_after_shutdown_starts() -> None:
    # Given: shutdown and completion drain receives an invalid work after shutdown starts scenario.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._is_shutdown = True
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

    # When: the shutdown and completion drain validation path is exercised.
    # Then: the test asserts that submit rejects work after shutdown starts.
    with pytest.raises(RuntimeError, match="shutting down"):
        await engine.submit(item)

    engine_any._transport.submit_work_item.assert_not_awaited()


def test_worker_pipe_shutdown_ignores_broken_senders() -> None:
    # Given: shutdown and completion drain state is prepared for the worker pipe shutdown ignores broken senders scenario.
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

    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that worker pipe shutdown ignores broken senders.
    transport.signal_shutdown(1)


def test_worker_pipe_shutdown_unblocks_slot_waiter() -> None:
    # Given: shutdown and completion drain state is prepared for worker pipe shutdown unblocks slot waiter.
    sender = _PipeSender()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [cast(Connection, sender)],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=lambda: None,
        slot_wait_timeout_seconds=0.01,
    )
    first_item = WorkItem(
        id="work-1",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=3,
        key=b"key",
        payload=b"payload",
    )
    second_item = WorkItem(
        id="work-2",
        tp=TopicPartition("topic", 1),
        offset=43,
        epoch=3,
        key=b"key",
        payload=b"payload",
    )
    result_queue: queue.Queue[BaseException | None] = queue.Queue()

    transport.dispatch_payload(
        _work_item_to_dict(first_item),
        route_identity=RouteIdentity("topic", 1, b"key"),
        count_in_flight=True,
    )

    def blocked_dispatch() -> None:
        try:
            transport.dispatch_payload(
                _work_item_to_dict(second_item),
                route_identity=RouteIdentity("topic", 1, b"key"),
                count_in_flight=True,
            )
        except BaseException as exc:
            result_queue.put(exc)
        else:
            result_queue.put(None)

    waiter = threading.Thread(target=blocked_dispatch)
    waiter.start()

    transport.signal_shutdown(1)
    waiter.join(timeout=1.0)

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that worker pipe shutdown unblocks slot waiter.
    assert not waiter.is_alive()
    result = result_queue.get_nowait()
    assert isinstance(result, RuntimeError)
    assert "shutting down" in str(result)


def test_worker_pipe_slot_wait_uses_blocking_acquire_without_liveness_polling() -> None:
    # Given: shutdown and completion drain inputs and fakes are prepared for worker pipe slot wait uses blocking acquire without liveness polling.
    class _FakeSlots:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        def acquire(self, *args: object, **kwargs: object) -> bool:
            self.calls.append({"args": args, "kwargs": kwargs})
            return True

    slots = _FakeSlots()
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=1024,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
        slot_wait_liveness_check=None,
        slot_wait_timeout_seconds=0,
    )
    transport._worker_pipe_queue_slots = cast(Any, slots)

    transport._acquire_worker_pipe_queue_slot(
        worker_idx=0,
        payload=_work_item_to_dict(
            WorkItem(
                id="work-1",
                tp=TopicPartition("topic", 1),
                offset=42,
                epoch=3,
                key=b"key",
                payload=b"payload",
            )
        ),
    )

    # When: the relevant shutdown and completion drain code path is exercised.
    # Then: the assertions confirm that worker pipe slot wait uses blocking acquire without liveness polling.
    assert slots.calls == [{"args": (), "kwargs": {"blocking": True}}]


def test_worker_pipe_dispatch_rejects_oversized_payload_before_send() -> None:
    # Given: shutdown and completion drain receives an invalid oversized payload before send scenario.
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

    # When: the shutdown and completion drain validation path is exercised.
    # Then: the test asserts that worker pipe dispatch rejects oversized payload before send.
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
    # Given: shutdown and completion drain receives an invalid invalid payload before send scenario.
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

    # When: the shutdown and completion drain validation path is exercised.
    # Then: the test asserts that worker pipe dispatch rejects invalid payload before send.
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
    # Given: shutdown and completion drain state is prepared for shutdown delegates signal and close through transport seam.
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
    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown delegates signal and close through transport seam.
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
    # Given: shutdown and completion drain state is prepared for shutdown worker pipes clears pending dispatches without requeueing.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown worker pipes clears pending dispatches without requeueing.
    assert sender.payloads == [b"sentinel"]
    assert transport._pending_dispatch == {}
    engine_any._ensure_workers_alive.assert_not_called()
    engine_any._recover_pending_pipe_dispatches.assert_not_called()
    engine_any._requeue_recovered_payloads.assert_not_called()
    engine_any._emit_worker_recovery_failure.assert_not_called()
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_worker_pipes_drains_completion_before_joining_workers() -> None:
    # Given: shutdown and completion drain state is prepared for shutdown worker pipes drains completion before joining workers.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown worker pipes drains completion before joining workers.
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown runs post-join drain before local cleanup.
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
    debug_log = Mock()
    monkeypatch.setattr(process_engine_module._logger, "debug", debug_log)

    await engine.shutdown()

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown runs post-join drain before local cleanup.
    assert order == ["drain", "join", "drain", "drain", "drain", "clear", "close"]
    assert engine_any._drain_shutdown_ipc_once.call_count == 4
    post_join_log = next(
        call
        for call in debug_log.call_args_list
        if call.args
        and str(call.args[0]).startswith(
            "ProcessExecutionEngine shutdown post-join drain"
        )
    )
    assert post_join_log.args[2] == 1
    transport.signal_shutdown.assert_called_once_with(1)
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_waits_for_stable_empty_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown post-join drain waits for stable empty before cleanup.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join drain waits for stable empty before cleanup.
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
    # Given: shutdown and completion drain state is prepared for shutdown post-join drain observes stable empty after deadline event.
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

    monotonic_values = iter([0.0, 0.06, 0.07])
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join drain observes stable empty after deadline event.
    assert result == (0, 1, 3)
    assert drain_calls == [(0, 1), (0, 0), (0, 0)]
    assert sleep_calls == [0.01, 0.01]


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_waits_for_first_late_event_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown post-join drain waits for first late event after deadline.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    drain_results = [
        (0, 0),
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

    monotonic_values = iter([0.0, 0.055, 0.065, 0.075])
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join drain waits for first late event after deadline.
    assert result == (0, 1, 4)
    assert drain_calls == [(0, 0), (0, 1), (0, 0), (0, 0)]
    assert sleep_calls == [0.01, 0.01, 0.01]


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_has_bounded_post_deadline_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown post-join drain has bounded post deadline grace.
    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    drain_calls: list[tuple[int, int]] = []
    sleep_calls: list[float] = []

    def drain_once() -> tuple[int, int]:
        result = (0, 1)
        drain_calls.append(result)
        return result

    async def record_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monotonic_values = iter([0.0, 0.055, 0.065, 0.075, 0.095])
    monkeypatch.setattr(time, "monotonic", lambda: next(monotonic_values, 0.095))
    monkeypatch.setattr(
        "pyrallel_consumer.execution_plane.process_engine.asyncio.sleep",
        record_sleep,
    )
    engine_any._drain_shutdown_ipc_once = Mock(side_effect=drain_once)

    result = await engine._drain_shutdown_ipc_until_stable_empty(
        max_seconds=0.05,
        stable_empty_passes=2,
    )

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join drain has bounded post deadline grace.
    assert result == (0, 4, 4)
    assert drain_calls == [(0, 1), (0, 1), (0, 1), (0, 1)]
    assert sleep_calls == [0.01, 0.01, 0.01]


@pytest.mark.asyncio
async def test_shutdown_post_join_stable_empty_prefetches_real_late_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown post-join stable empty prefetches real late completion.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join stable empty prefetches real late completion.
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
    # Given: shutdown and completion drain state is prepared for the shutdown post-join late completion keeps different identity registry scenario.
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

    # When: the relevant shutdown and completion drain event or operation is applied.
    # Then: the test asserts that shutdown post-join late completion keeps different identity registry.
    assert list(engine_any._prefetched_completion_events) == [stale_completion]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1
    assert "topic-1@42 id=work-redelivered epoch=8" in caplog.text
    clear_pending_dispatches.assert_called_once_with()


@pytest.mark.asyncio
async def test_shutdown_post_join_drain_reconciles_late_completion_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: shutdown and completion drain state is prepared for shutdown post-join drain reconciles late completion before cleanup.
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

    # When: the shutdown or drain path is exercised.
    # Then: the final cleanup and completion state confirms that shutdown post-join drain reconciles late completion before cleanup.
    def assert_reconciled_before_local_cleanup() -> None:
        assert engine_any._in_flight_registry == {}
        assert engine_any._prefetched_completion_events == [completion]

    transport = Mock()
    transport.clear_pending_dispatches.side_effect = (
        assert_reconciled_before_local_cleanup
    )
    engine_any._transport = transport
    engine_any._join_worker_with_escalation = Mock(side_effect=join_worker)
    debug_log = Mock()
    monkeypatch.setattr(process_engine_module._logger, "debug", debug_log)

    await engine.shutdown()

    engine_any._join_worker_with_escalation.assert_called_once()
    transport.clear_pending_dispatches.assert_called_once_with()
    transport.close.assert_called_once_with()
    assert engine_any._prefetched_completion_events == [completion]
    assert engine_any._in_flight_registry == {}
    assert engine.get_in_flight_count() == 1
    post_join_log = next(
        call
        for call in debug_log.call_args_list
        if call.args
        and str(call.args[0]).startswith(
            "ProcessExecutionEngine shutdown post-join drain"
        )
    )
    assert post_join_log.args[2] == 1
    assert post_join_log.args[4] == 0
