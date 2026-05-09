# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_execution_engine_worker_runtime.py
# Role: Verifies worker runtime route-batch execution, batch completion envelopes, and fatal flush behavior.
# Extend here for focused process execution engine regression coverage in this area.

from pyrallel_consumer.dto import OrderingMode
from pyrallel_consumer.execution_plane.worker_spec import (
    BatchWorkerRuntimeSpec,
    WorkerSpec,
)
from tests.unit.execution_plane._process_execution_engine_support import (
    Any,
    CompletionStatus,
    ExecutionConfig,
    ExecutionMode,
    ProcessConfig,
    ProcessExecutionEngine,
    RouteBatch,
    TopicPartition,
    WorkItem,
    _serialize_batch_payload,
    _work_item_to_dict,
    _worker_loop,
    batch_completion_from_dict,
    cast,
    msgpack,
    pytest,
    queue,
    worker_runtime_module,
)


def test_worker_runtime_route_batch_invokes_batch_worker_once() -> None:
    # Given: a process route batch is handled by a public batch-worker spec.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-public-worker", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)
    seen_batches: list[list[WorkItem]] = []

    def batch_worker(batch: list[WorkItem]) -> None:
        seen_batches.append(list(batch))
        return None

    worker_spec = WorkerSpec.batch(
        batch_worker,
        BatchWorkerRuntimeSpec.from_config(
            ordering_mode=OrderingMode.KEY_HASH,
            batch_worker_config=object(),
            max_retries=1,
        ),
    )

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        worker_spec,  # type: ignore[arg-type]
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    raw_payload = msgpack.unpackb(completion_queue.get_nowait(), raw=False)
    completion = batch_completion_from_dict(raw_payload["completion"])

    # Then: process route-batch runtime invokes the batch worker once with all items.
    assert seen_batches == [items]
    assert raw_payload["kind"] == "batch_completion"
    assert [(event.id, event.status) for event in completion.results] == [
        ("work-1", CompletionStatus.SUCCESS),
        ("work-2", CompletionStatus.SUCCESS),
        ("work-3", CompletionStatus.SUCCESS),
    ]


def test_worker_runtime_executes_route_batch_items_in_order() -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime executes route batch items in order.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-ordered", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)
    executed_offsets: list[int] = []

    def record_order(item: WorkItem) -> None:
        executed_offsets.append(item.offset)

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        record_order,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime executes route batch items in order.
    assert executed_offsets == [1, 2, 3]


def test_worker_runtime_route_batch_emits_batch_start_without_item_starts() -> None:
    # Given: a route batch is delivered to a worker process.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-start", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
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

    registry_events = []
    while not registry_event_queue.empty():
        registry_events.append(cast(dict[str, Any], registry_event_queue.get_nowait()))

    # Then: route batches acknowledge membership once and do not use per-item start.
    assert [event.get("kind") for event in registry_events].count("batch_start") == 1
    assert [event for event in registry_events if event.get("kind") == "start"] == []
    batch_start = next(
        event for event in registry_events if event.get("kind") == "batch_start"
    )
    assert batch_start["batch_id"] == "batch-start"
    assert batch_start["worker_index"] == 0
    assert batch_start["item_ids"] == [item.id for item in items]
    assert batch_start["item_count"] == len(items)
    assert "payloads" not in batch_start


def test_worker_runtime_route_batch_failure_uses_one_child_attempt() -> None:
    # Given: a route batch worker fails and process max_retries is greater than one.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    item = WorkItem("work-1", TopicPartition("topic", 1), 1, 7, b"key", b"")
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-one-attempt", ("topic", 1, b"key"), 0, [item]),
            1.0,
        )
    )
    task_source.put(None)
    calls = 0

    def fail_once_per_parent_attempt(_item: WorkItem) -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("boom")

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        fail_once_per_parent_attempt,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=3,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    raw_payloads = []
    while not completion_queue.empty():
        raw_payloads.append(msgpack.unpackb(completion_queue.get_nowait(), raw=False))
    completion = batch_completion_from_dict(
        next(
            payload
            for payload in raw_payloads
            if payload.get("kind") == "batch_completion"
        )["completion"]
    )

    # Then: child-local retries are bypassed for process route-batch attempts.
    assert calls == 1
    assert [
        (event.offset, event.status, event.attempt) for event in completion.results
    ] == [(1, CompletionStatus.FAILURE, 1)]


