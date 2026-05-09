# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_core.py
# Role: Verifies process execution engine contract, construction, transport selection, submit routing, and worker diagnostics.
# Extend here for focused process execution engine regression coverage in this area.

from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    AsyncGenerator,
    BaseExecutionEngineContractTest,
    CompletionEvent,
    CompletionStatus,
    EngineWorkerDiagnostics,
    ExecutionConfig,
    ExecutionMode,
    Mock,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteBatch,
    RouteIdentity,
    TopicPartition,
    WorkerPipesProcessTransport,
    WorkItem,
    _async_worker,
    _BrokenPipeSender,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _contract_worker,
    _DeadWorker,
    _FakeProcess,
    _PipeSender,
    _serialize_batch_payload,
    _sync_worker,
    _work_item_from_dict,
    _work_item_to_dict,
    asyncio,
    cast,
    decode_worker_pipe_payload,
    logging,
    msgpack,
    pytest,
    pytest_asyncio,
    queue,
    route_batch_from_dict,
    stable_worker_index_for_route,
    worker_pipes_module,
)


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
    # Given: core process execution engine inputs and fakes are prepared for process work item serialization preserves poison key.
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

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that process work item serialization preserves poison key.
    assert decoded.poison_key == b"original-key"


def test_process_completion_codec_preserves_terminal_failure_class_and_legacy_defaults() -> (
    None
):
    # Given: a completion event carries additive terminal/failure classification fields.
    event = CompletionEvent(
        id="work-1",
        tp=TopicPartition("topic", 1),
        offset=42,
        epoch=7,
        status=CompletionStatus.FAILURE,
        error="worker failed",
        attempt=2,
        terminal=True,
        failure_class="WORKER_FAILURE",
    )

    payload = _completion_event_to_dict(event)
    decoded = _completion_event_from_dict(payload)

    # Then: process IPC preserves the additive fields.
    assert payload["terminal"] is True
    assert payload["failure_class"] == "WORKER_FAILURE"
    assert decoded.terminal is True
    assert decoded.failure_class == "WORKER_FAILURE"

    # And: legacy payloads missing those fields decode to safe defaults.
    payload.pop("terminal")
    payload.pop("failure_class")
    legacy_decoded = _completion_event_from_dict(payload)
    assert legacy_decoded.terminal is False
    assert legacy_decoded.failure_class is None


def test_ensure_workers_alive_does_not_requeue_timed_out_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine state is prepared for the ensure workers alive does not requeue timed out work scenario.
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

    # When: the relevant core process execution engine event or operation is applied.
    # Then: the test asserts that ensure workers alive does not requeue timed out work.
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
    # Given: core process execution engine inputs and fakes are prepared for process engine bounds log queue to process queue size.
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

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that process engine bounds log queue to process queue size.
    assert cast(Any, engine._log_queue)._maxsize == 7
    assert created["queue"] is engine._log_queue
    assert created["started"] is True


def test_process_execution_engine_rejects_async_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine receives an invalid async worker scenario.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(process_count=1, queue_size=7),
    )

    # When: the core process execution engine validation path is exercised.
    # Then: the test asserts that process engine rejects async worker.
    with pytest.raises(TypeError, match="synchronous picklable worker"):
        ProcessExecutionEngine(config=config, worker_fn=_async_worker)


def test_process_execution_engine_rejects_worker_pipe_batching_configs() -> None:
    # Given: core process execution engine receives an invalid worker pipe batching configs scenario.
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=7,
            batch_size=2,
        ),
    )

    # When: the core process execution engine validation path is exercised.
    # Then: the test asserts that process engine rejects worker pipe batching configs.
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
    # Given: core process execution engine receives an invalid unsupported worker pipe slice combinations scenario.
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=7,
            **process_kwargs,
        ),
    )

    # When: the core process execution engine validation path is exercised.
    # Then: the test asserts that process engine rejects unsupported worker pipe slice combinations.
    with pytest.raises(ValueError, match=match):
        ProcessExecutionEngine(config=config, worker_fn=_sync_worker)


