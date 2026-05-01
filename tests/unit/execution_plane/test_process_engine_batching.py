"""Tests for ProcessExecutionEngine micro-batching."""

import asyncio
import queue
import threading
import time
from collections import deque
from typing import Any, Dict, cast

import msgpack
import pytest

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import (
    BatchCompletion,
    CompletionEvent,
    CompletionStatus,
    EngineRuntimeDiagnostics,
    RouteBatch,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane import process_engine
from pyrallel_consumer.execution_plane.process_codec import (
    batch_completion_from_dict,
    batch_completion_to_dict,
    decode_batch_completion_payload,
    decode_worker_pipe_payload,
    route_batch_from_dict,
    route_batch_to_dict,
    serialize_batch_completion_payload,
    serialize_worker_pipe_payload,
)
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _BatchAccumulator,
    _completion_event_to_dict,
    _decode_incoming_payloads,
)
from pyrallel_consumer.execution_plane.process_transport_shared_queue import (
    SharedQueueProcessTransport,
)


class _RetryCounter:
    def __init__(self):
        self.attempts: Dict[int, int] = {}

    def record_attempt(self, offset: int) -> None:
        self.attempts[offset] = self.attempts.get(offset, 0) + 1

    def get_attempts(self, offset: int) -> int:
        return self.attempts.get(offset, 0)


def _make_work_item(offset: int, partition: int = 0, topic: str = "test") -> WorkItem:
    return WorkItem(
        id=f"wi-{offset}",
        tp=TopicPartition(topic=topic, partition=partition),
        offset=offset,
        epoch=1,
        key=f"key-{offset}".encode(),
        payload=f"payload-{offset}".encode(),
    )


def _make_completion_event(
    offset: int,
    *,
    status: CompletionStatus = CompletionStatus.SUCCESS,
    error: str | None = None,
    attempt: int = 1,
    partition: int = 0,
    topic: str = "test",
) -> CompletionEvent:
    return CompletionEvent(
        id=f"wi-{offset}",
        tp=TopicPartition(topic, partition),
        offset=offset,
        epoch=1,
        status=status,
        error=error,
        attempt=attempt,
    )


def _make_unstarted_process_engine(raw_events: list[bytes]) -> ProcessExecutionEngine:
    engine = cast(
        ProcessExecutionEngine,
        ProcessExecutionEngine.__new__(ProcessExecutionEngine),
    )
    completion_queue: queue.Queue[bytes] = queue.Queue()
    for raw_event in raw_events:
        completion_queue.put_nowait(raw_event)
    engine._workers = []
    engine._prefetched_completion_events = deque()
    engine._completion_queue = cast(Any, completion_queue)
    engine._registry_event_queue = cast(Any, None)
    engine._in_flight_lock = threading.Lock()
    engine._in_flight_count = 0
    engine._in_flight_registry = {}
    engine._config = ExecutionConfig(
        mode="process",
        process_config=ProcessConfig(process_count=1, queue_size=16),
    )
    engine._is_shutdown = False

    def _noop_ensure_workers_alive(force: bool = False) -> None:
        del force

    def _noop_drain_registry_events() -> None:
        return None

    engine._ensure_workers_alive = _noop_ensure_workers_alive  # type: ignore[method-assign]
    engine._drain_registry_events = _noop_drain_registry_events  # type: ignore[method-assign]
    return engine


def _make_decode_only_process_engine(max_bytes: int) -> ProcessExecutionEngine:
    engine = cast(
        ProcessExecutionEngine,
        ProcessExecutionEngine.__new__(ProcessExecutionEngine),
    )
    engine._config = ExecutionConfig(
        mode="process",
        process_config=ProcessConfig(
            process_count=1,
            queue_size=16,
            msgpack_max_bytes=max_bytes,
        ),
    )
    return engine


def test_route_batch_wire_contract_preserves_identity_and_item_order() -> None:
    route_batch = RouteBatch(
        batch_id="batch-1",
        route_identity=("topic", 0, b"key-a"),
        worker_index=2,
        items=[_make_work_item(0), _make_work_item(1)],
    )

    decoded = route_batch_from_dict(route_batch_to_dict(route_batch))

    assert decoded.batch_id == "batch-1"
    assert decoded.route_identity == ("topic", 0, b"key-a")
    assert decoded.worker_index == 2
    assert [item.id for item in decoded.items] == ["wi-0", "wi-1"]
    assert [
        (item.tp.topic, item.tp.partition, item.offset) for item in decoded.items
    ] == [
        ("test", 0, 0),
        ("test", 0, 1),
    ]


@pytest.mark.parametrize("missing_field", ["batch_id", "route_identity", "items"])
def test_route_batch_from_dict_rejects_missing_required_fields(
    missing_field: str,
) -> None:
    payload = route_batch_to_dict(
        RouteBatch(
            batch_id="batch-required",
            route_identity=("test", 0, b"key-a"),
            worker_index=1,
            items=[_make_work_item(0)],
        )
    )
    payload.pop(missing_field)

    with pytest.raises(ValueError, match="invalid_route_batch"):
        route_batch_from_dict(payload)