def test_worker_runtime_stops_route_batch_after_first_failure() -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime stops route batch after first failure.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-fail", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)
    executed_offsets: list[int] = []

    def fail_second(item: WorkItem) -> None:
        executed_offsets.append(item.offset)
        if item.offset == 2:
            raise RuntimeError("boom")

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        fail_second,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    raw_payloads = []
    while not completion_queue.empty():
        raw_payloads.append(msgpack.unpackb(completion_queue.get_nowait(), raw=False))
    completion = batch_completion_from_dict(
        next(
            payload
            for payload in raw_payloads
            if payload.get("kind") == "batch_completion"
        )["completion"]
    )
    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime stops route batch after first failure.
    assert executed_offsets == [1, 2]
    assert [(event.offset, event.status) for event in completion.results] == [
        (1, CompletionStatus.SUCCESS),
        (2, CompletionStatus.FAILURE),
    ]
    assert [payload for payload in raw_payloads if "id" in payload] == []


def test_worker_runtime_emits_not_started_diagnostic_for_route_batch_remainder() -> (
    None
):
    # Given: worker runtime inputs are prepared to exercise worker runtime emits not-started diagnostic for route batch remainder.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-remainder", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fail_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise RuntimeError("boom")

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        fail_second,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    registry_events = []
    while not registry_event_queue.empty():
        registry_events.append(cast(dict[str, Any], registry_event_queue.get_nowait()))
    not_started_events = [
        event for event in registry_events if event.get("kind") == "not_started"
    ]
    # When: the worker runtime path produces diagnostics or completion output.
    # Then: the emitted output confirms that worker runtime emits not-started diagnostic for route batch remainder.
    assert not_started_events == [
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-remainder",
            "payloads": [_work_item_to_dict(items[2])],
        }
    ]


def test_worker_runtime_non_route_batch_keeps_item_level_completion_surface() -> None:
    # Given: worker runtime state is prepared for the worker runtime non route batch keeps item level completion surface scenario.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    item = WorkItem("work-1", TopicPartition("topic", 1), 1, 7, b"key", b"")
    task_source.put([_work_item_to_dict(item)])
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

    completion_payload = msgpack.unpackb(completion_queue.get_nowait(), raw=False)
    # When: the relevant worker runtime event or operation is applied.
    # Then: the test asserts that worker runtime non route batch keeps item level completion surface.
    assert completion_payload["id"] == "work-1"
    assert "batch_id" not in completion_payload
    assert "results" not in completion_payload


def test_worker_runtime_emits_batch_completion_for_executed_route_batch_prefix() -> (
    None
):
    # Given: worker runtime inputs are prepared to exercise worker runtime emits batch completion for executed route batch prefix.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-prefix", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fail_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise RuntimeError("boom")

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        fail_second,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    raw_payloads = [
        msgpack.unpackb(completion_queue.get_nowait(), raw=False)
        for _ in range(completion_queue.qsize())
    ]
    batch_payloads = [
        payload for payload in raw_payloads if payload.get("kind") == "batch_completion"
    ]
    item_payloads = [payload for payload in raw_payloads if "id" in payload]
    # When: the worker runtime path produces diagnostics or completion output.
    # Then: the emitted output confirms that worker runtime emits batch completion for executed route batch prefix.
    assert len(raw_payloads) == 1
    assert len(batch_payloads) == 1
    assert item_payloads == []
    completion = batch_completion_from_dict(batch_payloads[0]["completion"])
    assert completion.batch_id == "batch-prefix"
    assert completion.route_identity == ("topic", 1, b"key")
    assert [event.id for event in completion.results] == ["work-1", "work-2"]
    assert [event.offset for event in completion.results] == [1, 2]
    assert [event.status for event in completion.results] == [
        CompletionStatus.SUCCESS,
        CompletionStatus.FAILURE,
    ]
    assert completion.results[1].error == "boom"
    assert completion.results[1].attempt == 1