def test_process_execution_engine_defaults_to_worker_pipes_transport_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for process engine defaults to worker pipes transport seam.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(process_count=1, queue_size=7),
        ),
        worker_fn=_sync_worker,
    )

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that process engine defaults to worker pipes transport seam.
    try:
        assert isinstance(cast(Any, engine)._transport, WorkerPipesProcessTransport)
    finally:
        asyncio.run(engine.shutdown())


def test_worker_pipe_transport_creation_does_not_reuse_task_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine state is prepared for the worker pipe transport creation does not reuse task timeout scenario.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(
                process_count=1,
                queue_size=1,
                batch_size=1,
                max_batch_wait_ms=0,
                task_timeout_ms=0,
            ),
        ),
        worker_fn=_sync_worker,
    )

    # When: the relevant core process execution engine event or operation is applied.
    # Then: the test asserts that worker pipe transport creation does not reuse task timeout.
    try:
        transport = cast(Any, engine)._transport
        assert isinstance(transport, WorkerPipesProcessTransport)
        assert not hasattr(transport, "_slot_acquire_timeout_ms")
    finally:
        asyncio.run(engine.shutdown())


def test_process_execution_engine_selects_worker_pipe_transport_seam(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for process engine selects worker pipe transport seam.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    engine = ProcessExecutionEngine(
        config=ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            process_config=ProcessConfig(
                process_count=2,
                queue_size=7,
                batch_size=1,
                max_batch_wait_ms=0,
            ),
        ),
        worker_fn=_sync_worker,
    )

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that process engine selects worker pipe transport seam.
    try:
        assert isinstance(cast(Any, engine)._transport, WorkerPipesProcessTransport)
    finally:
        asyncio.run(engine.shutdown())


def test_start_worker_keeps_single_parent_completion_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine state is prepared for the start worker keeps single parent completion queue scenario.
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
            batch_size=1,
            max_batch_wait_ms=0,
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

    # When: the relevant core process execution engine event or operation is applied.
    # Then: the test asserts that start worker keeps single parent completion queue.
    assert worker is created_processes[0]
    assert worker.started is True
    assert worker.args[1] is engine_any._completion_queue


@pytest.mark.asyncio
async def test_submit_resolves_route_identity_before_transport_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine identity metadata and registry entries are prepared for submit resolves route identity before transport dispatch.
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

    # When: the registry event handler processes the prepared event.
    # Then: the registry state confirms that submit resolves route identity before transport dispatch.
    assert captured == [(RouteIdentity("topic", 3, b"route-key"), True)]