def test_route_batch_from_dict_rejects_non_list_items() -> None:
    payload = route_batch_to_dict(
        RouteBatch(
            batch_id="batch-items",
            route_identity=("test", 0, b"key-a"),
            worker_index=1,
            items=[_make_work_item(0)],
        )
    )
    payload["items"] = {"offset": 0}

    with pytest.raises(ValueError, match="invalid_route_batch"):
        route_batch_from_dict(payload)


def test_route_batch_from_dict_rejects_empty_items() -> None:
    payload = route_batch_to_dict(
        RouteBatch(
            batch_id="batch-empty",
            route_identity=("test", 0, b"key-a"),
            worker_index=1,
            items=[_make_work_item(0)],
        )
    )
    payload["items"] = []

    with pytest.raises(ValueError, match="invalid_route_batch"):
        route_batch_from_dict(payload)


def test_batch_completion_wire_contract_preserves_item_results() -> None:
    completion = BatchCompletion(
        batch_id="batch-1",
        route_identity=("topic", 0, b"key-a"),
        results=[
            CompletionEvent(
                id="wi-0",
                tp=TopicPartition("test", 0),
                offset=0,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
            CompletionEvent(
                id="wi-1",
                tp=TopicPartition("test", 0),
                offset=1,
                epoch=1,
                status=CompletionStatus.FAILURE,
                error="boom",
                attempt=3,
            ),
        ],
    )

    decoded = batch_completion_from_dict(batch_completion_to_dict(completion))

    assert decoded.batch_id == "batch-1"
    assert decoded.route_identity == ("topic", 0, b"key-a")
    assert [event.id for event in decoded.results] == ["wi-0", "wi-1"]
    assert [event.status for event in decoded.results] == [
        CompletionStatus.SUCCESS,
        CompletionStatus.FAILURE,
    ]
    assert decoded.results[1].error == "boom"
    assert decoded.results[1].attempt == 3


@pytest.mark.parametrize("missing_field", ["batch_id", "route_identity", "results"])
def test_batch_completion_from_dict_rejects_missing_required_fields(
    missing_field: str,
) -> None:
    payload = batch_completion_to_dict(
        BatchCompletion(
            batch_id="batch-required",
            route_identity=("test", 0, b"key-a"),
            results=[_make_completion_event(0)],
        )
    )
    payload.pop(missing_field)

    with pytest.raises(ValueError, match="invalid_batch_completion"):
        batch_completion_from_dict(payload)


def test_batch_completion_from_dict_rejects_non_list_results() -> None:
    payload = batch_completion_to_dict(
        BatchCompletion(
            batch_id="batch-results",
            route_identity=("test", 0, b"key-a"),
            results=[_make_completion_event(0)],
        )
    )
    payload["results"] = {"offset": 0}

    with pytest.raises(ValueError, match="invalid_batch_completion"):
        batch_completion_from_dict(payload)


def test_batch_completion_from_dict_rejects_empty_results() -> None:
    payload = batch_completion_to_dict(
        BatchCompletion(
            batch_id="batch-empty",
            route_identity=("test", 0, b"key-a"),
            results=[_make_completion_event(0)],
        )
    )
    payload["results"] = []

    with pytest.raises(ValueError, match="invalid_batch_completion"):
        batch_completion_from_dict(payload)


def test_batch_completion_envelope_roundtrip_preserves_prefix_result_order() -> None:
    completion = BatchCompletion(
        batch_id="batch-envelope",
        route_identity=("topic", 0, b"key-a"),
        results=[
            CompletionEvent(
                id="wi-0",
                tp=TopicPartition("test", 0),
                offset=0,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
            CompletionEvent(
                id="wi-1",
                tp=TopicPartition("test", 0),
                offset=1,
                epoch=1,
                status=CompletionStatus.FAILURE,
                error="boom",
                attempt=2,
            ),
        ],
    )

    decoded_payload = decode_batch_completion_payload(
        serialize_batch_completion_payload(completion, completion_enqueued_at=4.5),
        max_bytes=4096,
    )
    decoded = batch_completion_from_dict(decoded_payload["completion"])

    assert decoded_payload["kind"] == "batch_completion"
    assert decoded_payload["timing"] == {"completion_enqueued_at": 4.5}
    assert decoded.batch_id == "batch-envelope"
    assert decoded.route_identity == ("topic", 0, b"key-a")
    assert [
        (event.id, event.offset, event.status, event.error, event.attempt)
        for event in decoded.results
    ] == [
        ("wi-0", 0, CompletionStatus.SUCCESS, None, 1),
        ("wi-1", 1, CompletionStatus.FAILURE, "boom", 2),
    ]


def test_decode_completion_queue_item_events_uses_existing_config_without_instantiating_execution_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _make_completion_event(7)
    raw_event = msgpack.packb(_completion_event_to_dict(event), use_bin_type=True)
    engine = _make_decode_only_process_engine(max_bytes=len(raw_event) + 1)

    def fail_execution_config(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ExecutionConfig must not be constructed on decode path")

    monkeypatch.setattr(process_engine, "ExecutionConfig", fail_execution_config)

    decoded = engine._decode_completion_queue_item_events(raw_event)

    assert [item.id for item in decoded] == ["wi-7"]


def test_decode_completion_queue_item_events_uses_constant_fallback_without_instantiating_execution_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    event = _make_completion_event(8)
    raw_event = msgpack.packb(_completion_event_to_dict(event), use_bin_type=True)
    engine = cast(
        ProcessExecutionEngine,
        ProcessExecutionEngine.__new__(ProcessExecutionEngine),
    )
    cast(Any, engine)._config = object()

    def fail_execution_config(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("ExecutionConfig must not be constructed on decode path")

    monkeypatch.setattr(process_engine, "ExecutionConfig", fail_execution_config)

    decoded = engine._decode_completion_queue_item_events(raw_event)

    assert [item.id for item in decoded] == ["wi-8"]


def test_decode_completion_queue_item_events_rejects_oversized_raw_bytes_before_unpack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_decode_only_process_engine(max_bytes=2)

    def fail_unpack(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("msgpack.unpackb should not run for oversized bytes")

    monkeypatch.setattr(process_engine.msgpack, "unpackb", fail_unpack)

    with pytest.raises(ValueError, match="payload_too_large"):
        engine._decode_completion_queue_item_events(b"012345")


def test_decode_completion_queue_item_events_rejects_oversized_item_completion_bytes() -> (
    None
):
    event = _make_completion_event(7)
    raw_event = msgpack.packb(_completion_event_to_dict(event), use_bin_type=True)
    engine = _make_decode_only_process_engine(max_bytes=len(raw_event) - 1)

    with pytest.raises(ValueError, match="payload_too_large"):
        engine._decode_completion_queue_item_events(raw_event)


def test_decode_completion_queue_item_events_rejects_non_dict_payload() -> None:
    raw_event = msgpack.packb(["not", "a", "dict"], use_bin_type=True)
    engine = _make_decode_only_process_engine(max_bytes=len(raw_event) + 1)

    with pytest.raises(ValueError, match="invalid_completion_payload_type"):
        engine._decode_completion_queue_item_events(raw_event)


def test_decode_completion_queue_item_events_rejects_oversized_batch_completion_bytes() -> (
    None
):
    completion = BatchCompletion(
        batch_id="batch-too-large",
        route_identity=("test", 0, b"key-a"),
        results=[_make_completion_event(0)],
    )
    raw_event = serialize_batch_completion_payload(
        completion,
        completion_enqueued_at=1.0,
    )
    engine = _make_decode_only_process_engine(max_bytes=len(raw_event) - 1)

    with pytest.raises(ValueError, match="payload_too_large"):
        engine._decode_completion_queue_item_events(raw_event)


def test_completion_identity_cache_evicts_oldest_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _make_decode_only_process_engine(max_bytes=1024)
    monkeypatch.setattr(process_engine, "_MAX_SEEN_COMPLETION_IDENTITIES", 2)

    assert engine._is_duplicate_completion_event(_make_completion_event(0)) is False
    assert engine._is_duplicate_completion_event(_make_completion_event(1)) is False
    assert engine._is_duplicate_completion_event(_make_completion_event(2)) is False

    assert len(engine._seen_completion_identities) == 2
    assert engine._is_duplicate_completion_event(_make_completion_event(1)) is True
    assert engine._is_duplicate_completion_event(_make_completion_event(0)) is False


@pytest.mark.asyncio
async def test_parent_poll_expands_batch_completion_to_item_events() -> None:
    batch_completion = BatchCompletion(
        batch_id="batch-parent",
        route_identity=("test", 0, b"key-a"),
        results=[_make_completion_event(0), _make_completion_event(1)],
    )
    engine = _make_unstarted_process_engine(
        [
            serialize_batch_completion_payload(
                batch_completion, completion_enqueued_at=1.0
            )
        ]
    )
    engine._in_flight_count = 2

    events = await engine.poll_completed_events()

    assert [(event.id, event.offset, event.status) for event in events] == [
        ("wi-0", 0, CompletionStatus.SUCCESS),
        ("wi-1", 1, CompletionStatus.SUCCESS),
    ]
    assert engine.get_in_flight_count() == 0
    runtime_metrics = engine.get_runtime_metrics()
    assert runtime_metrics is not None
    assert runtime_metrics.process is not None
    process_metrics = runtime_metrics.process.batch_metrics
    assert process_metrics.completion_batch_payload_count == 1
    assert process_metrics.completion_item_payload_count == 0
    assert process_metrics.items_per_completion_ipc == 2.0


@pytest.mark.asyncio
async def test_parent_poll_preserves_batch_completion_failure_result_fields() -> None:
    batch_completion = BatchCompletion(
        batch_id="batch-parent-failure",
        route_identity=("test", 0, b"key-a"),
        results=[
            _make_completion_event(0),
            _make_completion_event(
                1,
                status=CompletionStatus.FAILURE,
                error="qa-fail",
                attempt=3,
            ),
        ],
    )
    engine = _make_unstarted_process_engine(
        [
            serialize_batch_completion_payload(
                batch_completion, completion_enqueued_at=1.0
            )
        ]
    )
    engine._in_flight_count = 2

    events = await engine.poll_completed_events()

    assert [
        (event.offset, event.status, event.error, event.attempt) for event in events
    ] == [
        (0, CompletionStatus.SUCCESS, None, 1),
        (1, CompletionStatus.FAILURE, "qa-fail", 3),
    ]


@pytest.mark.asyncio
async def test_parent_poll_batch_limit_counts_expanded_item_events() -> None:
    batch_completion = BatchCompletion(
        batch_id="batch-parent-limit",
        route_identity=("test", 0, b"key-a"),
        results=[_make_completion_event(0), _make_completion_event(1)],
    )
    engine = _make_unstarted_process_engine(
        [
            serialize_batch_completion_payload(
                batch_completion, completion_enqueued_at=1.0
            )
        ]
    )
    engine._in_flight_count = 2

    first_poll = await engine.poll_completed_events(batch_limit=1)
    second_poll = await engine.poll_completed_events(batch_limit=1)

    assert [event.offset for event in first_poll] == [0]
    assert [event.offset for event in second_poll] == [1]
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_parent_poll_suppresses_duplicate_item_completion_after_batch_envelope() -> (
    None
):
    duplicate_event = _make_completion_event(0)
    batch_completion = BatchCompletion(
        batch_id="batch-parent-dedupe",
        route_identity=("test", 0, b"key-a"),
        results=[duplicate_event],
    )
    engine = _make_unstarted_process_engine(
        [
            serialize_batch_completion_payload(
                batch_completion, completion_enqueued_at=1.0
            ),
            msgpack.packb(
                _completion_event_to_dict(duplicate_event), use_bin_type=True
            ),
        ]
    )
    engine._in_flight_count = 1

    events = await engine.poll_completed_events()

    assert [(event.id, event.offset) for event in events] == [("wi-0", 0)]
    assert engine.get_in_flight_count() == 0


@pytest.mark.asyncio
async def test_parent_poll_does_not_invent_completion_for_skipped_batch_tail() -> None:
    batch_completion = BatchCompletion(
        batch_id="batch-parent-prefix",
        route_identity=("test", 0, b"key-a"),
        results=[_make_completion_event(0), _make_completion_event(1)],
    )
    engine = _make_unstarted_process_engine(
        [
            serialize_batch_completion_payload(
                batch_completion, completion_enqueued_at=1.0
            )
        ]
    )
    engine._in_flight_count = 2

    events = await engine.poll_completed_events()

    assert [event.offset for event in events] == [0, 1]
    assert 2 not in [event.offset for event in events]


@pytest.mark.asyncio
async def test_parent_poll_keeps_existing_item_level_completion_path() -> None:
    event = _make_completion_event(7)
    engine = _make_unstarted_process_engine(
        [msgpack.packb(_completion_event_to_dict(event), use_bin_type=True)]
    )
    engine._in_flight_count = 1

    events = await engine.poll_completed_events()

    assert events == [event]
    assert engine.get_in_flight_count() == 0
    runtime_metrics = engine.get_runtime_metrics()
    assert runtime_metrics is not None
    assert runtime_metrics.process is not None
    process_metrics = runtime_metrics.process.batch_metrics
    assert process_metrics.completion_item_payload_count == 1
    assert process_metrics.completion_batch_payload_count == 0
    assert process_metrics.items_per_completion_ipc == 1.0


def test_worker_pipe_payload_codec_distinguishes_single_item_and_route_batch() -> None:
    single_payload = serialize_worker_pipe_payload([_make_work_item(0)], 1.5)
    batch_payload = serialize_worker_pipe_payload(
        RouteBatch(
            batch_id="batch-1",
            route_identity=("test", 0, b"key-a"),
            worker_index=1,
            items=[_make_work_item(0), _make_work_item(1)],
        ),
        2.5,
    )

    decoded_single = decode_worker_pipe_payload(single_payload, max_bytes=4096)
    decoded_batch = decode_worker_pipe_payload(batch_payload, max_bytes=4096)

    assert decoded_single["kind"] == "work_items"
    assert decoded_batch["kind"] == "route_batch"
    assert [item["offset"] for item in decoded_single["items"]] == [0]
    assert [item["offset"] for item in decoded_batch["batch"]["items"]] == [0, 1]


def test_worker_pipe_route_batch_payload_roundtrip_preserves_identity_and_order() -> (
    None
):
    route_batch = RouteBatch(
        batch_id="batch-ordered",
        route_identity=("test", 0, b"key-a"),
        worker_index=3,
        items=[_make_work_item(2), _make_work_item(3)],
    )

    packed = serialize_worker_pipe_payload(route_batch, 3.5)
    decoded_payload = decode_worker_pipe_payload(packed, max_bytes=4096)
    decoded_batch = route_batch_from_dict(decoded_payload["batch"])

    assert decoded_payload["kind"] == "route_batch"
    assert decoded_batch.batch_id == "batch-ordered"
    assert decoded_batch.route_identity == ("test", 0, b"key-a")
    assert decoded_batch.worker_index == 3
    assert [item.offset for item in decoded_batch.items] == [2, 3]


def test_worker_pipe_payload_codec_rejects_unknown_payload_kind() -> None:
    packed = msgpack.packb({"kind": "mystery", "items": []}, use_bin_type=True)

    with pytest.raises(ValueError, match="unknown_worker_pipe_payload_kind:mystery"):
        decode_worker_pipe_payload(packed, max_bytes=4096)


def _sync_worker(item: WorkItem) -> None:
    pass


def _sleepy_worker(item: WorkItem) -> None:
    time.sleep(0.01)


def _failing_worker(item: WorkItem) -> None:
    if item.offset == 2:
        raise ValueError("Intentional failure")


_retry_counter = _RetryCounter()


def _worker_succeeds_on_second_attempt(item: WorkItem) -> None:
    _retry_counter.record_attempt(item.offset)
    if _retry_counter.get_attempts(item.offset) < 2:
        raise RuntimeError("Simulated transient failure")


def _worker_always_fails(item: WorkItem) -> None:
    _retry_counter.record_attempt(item.offset)
    raise RuntimeError("Permanent failure")


@pytest.fixture
def small_batch_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="process",
        max_in_flight=100,
        max_retries=3,
        retry_backoff_ms=100,
        exponential_backoff=True,
        max_retry_backoff_ms=1000,
        retry_jitter_ms=0,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=256,
            batch_size=4,
            max_batch_wait_ms=50,
            worker_join_timeout_ms=5000,
        ),
    )


@pytest.fixture
def retry_config() -> ExecutionConfig:
    return ExecutionConfig(
        mode="process",
        max_in_flight=100,
        max_retries=3,
        retry_backoff_ms=50,
        exponential_backoff=True,
        max_retry_backoff_ms=500,
        retry_jitter_ms=10,
        process_config=ProcessConfig(
            process_count=1,
            queue_size=256,
            batch_size=2,
            max_batch_wait_ms=10,
            worker_join_timeout_ms=5000,
        ),
    )


class TestMicroBatching:
    @pytest.mark.asyncio
    async def test_submit_uses_inline_fast_path_for_single_item_batches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)

        async def fail_to_thread(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("single-item process submit should avoid to_thread")

        monkeypatch.setattr(process_engine.asyncio, "to_thread", fail_to_thread)
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                batch_size=1,
                max_batch_wait_ms=0,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
        try:
            await engine.submit(_make_work_item(0))

            diagnostics = engine.get_runtime_metrics()
            assert isinstance(diagnostics, EngineRuntimeDiagnostics)
            assert diagnostics.process is not None
            metrics = diagnostics.process.batch_metrics
            assert metrics.size_flush_count == 1
            assert metrics.total_flushed_items == 1
            assert metrics.last_flush_size == 1
        finally:
            await engine.shutdown()

    def test_single_item_fast_path_reports_full_queue_without_buffering(self) -> None:
        task_queue: queue.Queue[bytes] = queue.Queue(maxsize=1)
        task_queue.put_nowait(b"busy")
        accumulator = _BatchAccumulator(
            task_queue=task_queue,
            batch_size=1,
            max_batch_wait_ms=0,
        )

        accepted = accumulator.add_nowait_fast_path(_make_work_item(0))

        assert accepted is False
        metrics = accumulator.snapshot()
        assert metrics.size_flush_count == 0
        assert metrics.total_flushed_items == 0
        assert metrics.buffered_items == 0

    @pytest.mark.asyncio
    async def test_submit_falls_back_to_threaded_add_when_fast_path_cannot_enqueue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                batch_size=1,
                max_batch_wait_ms=0,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)

        class FallbackAccumulator:
            def __init__(self) -> None:
                self.fast_path_items: list[WorkItem] = []
                self.threaded_add_items: list[WorkItem] = []

            def add_nowait_fast_path(self, work_item: WorkItem) -> bool:
                self.fast_path_items.append(work_item)
                return False

            def add(self, work_item: WorkItem) -> None:
                self.threaded_add_items.append(work_item)

            def close(self) -> None:
                return None

        accumulator = FallbackAccumulator()
        threaded_calls = []

        async def immediate_to_thread(func: Any, *args: Any, **kwargs: Any) -> Any:
            threaded_calls.append(func)
            return func(*args, **kwargs)

        monkeypatch.setattr(process_engine.asyncio, "to_thread", immediate_to_thread)
        engine._batch_accumulator = accumulator  # type: ignore[assignment]
        item = _make_work_item(0)
        try:
            await engine.submit(item)

            assert accumulator.fast_path_items == [item]
            assert accumulator.threaded_add_items == [item]
            assert threaded_calls == [accumulator.add]
            assert engine.get_in_flight_count() == 1
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_poll_completed_events_does_not_depend_on_queue_empty(self) -> None:
        event = CompletionEvent(
            id="wi-0",
            tp=TopicPartition("test", 0),
            offset=0,
            epoch=1,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )

        class LyingCompletionQueue:
            def __init__(self) -> None:
                self._items = [
                    msgpack.packb(_completion_event_to_dict(event), use_bin_type=True)
                ]

            def empty(self) -> bool:
                return True

            def get_nowait(self) -> bytes:
                if not self._items:
                    raise queue.Empty()
                return self._items.pop(0)

        engine = cast(
            ProcessExecutionEngine,
            ProcessExecutionEngine.__new__(ProcessExecutionEngine),
        )
        engine._workers = []
        engine._prefetched_completion_events = deque()
        engine._completion_queue = cast(Any, LyingCompletionQueue())
        engine._registry_event_queue = cast(Any, None)
        engine._in_flight_lock = threading.Lock()
        engine._in_flight_count = 1

        def _noop_ensure_workers_alive(*, force: bool = False) -> None:
            del force

        engine._ensure_workers_alive = _noop_ensure_workers_alive  # type: ignore[method-assign]
        engine._drain_registry_events = lambda: None  # type: ignore[method-assign]

        events = await engine.poll_completed_events()

        assert events == [event]
        assert engine.get_in_flight_count() == 0

    def test_demand_flush_emits_existing_buffer_before_appending_new_item(self):
        task_queue: queue.Queue[bytes] = queue.Queue()
        accumulator = _BatchAccumulator(
            task_queue=task_queue,
            batch_size=64,
            max_batch_wait_ms=1000,
            flush_policy="demand",
            demand_flush_min_residence_ms=0,
        )

        accumulator.add(_make_work_item(0))
        assert task_queue.empty()

        accumulator.add(_make_work_item(1))

        flushed_payload, _ = _decode_incoming_payloads(
            task_queue.get_nowait(), 1_000_000
        )
        assert [payload["offset"] for payload in flushed_payload] == [0]

        metrics = accumulator.snapshot()
        assert metrics.demand_flush_count == 1
        assert metrics.buffered_items == 1

    def test_demand_min_residence_waits_until_threshold_before_flushing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clock = {"now": 100.0}
        monkeypatch.setattr(
            "pyrallel_consumer.execution_plane.process_engine.time.monotonic",
            lambda: clock["now"],
        )
        monkeypatch.setattr(_BatchAccumulator, "_start_flush_timer", lambda self: None)

        task_queue: queue.Queue[bytes] = queue.Queue()
        accumulator = _BatchAccumulator(
            task_queue=task_queue,
            batch_size=64,
            max_batch_wait_ms=1000,
            flush_policy="demand_min_residence",
            demand_flush_min_residence_ms=2,
        )

        accumulator.add(_make_work_item(0))
        clock["now"] += 0.001
        accumulator.add(_make_work_item(1))
        assert task_queue.empty()

        clock["now"] += 0.002
        accumulator.add(_make_work_item(2))

        flushed_payload, _ = _decode_incoming_payloads(
            task_queue.get_nowait(), 1_000_000
        )
        assert [payload["offset"] for payload in flushed_payload] == [0, 1]

        metrics = accumulator.snapshot()
        assert metrics.demand_flush_count == 1
        assert metrics.buffered_items == 1

    @pytest.mark.asyncio
    async def test_get_runtime_metrics_reports_buffered_items_before_flush(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                batch_size=4,
                max_batch_wait_ms=1000,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
        try:
            await engine.submit(_make_work_item(0))
            diagnostics = engine.get_runtime_metrics()

            assert isinstance(diagnostics, EngineRuntimeDiagnostics)
            assert diagnostics.process is not None
            metrics = diagnostics.process.batch_metrics
            assert metrics.buffered_items == 1
            assert metrics.size_flush_count == 0
            assert metrics.timer_flush_count == 0
            assert metrics.buffered_age_seconds >= 0.0
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_runtime_metrics_reports_size_flush_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                batch_size=2,
                max_batch_wait_ms=1000,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
        try:
            await engine.submit(_make_work_item(0))
            await engine.submit(_make_work_item(1))

            diagnostics = engine.get_runtime_metrics()

            assert isinstance(diagnostics, EngineRuntimeDiagnostics)
            assert diagnostics.process is not None
            metrics = diagnostics.process.batch_metrics
            assert metrics.size_flush_count == 1
            assert metrics.timer_flush_count == 0
            assert metrics.close_flush_count == 0
            assert metrics.total_flushed_items == 2
            assert metrics.last_flush_size == 2
            assert metrics.buffered_items == 0
            assert metrics.last_flush_wait_seconds >= 0.0
            assert metrics.transport_mode == "shared_queue"
            assert metrics.support_state == "full"
            assert metrics.timer_flush_supported is True
            assert metrics.demand_flush_supported is True
            assert metrics.recycle_supported is True
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_runtime_metrics_reports_process_timing_after_completion(
        self,
    ) -> None:
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                batch_size=1,
                max_batch_wait_ms=0,
                worker_join_timeout_ms=5000,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sleepy_worker)
        try:
            await engine.submit(_make_work_item(0))

            assert await engine.wait_for_completion(timeout_seconds=2.0) is True

            events = await engine.poll_completed_events()
            diagnostics = engine.get_runtime_metrics()

            assert len(events) == 1
            assert isinstance(diagnostics, EngineRuntimeDiagnostics)
            assert diagnostics.process is not None
            metrics = diagnostics.process.batch_metrics
            assert metrics.last_main_to_worker_ipc_seconds >= 0.0
            assert metrics.avg_main_to_worker_ipc_seconds >= 0.0
            assert metrics.last_worker_exec_seconds > 0.0
            assert metrics.avg_worker_exec_seconds > 0.0
            assert metrics.last_worker_to_main_ipc_seconds >= 0.0
            assert metrics.avg_worker_to_main_ipc_seconds >= 0.0
            assert metrics.transport_mode == "shared_queue"
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_get_runtime_metrics_marks_worker_pipes_as_bounded_support(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
        config = ExecutionConfig(
            mode="process",
            process_config=ProcessConfig(
                process_count=1,
                queue_size=16,
                transport_mode="worker_pipes",
                batch_size=1,
                max_batch_wait_ms=0,
            ),
        )
        engine = ProcessExecutionEngine(config=config, worker_fn=_sync_worker)
        try:
            diagnostics = engine.get_runtime_metrics()

            assert isinstance(diagnostics, EngineRuntimeDiagnostics)
            assert diagnostics.process is not None
            metrics = diagnostics.process.batch_metrics
            assert metrics.transport_mode == "worker_pipes"
            assert metrics.support_state == "bounded"
            assert metrics.timer_flush_supported is False
            assert metrics.demand_flush_supported is False
            assert metrics.recycle_supported is False
        finally:
            await engine.shutdown()

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


class TestRetryLogic:
    @pytest.mark.asyncio
    async def test_success_on_retry(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_succeeds_on_second_attempt
        )
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(1.0)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.SUCCESS
            assert event.attempt == 2
            assert event.error is None
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_failure_after_max_retries(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_always_fails
        )
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(2.0)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.FAILURE
            assert event.attempt == retry_config.max_retries
            assert event.error is not None
            assert "Permanent failure" in event.error
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(
            config=retry_config, worker_fn=_worker_always_fails
        )
        try:
            start_time = time.time()
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(2.5)

            events = await engine.poll_completed_events()
            elapsed = time.time() - start_time

            assert len(events) == 1
            assert events[0].attempt == 3

            expected_min_delay = (50 + 100) / 1000.0
            assert elapsed >= expected_min_delay * 0.9
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_backoff_cap_enforced(self):
        config = ExecutionConfig(
            mode="process",
            max_in_flight=100,
            max_retries=5,
            retry_backoff_ms=100,
            exponential_backoff=True,
            max_retry_backoff_ms=200,
            retry_jitter_ms=0,
            process_config=ProcessConfig(
                process_count=1,
                queue_size=256,
                batch_size=1,
                max_batch_wait_ms=10,
                worker_join_timeout_ms=5000,
            ),
        )

        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(config=config, worker_fn=_worker_always_fails)
        try:
            start_time = time.time()
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(1.5)

            events = await engine.poll_completed_events()
            elapsed = time.time() - start_time

            assert len(events) == 1
            assert events[0].attempt == 5

            expected_total_backoff = (100 + 200 + 200 + 200) / 1000.0
            assert elapsed >= expected_total_backoff * 0.9
        finally:
            await engine.shutdown()

    @pytest.mark.asyncio
    async def test_attempt_count_on_immediate_success(self, retry_config):
        global _retry_counter
        _retry_counter = _RetryCounter()

        engine = ProcessExecutionEngine(config=retry_config, worker_fn=_sync_worker)
        try:
            await engine.submit(_make_work_item(0))
            await asyncio.sleep(0.5)

            events = await engine.poll_completed_events()
            assert len(events) == 1
            event = events[0]
            assert event.status == CompletionStatus.SUCCESS
            assert event.attempt == 1
        finally:
            await engine.shutdown()


class _FakeShutdownWorker:
    def __init__(self, pid: int, alive_states: list[bool]):
        self.pid = pid
        self._alive_states = list(alive_states)
        self.join_calls: list[float] = []
        self.terminate_calls = 0
        self.kill_calls = 0

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout if timeout is not None else -1.0)

    def is_alive(self) -> bool:
        if self._alive_states:
            return self._alive_states.pop(0)
        return False

    def terminate(self) -> None:
        self.terminate_calls += 1

    def kill(self) -> None:
        self.kill_calls += 1


class _FakeCloser:
    def __init__(self):
        self.closed = False
        self.items = []

    def put(self, item) -> None:
        self.items.append(item)

    def get_nowait(self):
        raise queue.Empty()

    def close(self) -> None:
        self.closed = True


class _FakeListener:
    def __init__(self):
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


class _FakeDrainQueue:
    def __init__(self, items=None):
        self._items = list(items or [])
        self.closed = False
        self.put_items = []

    def get_nowait(self):
        if not self._items:
            raise queue.Empty()
        return self._items.pop(0)

    def put(self, item) -> None:
        self.put_items.append(item)

    def close(self) -> None:
        self.closed = True


class TestShutdownLifecycle:
    @staticmethod
    def _build_shutdown_engine(worker: _FakeShutdownWorker) -> ProcessExecutionEngine:
        engine = cast(
            ProcessExecutionEngine,
            ProcessExecutionEngine.__new__(ProcessExecutionEngine),
        )
        engine._config = ExecutionConfig(
            mode="process",
            max_in_flight=10,
            max_retries=3,
            retry_backoff_ms=10,
            exponential_backoff=False,
            max_retry_backoff_ms=10,
            retry_jitter_ms=0,
            process_config=ProcessConfig(
                process_count=1,
                queue_size=8,
                batch_size=1,
                max_batch_wait_ms=10,
                worker_join_timeout_ms=50,
            ),
        )
        engine._workers = cast(list[Any], [worker])
        engine._batch_accumulator = cast(Any, _FakeCloser())
        engine._task_queue = cast(Any, _FakeCloser())
        engine._completion_queue = cast(Any, _FakeCloser())
        engine._registry_event_queue = cast(Any, _FakeCloser())
        engine._log_listener = cast(Any, _FakeListener())
        engine._prefetched_completion_events = deque()
        engine._in_flight_registry = {}
        engine._worker_pid_by_index = {}
        engine._in_flight_count = 0
        engine._in_flight_lock = __import__("threading").Lock()
        engine._logger = __import__("logging").getLogger(__name__)
        engine._is_shutdown = False
        engine._transport = SharedQueueProcessTransport(
            task_queue=cast(Any, engine._task_queue),
            get_batch_accumulator=lambda: cast(Any, engine._batch_accumulator),
            work_item_from_dict=process_engine._work_item_from_dict,
            increment_in_flight=lambda: None,
            sentinel=process_engine._SENTINEL,
        )
        setattr(engine, "_drain_registry_events", lambda: None)
        return engine

    @pytest.mark.asyncio
    async def test_shutdown_drains_registry_events_before_join(self):
        worker = _FakeShutdownWorker(pid=303, alive_states=[False])
        engine = self._build_shutdown_engine(worker)
        engine._registry_event_queue = _FakeDrainQueue(
            [
                {
                    "kind": "done",
                    "key": (0, "topic", 0, 42),
                }
            ]
        )
        engine._completion_queue = _FakeDrainQueue()
        engine._in_flight_registry = {
            (0, "topic", 0, 42): {
                "offset": 42,
                "topic": "topic",
                "partition": 0,
                "requeue_attempts": 0,
            }
        }

        await engine.shutdown()

        assert engine._in_flight_registry == {}
        assert worker.join_calls == [0.05]

    @pytest.mark.asyncio
    async def test_shutdown_rejoins_after_terminate_before_considering_kill(self):
        engine = self._build_shutdown_engine(
            _FakeShutdownWorker(pid=101, alive_states=[True, False])
        )

        await engine.shutdown()

        worker = engine._workers[0]
        assert worker.terminate_calls == 1
        assert worker.kill_calls == 0
        assert worker.join_calls == [0.05, 0.05]
        assert engine._log_listener.stopped is True

    @pytest.mark.asyncio
    async def test_shutdown_kills_worker_only_after_terminate_still_leaves_it_alive(
        self,
    ):
        engine = self._build_shutdown_engine(
            _FakeShutdownWorker(pid=202, alive_states=[True, True, False])
        )

        await engine.shutdown()

        worker = engine._workers[0]
        assert worker.terminate_calls == 1
        assert worker.kill_calls == 1
        assert worker.join_calls == [0.05, 0.05, 0.05]
        assert engine._log_listener.stopped is True