def test_worker_runtime_batch_completion_excludes_not_started_remainder() -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime batch completion excludes not-started remainder.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-skip", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fail_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise RuntimeError("boom")

    _worker_loop(
        task_source,
        completion_queue,  # type: ignore[arg-type]
        registry_event_queue,  # type: ignore[arg-type]
        fail_second,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    raw_payloads = [
        msgpack.unpackb(completion_queue.get_nowait(), raw=False)
        for _ in range(completion_queue.qsize())
    ]
    batch_payload = next(
        payload for payload in raw_payloads if payload.get("kind") == "batch_completion"
    )
    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime batch completion excludes not-started remainder.
    assert len(raw_payloads) == 1
    assert [payload for payload in raw_payloads if "id" in payload] == []
    completion = batch_completion_from_dict(batch_payload["completion"])
    assert [event.id for event in completion.results] == ["work-1", "work-2"]

    registry_events = [
        cast(dict[str, Any], registry_event_queue.get_nowait())
        for _ in range(registry_event_queue.qsize())
    ]
    not_started_events = [
        event for event in registry_events if event.get("kind") == "not_started"
    ]
    assert not_started_events == [
        {
            "kind": "not_started",
            "reason": "ordered_batch_failure",
            "batch_id": "batch-skip",
            "payloads": [_work_item_to_dict(items[2])],
        }
    ]


def test_worker_runtime_batch_completion_send_failure_is_diagnostic() -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime batch completion send failure is diagnostic.
    class ExplodingCompletionQueue:
        def __init__(self) -> None:
            self.payloads: list[object] = []

        def put(self, payload: object) -> None:
            decoded = msgpack.unpackb(cast(bytes, payload), raw=False)
            if decoded.get("kind") == "batch_completion":
                raise RuntimeError("batch-wire-down")
            self.payloads.append(payload)

    task_source: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    completion_queue = ExplodingCompletionQueue()
    item = WorkItem("work-1", TopicPartition("topic", 1), 1, 7, b"key", b"")
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-send-failure", ("topic", 1, b"key"), 0, [item]),
            1.0,
        )
    )
    task_source.put(None)

    _worker_loop(
        task_source,
        cast(Any, completion_queue),
        registry_event_queue,  # type: ignore[arg-type]
        lambda _item: None,
        0,
        ExecutionConfig(
            mode=ExecutionMode.PROCESS,
            max_retries=1,
            process_config=ProcessConfig(process_count=1),
        ),
    )

    registry_events = [
        cast(dict[str, Any], registry_event_queue.get_nowait())
        for _ in range(registry_event_queue.qsize())
    ]
    diagnostics = [
        event
        for event in registry_events
        if event.get("kind") == "batch_completion_send_failed"
    ]
    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime batch completion send failure is diagnostic.
    assert diagnostics == [
        {
            "kind": "batch_completion_send_failed",
            "batch_id": "batch-send-failure",
            "error": "batch-wire-down",
        }
    ]
    fallback_payloads = [
        msgpack.unpackb(cast(bytes, payload), raw=False)
        for payload in completion_queue.payloads
    ]
    assert [
        (payload["id"], payload["offset"], payload["status"])
        for payload in fallback_payloads
    ] == [("work-1", 1, "success")]