@pytest.mark.asyncio
async def test_submit_routes_matching_identities_to_same_worker_pipe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for submit routes matching identities to same worker pipe.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=2,
            queue_size=4,
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

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that submit routes matching identities to same worker pipe.
    try:
        await engine.submit(item_a)
        await engine.submit(item_b)

        payload_counts = [len(sender.payloads) for sender in senders]
        assert payload_counts in ([2, 0], [0, 2])
        assert engine.get_in_flight_count() == 2
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_worker_pipes_submit_batch_sends_one_route_batch_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker pipes submit batch sends one route batch payload.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=2,
            queue_size=4,
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    senders = [_PipeSender(), _PipeSender()]
    engine_any = cast(Any, engine)
    engine_any._worker_pipe_senders.clear()
    engine_any._worker_pipe_senders.extend(senders)
    items = [
        WorkItem(
            id="work-a",
            tp=TopicPartition("topic", 0),
            offset=1,
            epoch=1,
            key=b"same-key",
            payload=b"a",
        ),
        WorkItem(
            id="work-b",
            tp=TopicPartition("topic", 0),
            offset=2,
            epoch=1,
            key=b"same-key",
            payload=b"b",
        ),
    ]
    route_identity = RouteIdentity("topic", 0, b"same-key")
    expected_worker_idx = stable_worker_index_for_route(route_identity, 2)

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker pipes submit batch sends one route batch payload.
    try:
        await engine.submit_batch(items)

        assert [len(sender.payloads) for sender in senders].count(1) == 1
        assert senders[expected_worker_idx].payloads
        decoded_payload = decode_worker_pipe_payload(
            senders[expected_worker_idx].payloads[0],
            max_bytes=4096,
        )
        decoded_batch = route_batch_from_dict(decoded_payload["batch"])
        assert decoded_payload["kind"] == "route_batch"
        assert decoded_batch.worker_index == expected_worker_idx
        assert decoded_batch.route_identity == ("topic", 0, b"same-key")
        assert [item.id for item in decoded_batch.items] == ["work-a", "work-b"]
        assert engine.get_in_flight_count() == 2
        runtime_metrics = engine.get_runtime_metrics()
        assert runtime_metrics is not None
        assert runtime_metrics.process is not None
        process_metrics = runtime_metrics.process.batch_metrics
        assert process_metrics.route_batch_count == 1
        assert process_metrics.route_batch_item_count == 2
        assert process_metrics.items_per_input_ipc == 2.0
        assert isinstance(runtime_metrics.workers, EngineWorkerDiagnostics)
        assert runtime_metrics.workers.total == 2
        assert runtime_metrics.workers.executing == 0
        assert runtime_metrics.workers.admitted == 1
        assert runtime_metrics.workers.top_k_loads == [2]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_worker_diagnostics_do_not_double_count_executing_route_batch_tail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker diagnostics do not double count executing route batch tail.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=4,
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    sender = _PipeSender()
    engine_any = cast(Any, engine)
    engine_any._worker_pipe_senders.clear()
    engine_any._worker_pipe_senders.append(sender)
    first = WorkItem("work-a", TopicPartition("topic", 0), 1, 1, b"same", b"a")
    second = WorkItem("work-b", TopicPartition("topic", 0), 2, 1, b"same", b"b")

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker diagnostics do not double count executing route batch tail.
    try:
        await engine.submit_batch([first, second])
        payload = _work_item_to_dict(first)
        engine_any._apply_registry_event(
            {
                "kind": "start",
                "key": (0, "topic", 0, 1),
                "payload": payload,
            }
        )

        runtime_metrics = engine.get_runtime_metrics()

        assert runtime_metrics is not None
        assert isinstance(runtime_metrics.workers, EngineWorkerDiagnostics)
        assert runtime_metrics.workers.total == 1
        assert runtime_metrics.workers.executing == 1
        assert runtime_metrics.workers.admitted == 0
        assert runtime_metrics.workers.top_k_loads == [2]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_worker_diagnostics_preserve_pending_load_indexes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker diagnostics preserve pending load indexes.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=3,
            queue_size=4,
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    engine_any = cast(Any, engine)
    engine_any._transport = type(
        "_TestTransport",
        (),
        {
            "handle_registry_event": lambda self, event: None,
            "signal_shutdown": lambda self, worker_count: None,
            "clear_pending_dispatches": lambda self: None,
            "close": lambda self: None,
            "snapshot_pending_worker_loads": lambda self: [2, "bad", 5],
        },
    )()
    engine_any._apply_registry_event(
        {
            "kind": "start",
            "key": (2, "topic", 0, 1),
            "payload": _work_item_to_dict(
                WorkItem("work-a", TopicPartition("topic", 0), 1, 1, b"k", b"a")
            ),
        }
    )

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker diagnostics preserve pending load indexes.
    try:
        runtime_metrics = engine.get_runtime_metrics()

        assert runtime_metrics is not None
        assert isinstance(runtime_metrics.workers, EngineWorkerDiagnostics)
        assert runtime_metrics.workers.total == 3
        assert runtime_metrics.workers.executing == 1
        assert runtime_metrics.workers.admitted == 1
        assert runtime_metrics.workers.top_k_loads == [6, 2]
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_worker_pipes_route_batch_metrics_calculate_size_distribution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker pipes route batch metrics calculate size distribution.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=4,
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    sender = _PipeSender()
    engine_any = cast(Any, engine)
    engine_any._worker_pipe_senders.clear()
    engine_any._worker_pipe_senders.append(sender)
    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker pipes route batch metrics calculate size distribution.
    try:
        await engine.submit_batch(
            [
                WorkItem("work-a", TopicPartition("topic", 0), 1, 1, b"same", b"a"),
                WorkItem("work-b", TopicPartition("topic", 0), 2, 1, b"same", b"b"),
            ]
        )
        await engine.submit_batch(
            [
                WorkItem("work-c", TopicPartition("topic", 0), 3, 1, b"same", b"c"),
                WorkItem("work-d", TopicPartition("topic", 0), 4, 1, b"same", b"d"),
                WorkItem("work-e", TopicPartition("topic", 0), 5, 1, b"same", b"e"),
            ]
        )

        runtime_metrics = engine.get_runtime_metrics()
        assert runtime_metrics is not None
        assert runtime_metrics.process is not None
        process_metrics = runtime_metrics.process.batch_metrics
        assert process_metrics.route_batch_count == 2
        assert process_metrics.route_batch_item_count == 5
        assert process_metrics.route_batch_size_avg == 2.5
        assert process_metrics.route_batch_size_max == 3
        assert process_metrics.items_per_input_ipc == 2.5
    finally:
        await engine.shutdown()