def test_worker_runtime_route_batch_emits_only_batch_completion_envelope() -> None:
    # Given: worker runtime inputs are prepared to exercise worker runtime route batch emits only batch completion envelope.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    item = WorkItem("work-1", TopicPartition("topic", 1), 1, 7, b"key", b"")
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-item-level", ("topic", 1, b"key"), 0, [item]),
            1.0,
        )
    )
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

    raw_payloads = [
        msgpack.unpackb(completion_queue.get_nowait(), raw=False)
        for _ in range(completion_queue.qsize())
    ]
    # When: the worker runtime path produces diagnostics or completion output.
    # Then: the emitted output confirms that worker runtime route batch emits only batch completion envelope.
    assert len(raw_payloads) == 1
    assert [payload for payload in raw_payloads if "id" in payload] == []
    assert raw_payloads[0]["kind"] == "batch_completion"


def test_worker_runtime_defers_route_batch_done_until_completion_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime defers route batch done until completion flush.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    item = WorkItem("work-1", TopicPartition("topic", 1), 1, 7, b"key", b"")
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-done-after-flush", ("topic", 1, b"key"), 0, [item]),
            1.0,
        )
    )
    task_source.put(None)
    original_flush = worker_runtime_module._flush_route_batch_completion
    seen_done_before_flush: list[dict[str, Any]] = []

    def capture_flush(**kwargs: Any) -> None:
        queue_ref = cast(queue.Queue[object], kwargs["registry_event_queue"])
        seen_done_before_flush.extend(
            cast(dict[str, Any], event)
            for event in list(queue_ref.queue)
            if isinstance(event, dict) and event.get("kind") == "done"
        )
        original_flush(**kwargs)

    monkeypatch.setattr(
        worker_runtime_module,
        "_flush_route_batch_completion",
        capture_flush,
    )

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

    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime defers route batch done until completion flush.
    assert seen_done_before_flush == []
    registry_events = [
        cast(dict[str, Any], registry_event_queue.get_nowait())
        for _ in range(registry_event_queue.qsize())
    ]
    assert [event.get("kind") for event in registry_events].count("done") == 1


def test_worker_runtime_fatal_route_batch_flushes_prefix_completion_before_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime fatal route batch flushes prefix completion before exit.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-fatal-prefix", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fake_exit(code: int) -> None:
        raise SystemExit(code)

    def timeout_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise TimeoutError("fatal-timeout")

    monkeypatch.setattr(worker_runtime_module.os, "_exit", fake_exit)

    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime fatal route batch flushes prefix completion before exit.
    with pytest.raises(SystemExit):
        _worker_loop(
            task_source,
            completion_queue,  # type: ignore[arg-type]
            registry_event_queue,  # type: ignore[arg-type]
            timeout_second,
            0,
            ExecutionConfig(
                mode=ExecutionMode.PROCESS,
                max_retries=1,
                process_config=ProcessConfig(process_count=1),
            ),
        )

    raw_payloads = [
        msgpack.unpackb(completion_queue.get_nowait(), raw=False)
        for _ in range(completion_queue.qsize())
    ]
    assert len(raw_payloads) == 1
    assert raw_payloads[0]["kind"] == "batch_completion"
    completion = batch_completion_from_dict(raw_payloads[0]["completion"])
    assert [(event.id, event.offset, event.status) for event in completion.results] == [
        ("work-1", 1, CompletionStatus.SUCCESS)
    ]

    registry_events = [
        cast(dict[str, Any], registry_event_queue.get_nowait())
        for _ in range(registry_event_queue.qsize())
    ]
    assert [event for event in registry_events if event.get("kind") == "timeout"]
    assert [
        event
        for event in registry_events
        if event.get("kind") == "done"
        and isinstance(event.get("payload"), dict)
        and event["payload"].get("offset") == 1
    ]
    assert [
        event for event in registry_events if event.get("kind") == "not_started"
    ] == []


def test_parent_expands_fatal_route_batch_prefix_batch_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: worker runtime inputs and fakes are prepared for parent expands fatal route batch prefix batch completion.
    task_source: queue.Queue[object] = queue.Queue()
    completion_queue: queue.Queue[object] = queue.Queue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-fatal-parent", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fake_exit(code: int) -> None:
        raise SystemExit(code)

    def timeout_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise TimeoutError("fatal-timeout")

    monkeypatch.setattr(worker_runtime_module.os, "_exit", fake_exit)

    # When: the relevant worker runtime code path is exercised.
    # Then: the assertions confirm that parent expands fatal route batch prefix batch completion.
    with pytest.raises(SystemExit):
        _worker_loop(
            task_source,
            completion_queue,  # type: ignore[arg-type]
            registry_event_queue,  # type: ignore[arg-type]
            timeout_second,
            0,
            ExecutionConfig(
                mode=ExecutionMode.PROCESS,
                max_retries=1,
                process_config=ProcessConfig(process_count=1),
            ),
        )

    engine = ProcessExecutionEngine.__new__(ProcessExecutionEngine)
    engine_any = cast(Any, engine)
    engine_any._config = ExecutionConfig(
        mode=ExecutionMode.PROCESS,
        process_config=ProcessConfig(process_count=1),
    )
    engine._initialize_runtime_timing_state()

    events = engine._decode_completion_queue_item_events(completion_queue.get_nowait())

    assert [(event.id, event.offset, event.status) for event in events] == [
        ("work-1", 1, CompletionStatus.SUCCESS)
    ]
    assert engine_any._completion_batch_payload_count == 1
    assert engine_any._completion_item_payload_count == 0


def test_worker_runtime_fatal_route_batch_prefix_flush_falls_back_to_item_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: worker runtime route batch and completion payloads are prepared for worker runtime fatal route batch prefix flush falls back to item completion.
    class ExplodingBatchCompletionQueue:
        def __init__(self) -> None:
            self.payloads: list[object] = []

        def put(self, payload: object) -> None:
            decoded = msgpack.unpackb(cast(bytes, payload), raw=False)
            if decoded.get("kind") == "batch_completion":
                raise RuntimeError("fatal-batch-wire-down")
            self.payloads.append(payload)

    task_source: queue.Queue[object] = queue.Queue()
    completion_queue = ExplodingBatchCompletionQueue()
    registry_event_queue: queue.Queue[object] = queue.Queue()
    items = [
        WorkItem(f"work-{offset}", TopicPartition("topic", 1), offset, 7, b"key", b"")
        for offset in (1, 2, 3)
    ]
    task_source.put(
        _serialize_batch_payload(
            RouteBatch("batch-fatal-fallback", ("topic", 1, b"key"), 0, items),
            1.0,
        )
    )
    task_source.put(None)

    def fake_exit(code: int) -> None:
        raise SystemExit(code)

    def timeout_second(item: WorkItem) -> None:
        if item.offset == 2:
            raise TimeoutError("fatal-timeout")

    monkeypatch.setattr(worker_runtime_module.os, "_exit", fake_exit)

    # When: the worker runtime executes the prepared payloads.
    # Then: the completion envelope or diagnostic output confirms that worker runtime fatal route batch prefix flush falls back to item completion.
    with pytest.raises(SystemExit):
        _worker_loop(
            task_source,
            cast(Any, completion_queue),
            registry_event_queue,  # type: ignore[arg-type]
            timeout_second,
            0,
            ExecutionConfig(
                mode=ExecutionMode.PROCESS,
                max_retries=1,
                process_config=ProcessConfig(process_count=1),
            ),
        )

    fallback_payloads = [
        msgpack.unpackb(cast(bytes, payload), raw=False)
        for payload in completion_queue.payloads
    ]
    assert [
        (payload["id"], payload["offset"], payload["status"])
        for payload in fallback_payloads
    ] == [("work-1", 1, "success")]
    diagnostics = [
        cast(dict[str, Any], registry_event_queue.get_nowait())
        for _ in range(registry_event_queue.qsize())
    ]
    assert [
        event
        for event in diagnostics
        if event.get("kind") == "batch_completion_send_failed"
    ] == [
        {
            "kind": "batch_completion_send_failed",
            "batch_id": "batch-fatal-fallback",
            "error": "fatal-batch-wire-down",
        }
    ]