@pytest.mark.asyncio
async def test_worker_pipes_submit_batch_hashes_route_once_and_records_pending_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker pipes submit batch hashes route once and records pending batch.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    hash_calls: list[RouteIdentity] = []

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker pipes submit batch hashes route once and records pending batch.
    def fake_stable_worker_index(
        route_identity: RouteIdentity,
        process_count: int,
    ) -> int:
        assert process_count == 2
        hash_calls.append(route_identity)
        return 1

    monkeypatch.setattr(
        worker_pipes_module,
        "stable_worker_index_for_route",
        fake_stable_worker_index,
    )
    config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(
            process_count=2,
            queue_size=4,
            batch_size=1,
            max_batch_wait_ms=0,
        ),
    )
    engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
    senders = [_PipeSender(), _PipeSender()]
    engine_any = cast(Any, engine)
    engine_any._worker_pipe_senders.clear()
    engine_any._worker_pipe_senders.extend(senders)
    items = [
        WorkItem(
            id="work-a",
            tp=TopicPartition("topic", 3),
            offset=10,
            epoch=5,
            key=b"same-key",
            payload=b"a",
        ),
        WorkItem(
            id="work-b",
            tp=TopicPartition("topic", 3),
            offset=11,
            epoch=5,
            key=b"same-key",
            payload=b"b",
        ),
    ]

    try:
        await engine.submit_batch(items)

        assert hash_calls == [RouteIdentity("topic", 3, b"same-key")]
        transport = cast(Any, engine)._transport
        pending_batches = list(transport._pending_dispatch.values())
        assert len(pending_batches) == 1
        pending_batch = pending_batches[0]
        assert pending_batch["batch_id"]
        assert [item["id"] for item in pending_batch["items"]] == ["work-a", "work-b"]
        assert [item["offset"] for item in pending_batch["items"]] == [10, 11]
    finally:
        await engine.shutdown()


def test_worker_pipe_submit_batch_send_failure_rolls_back_pending_and_slot() -> None:
    # Given: core process execution engine inputs and fakes are prepared for worker pipe submit batch send failure rolls back pending and slot.
    transport = WorkerPipesProcessTransport(
        process_count=1,
        queue_size=1,
        max_payload_bytes=4096,
        serialize_work_item=_work_item_to_dict,
        serialize_batch_payload=_serialize_batch_payload,
        work_item_from_dict=_work_item_from_dict,
        get_worker_pipe_senders=lambda: [_BrokenPipeSender()],
        increment_in_flight=lambda: None,
        pipe_sentinel=b"sentinel",
    )
    route_batch = RouteBatch(
        batch_id="batch-failure",
        route_identity=("topic", 1, b"same-key"),
        worker_index=None,
        items=[
            WorkItem(
                id="work-a",
                tp=TopicPartition("topic", 1),
                offset=42,
                epoch=7,
                key=b"same-key",
                payload=b"a",
            ),
            WorkItem(
                id="work-b",
                tp=TopicPartition("topic", 1),
                offset=43,
                epoch=7,
                key=b"same-key",
                payload=b"b",
            ),
        ],
    )

    # When: the relevant core process execution engine code path is exercised.
    # Then: the assertions confirm that worker pipe submit batch send failure rolls back pending and slot.
    with pytest.raises(
        RuntimeError, match="Failed to dispatch worker pipe route batch"
    ):
        transport.dispatch_route_batch(
            route_batch,
            route_identity=RouteIdentity("topic", 1, b"same-key"),
            count_in_flight=True,
        )

    assert transport._pending_dispatch == {}
    assert transport._worker_pipe_queue_slots.acquire(blocking=False) is True
