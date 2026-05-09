# -*- coding: utf-8 -*-
# File: tests/unit/test_consumer.py
# Role: Verifies the PyrallelConsumer facade lifecycle, metrics wiring, resource signals, and poller delegation.
# Extend here for public consumer facade lifecycle or sidecar integration changes.

import asyncio
import queue
import time
from collections import deque
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import (
    BatchCompletion,
    CompletionStatus,
    ExecutionMode,
    OrderingMode,
    ResourceSignalSnapshot,
    ResourceSignalStatus,
    RouteBatch,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.process_codec import (
    batch_completion_from_dict,
    decode_batch_completion_payload,
    serialize_batch_completion_payload,
    serialize_batch_payload,
    work_item_to_dict,
)
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine
from pyrallel_consumer.execution_plane.process_worker_runtime import _worker_loop
from pyrallel_consumer.execution_plane.worker_spec import WorkerSpec
from pyrallel_consumer.worker import BatchItemOutcome, BatchWorkerContractError

_PROCESS_BATCH_FACADE_SEEN_IDS: list[list[str]] = []


def _process_batch_facade_mixed_worker(
    items: Sequence[WorkItem],
) -> dict[str, BatchItemOutcome]:
    _PROCESS_BATCH_FACADE_SEEN_IDS.append([item.id for item in items])
    return {
        "work-success": BatchItemOutcome.success(),
        "work-retry": BatchItemOutcome.failure("retryable failure"),
        "work-terminal": BatchItemOutcome.failure("terminal failure"),
    }


def _process_batch_facade_invalid_worker(
    items: Sequence[WorkItem],
) -> dict[str, BatchItemOutcome]:
    return {items[0].id: BatchItemOutcome.success()}


class _DummyEngine:
    def __init__(self):
        self.shutdown_called = False

    async def shutdown(self):
        self.shutdown_called = True


class _DummyPrometheusExporter:
    instances: list["_DummyPrometheusExporter"] = []

    def __init__(self, config):
        self.config = config
        self.system_metrics_updates = []
        self.pipeline_diagnostics_updates = []
        self.completion_updates = []
        self.closed = False
        _DummyPrometheusExporter.instances.append(self)

    def update_from_system_metrics(self, metrics) -> None:
        self.system_metrics_updates.append(metrics)

    def update_pipeline_diagnostics(self, diagnostics, *, engine_type: str) -> None:
        self.pipeline_diagnostics_updates.append((diagnostics, engine_type))

    def observe_completion(self, tp, status, duration_seconds: float) -> None:
        self.completion_updates.append((tp, status, duration_seconds))

    def close(self) -> None:
        self.closed = True


class _DummyWorkManager:
    def __init__(
        self,
        *,
        execution_engine,
        max_in_flight_messages,
        metrics_exporter=None,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        poison_message_circuit=None,
        route_batch_size=None,
    ):
        self.execution_engine = execution_engine
        self.max_in_flight_messages = max_in_flight_messages
        self.metrics_exporter = metrics_exporter
        self.ordering_mode = ordering_mode
        self.max_revoke_grace_ms = max_revoke_grace_ms
        self.poison_message_circuit = poison_message_circuit
        self.route_batch_size = route_batch_size

    def set_metrics_exporter(self, metrics_exporter) -> None:
        self.metrics_exporter = metrics_exporter


class _DummyPoller:
    def __init__(self, *, consume_topic, kafka_config, execution_engine, work_manager):
        self.consume_topic = consume_topic
        self.kafka_config = kafka_config
        self.execution_engine = execution_engine
        self.work_manager = work_manager
        self.started = False
        self.stopped = False
        self.wait_closed_called = False
        self.metrics = SimpleNamespace(source="dummy")
        self.runtime_snapshot = SimpleNamespace(source="runtime")
        self.pipeline_diagnostics = SimpleNamespace(source="pipeline")

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def wait_closed(self):
        self.wait_closed_called = True

    def get_metrics(self):
        return self.metrics

    def get_runtime_snapshot(self):
        return self.runtime_snapshot

    def get_pipeline_diagnostics(self):
        return self.pipeline_diagnostics


class _DummyResourceSignalProvider:
    def snapshot(self) -> ResourceSignalSnapshot:
        return ResourceSignalSnapshot(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.25,
            memory_utilization=0.5,
        )


class _FailingOnceResourceSignalProvider:
    def __init__(self) -> None:
        self.calls = 0

    def snapshot(self) -> ResourceSignalSnapshot:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("resource sampler failed")
        return ResourceSignalSnapshot(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.75,
            memory_utilization=0.5,
        )


class _FailingStopPoller(_DummyPoller):
    async def stop(self):
        self.stopped = True
        raise RuntimeError("poller failed")


class _FailingStartPoller(_DummyPoller):
    async def start(self):
        raise RuntimeError("poller start failed")


def test_pyrallel_consumer_constructor_docstring_documents_resource_signals():
    # Given: the PyrallelConsumer constructor docstring is available.
    docstring = PyrallelConsumer.__init__.__doc__

    # When: the docstring is inspected for resource signal guidance.
    # Then: resource signal provider, fail-open, and no-raise contracts are documented.
    assert docstring is not None
    assert "resource_signal_provider" in docstring
    assert "NullResourceSignalProvider" in docstring
    assert "fail-open" in docstring
    assert "must not raise" in docstring


def test_pyrallel_consumer_from_batch_worker_rejects_partition_mode() -> None:
    # Given: a config requests partition ordering for a public batch worker.
    config = KafkaConfig()
    config.parallel_consumer.ordering_mode = OrderingMode.PARTITION

    # When: the batch-worker facade is assembled.
    # Then: v1 fails closed until the LeasedBatch partition gate lands.
    with pytest.raises(
        ValueError,
        match="batch_worker_partition_ordering_unsupported_until_leased_batch_gate",
    ):
        PyrallelConsumer.from_batch_worker(
            config=config,
            batch_worker=lambda items: None,
            topic="demo",
        )


def test_pyrallel_consumer_from_batch_worker_rejects_adaptive_concurrency() -> None:
    # Given: adaptive concurrency is enabled.
    config = KafkaConfig()
    config.parallel_consumer.adaptive_concurrency.enabled = True

    # When: the batch-worker facade is assembled.
    # Then: v1 rejects the combination until the live-capacity gate lands.
    with pytest.raises(
        ValueError,
        match="batch_worker_adaptive_concurrency_unsupported_until_live_capacity_gate",
    ):
        PyrallelConsumer.from_batch_worker(
            config=config,
            batch_worker=lambda items: None,
            topic="demo",
        )


def test_pyrallel_consumer_from_batch_worker_rejects_adaptive_backpressure() -> None:
    # Given: adaptive backpressure is enabled.
    config = KafkaConfig()
    config.parallel_consumer.adaptive_backpressure.enabled = True

    # When: the batch-worker facade is assembled.
    # Then: v1 rejects the combination until the live-capacity gate lands.
    with pytest.raises(
        ValueError,
        match="batch_worker_adaptive_backpressure_unsupported_until_live_capacity_gate",
    ):
        PyrallelConsumer.from_batch_worker(
            config=config,
            batch_worker=lambda items: None,
            topic="demo",
        )


def test_pyrallel_consumer_from_batch_worker_opens_async_runtime_path(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: dummy facade dependencies are installed and key-hash batch worker mode is used.
    captured_worker: WorkerSpec | None = None

    def _create_engine(execution_config, worker):  # noqa: ARG001
        nonlocal captured_worker
        captured_worker = worker
        return _DummyEngine()

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )

    config = KafkaConfig()

    def batch_worker(items):
        return None

    consumer = PyrallelConsumer.from_batch_worker(
        config=config,
        batch_worker=batch_worker,
        topic="demo",
    )

    # Then: async public batch workers use the batch WorkerSpec runtime path.
    assert consumer._execution_engine is not None
    assert captured_worker is not None
    assert captured_worker.kind == "batch"
    assert captured_worker.callable is batch_worker
    assert captured_worker.batch_runtime is not None
    assert captured_worker.batch_runtime.ordering_mode == OrderingMode.KEY_HASH
    assert consumer._work_manager._batch_dispatch_enabled is True


def test_pyrallel_consumer_from_batch_worker_opens_process_runtime_path(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: process mode is configured after process batch runtime support lands.
    captured_worker: WorkerSpec | None = None

    def _create_engine(execution_config, worker):  # noqa: ARG001
        nonlocal captured_worker
        captured_worker = worker
        return _DummyEngine()

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = ExecutionMode.PROCESS

    def batch_worker(items):
        return None

    consumer = PyrallelConsumer.from_batch_worker(
        config=config,
        batch_worker=batch_worker,
        topic="demo",
    )

    # Then: process public batch workers use the same batch WorkerSpec runtime path.
    assert consumer._execution_engine is not None
    assert captured_worker is not None
    assert captured_worker.kind == "batch"
    assert captured_worker.callable is batch_worker
    assert captured_worker.batch_runtime is not None
    assert consumer._work_manager._batch_dispatch_enabled is True


def test_pyrallel_consumer_process_batch_worker_clamps_route_batch_size(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: public batch size is smaller than the process transport route batch size.
    def _create_engine(execution_config, worker):  # noqa: ARG001
        return _DummyEngine()

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = ExecutionMode.PROCESS
    config.parallel_consumer.batch_worker.max_batch_size = 5
    config.parallel_consumer.execution.process_config.route_batch_size = 64

    def batch_worker(items):
        return None

    consumer = PyrallelConsumer.from_batch_worker(
        config=config,
        batch_worker=batch_worker,
        topic="demo",
    )

    # Then: process public batch-worker leases are capped by the public max size.
    assert consumer._work_manager._route_batch_size == 5


def test_pyrallel_consumer_process_batch_facade_contract_handles_mixed_and_stale(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: the public facade creates a process batch-worker runtime path.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = ExecutionMode.PROCESS
    config.parallel_consumer.execution.max_retries = 3
    config.parallel_consumer.ordering_mode = OrderingMode.UNORDERED
    _PROCESS_BATCH_FACADE_SEEN_IDS.clear()

    consumer = PyrallelConsumer.from_batch_worker(
        config=config,
        batch_worker=_process_batch_facade_mixed_worker,
        topic="demo",
    )
    engine = cast(ProcessExecutionEngine, consumer._execution_engine)
    engine_any = cast(Any, engine)
    worker_spec = cast(WorkerSpec, engine_any._worker_fn)
    items = [
        WorkItem("work-success", TopicPartition("topic", 1), 50, 7, b"key", b"a"),
        WorkItem("work-retry", TopicPartition("topic", 1), 51, 7, b"key", b"b"),
        WorkItem("work-terminal", TopicPartition("topic", 1), 52, 7, b"key", b"c"),
    ]

    # When: the facade WorkerSpec is exercised by the process batch runtime.
    task_source: queue.Queue[object] = queue.Queue()
    worker_completion_queue: queue.Queue[object] = queue.Queue()
    worker_registry_queue: queue.Queue[object] = queue.Queue()
    task_source.put(
        serialize_batch_payload(
            RouteBatch("batch-facade-worker", ("topic", 1, b"key"), 0, items),
            time.monotonic(),
        )
    )
    task_source.put(None)
    _worker_loop(
        task_source,
        worker_completion_queue,  # type: ignore[arg-type]
        worker_registry_queue,  # type: ignore[arg-type]
        worker_spec,
        0,
        config.parallel_consumer.execution,
    )
    worker_payload = decode_batch_completion_payload(
        worker_completion_queue.get_nowait(),
        config.parallel_consumer.execution.process_config.msgpack_max_bytes,
    )
    worker_completion = batch_completion_from_dict(worker_payload["completion"])

    # Then: the process batch worker receives list[WorkItem] through the facade path.
    assert worker_spec.kind == "batch"
    assert consumer._work_manager._batch_dispatch_enabled is True
    assert _PROCESS_BATCH_FACADE_SEEN_IDS == [
        ["work-success", "work-retry", "work-terminal"]
    ]
    assert [(event.id, event.status) for event in worker_completion.results] == [
        ("work-success", CompletionStatus.SUCCESS),
        ("work-retry", CompletionStatus.FAILURE),
        ("work-terminal", CompletionStatus.FAILURE),
    ]

    # When: parent finalization sees success, retryable failure, terminal failure,
    # and a child done event for the retryable failure before completion.
    dispatched_batches: list[RouteBatch] = []

    class RecordingTransport:
        def handle_registry_event(self, _event: dict[str, Any]) -> None:
            return None

        def dispatch_route_batch(
            self,
            route_batch: RouteBatch,
            *,
            route_identity: object,
            count_in_flight: bool,
        ) -> None:
            del route_identity, count_in_flight
            dispatched_batches.append(route_batch)

    success_payload = work_item_to_dict(items[0])
    retry_payload = work_item_to_dict(items[1])
    terminal_payload = work_item_to_dict(items[2])
    terminal_payload["requeue_attempts"] = 3
    engine_any._transport = RecordingTransport()
    engine_any._completion_queue = queue.Queue()
    engine_any._registry_event_queue = queue.Queue()
    engine_any._prefetched_completion_events = deque()
    engine_any._seen_completion_identities = set()
    engine_any._seen_completion_identity_order = deque()
    engine_any._in_flight_registry = {
        (0, "topic", 1, 50): success_payload,
        (0, "topic", 1, 51): retry_payload,
        (0, "topic", 1, 52): terminal_payload,
    }
    engine_any._in_flight_count = 3
    engine_any._process_batch_manifests = {}
    engine_any._active_process_batch_ids = {"batch-facade-parent"}
    engine_any._process_control_events = deque()
    engine_any._registry_event_queue.put(
        {"kind": "done", "key": (0, "topic", 1, 51), "payload": retry_payload}
    )
    engine_any._completion_queue.put(
        serialize_batch_completion_payload(
            BatchCompletion(
                batch_id="batch-facade-parent",
                route_identity=("topic", 1, b"key"),
                results=worker_completion.results,
            ),
            completion_enqueued_at=time.monotonic(),
        )
    )

    completed_events = asyncio.run(engine.poll_completed_events())

    # Then: success is committable, retryable failure redispatches, terminal failure finalizes.
    assert [
        (event.id, event.status, event.error, event.attempt)
        for event in completed_events
    ] == [
        ("work-success", CompletionStatus.SUCCESS, None, 1),
        ("work-terminal", CompletionStatus.FAILURE, "terminal failure", 3),
    ]
    assert [
        (item.id, item.requeue_attempts) for item in dispatched_batches[0].items
    ] == [("work-retry", 1)]

    # When: a stale batch completion arrives after parent ownership moved on.
    engine_any._active_process_batch_ids = {"batch-current"}
    engine_any._completion_queue.put(
        serialize_batch_completion_payload(
            BatchCompletion(
                batch_id="batch-stale",
                route_identity=("topic", 1, b"key"),
                results=[worker_completion.results[1]],
            ),
            completion_enqueued_at=time.monotonic(),
        )
    )

    # Then: stale completions do not create completion, retry, or finalization output.
    assert asyncio.run(engine.poll_completed_events()) == []
    assert len(dispatched_batches) == 1


def test_pyrallel_consumer_process_batch_facade_invalid_result_is_fatal_only(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a process batch worker created through the facade returns an invalid result.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = ExecutionMode.PROCESS

    consumer = PyrallelConsumer.from_batch_worker(
        config=config,
        batch_worker=_process_batch_facade_invalid_worker,
        topic="demo",
    )
    engine = cast(ProcessExecutionEngine, consumer._execution_engine)
    engine_any = cast(Any, engine)
    worker_spec = cast(WorkerSpec, engine_any._worker_fn)
    items = [
        WorkItem("work-invalid-a", TopicPartition("topic", 1), 60, 7, b"key", b"a"),
        WorkItem("work-invalid-b", TopicPartition("topic", 1), 61, 7, b"key", b"b"),
    ]
    task_source: queue.Queue[object] = queue.Queue()
    worker_completion_queue: queue.Queue[object] = queue.Queue()
    worker_registry_queue: queue.Queue[object] = queue.Queue()
    task_source.put(
        serialize_batch_payload(
            RouteBatch("batch-invalid-facade", ("topic", 1, b"key"), 0, items),
            time.monotonic(),
        )
    )
    task_source.put(None)

    # When: the invalid batch result is executed by the process worker runtime.
    _worker_loop(
        task_source,
        worker_completion_queue,  # type: ignore[arg-type]
        worker_registry_queue,  # type: ignore[arg-type]
        worker_spec,
        0,
        config.parallel_consumer.execution,
    )
    engine_any._transport = type(
        "NoopTransport",
        (),
        {"handle_registry_event": lambda self, event: None},
    )()
    engine_any._process_control_events = deque()
    for _ in range(worker_registry_queue.qsize()):
        engine._apply_registry_event(
            cast(dict[str, Any], worker_registry_queue.get_nowait())
        )

    control_events = asyncio.run(engine.poll_control_events())

    # Then: invalid contracts surface only as fatal control events.
    assert worker_completion_queue.empty()
    assert len(control_events) == 1
    assert control_events[0].kind == "fatal"
    assert isinstance(control_events[0].error, BatchWorkerContractError)


@pytest.mark.asyncio
async def test_pyrallel_consumer_starts_and_stops(monkeypatch: MonkeyPatch):
    # Given: dummy engine, work manager, and poller factories are installed.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        metrics_exporter=None,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
            poison_message_circuit=poison_message_circuit,
        )
        return dummy_work_manager

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = cast(KafkaConfig, cast(object, SimpleNamespace(parallel_consumer=None)))

    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    assert dummy_work_manager is not None

    # When: PyrallelConsumer starts and then stops with the dummy dependencies.
    await consumer.start()
    await consumer.stop()

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    # Then: poller lifecycle, engine shutdown, and metric access all occur as expected.
    assert dummy_poller.started is True
    assert dummy_poller.stopped is True
    assert dummy_engine.shutdown_called is True
    assert dummy_poller.metrics.source == "dummy"


@pytest.mark.asyncio
async def test_pyrallel_consumer_auto_wires_metrics_exporter_when_enabled(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled and dummy consumer dependencies are installed.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        dummy_work_manager.metrics_exporter = metrics_exporter
        return dummy_work_manager

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9911

    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    # When: PyrallelConsumer starts and stops with the dummy Prometheus exporter.
    # Then: the exporter is created on start, wired into the work manager, updated, closed, and cleared.
    assert dummy_work_manager is not None
    assert dummy_work_manager.metrics_exporter is None

    await consumer.start()
    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)
    assert exporter.config.port == 9911
    assert dummy_work_manager.metrics_exporter is exporter
    await consumer.stop()

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    assert exporter.system_metrics_updates == [
        dummy_poller.metrics,
        dummy_poller.metrics,
    ]
    assert exporter.pipeline_diagnostics_updates == [
        (dummy_poller.pipeline_diagnostics, "async"),
        (dummy_poller.pipeline_diagnostics, "async"),
    ]
    assert exporter.closed is True
    assert dummy_engine.shutdown_called is True
    assert dummy_work_manager.metrics_exporter is None
    assert consumer._metrics_exporter is None
    assert consumer._metrics_task is None


@pytest.mark.asyncio
async def test_pyrallel_consumer_publishes_pipeline_diagnostics_with_process_engine_type(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled and execution mode is set to process.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        return _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
            poison_message_circuit=poison_message_circuit,
        )

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9912
    config.parallel_consumer.execution.mode = ExecutionMode.PROCESS

    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    await consumer.start()
    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)
    await consumer.stop()

    # When: PyrallelConsumer starts and stops with dummy metrics dependencies.
    # Then: pipeline diagnostics are published with process as the engine type.
    assert dummy_poller is not None
    assert exporter.pipeline_diagnostics_updates == [
        (dummy_poller.pipeline_diagnostics, "process"),
        (dummy_poller.pipeline_diagnostics, "process"),
    ]


@pytest.mark.asyncio
async def test_pyrallel_consumer_publishes_resource_signal_snapshot(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled with a resource signal provider returning available CPU and memory values.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        return _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
            poison_message_circuit=poison_message_circuit,
        )

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9921

    consumer = PyrallelConsumer(
        config=config,
        worker=lambda _: None,
        topic="demo",
        resource_signal_provider=_DummyResourceSignalProvider(),
    )

    await consumer.start()
    await consumer.stop()

    exporter = _DummyPrometheusExporter.instances[-1]
    # When: PyrallelConsumer starts and stops while exporting metrics.
    # Then: system metrics include the available resource signal snapshot.
    assert exporter.system_metrics_updates[0].resource_signal is not None
    assert (
        exporter.system_metrics_updates[0].resource_signal.status
        == ResourceSignalStatus.AVAILABLE
    )
    assert exporter.system_metrics_updates[0].resource_signal.cpu_utilization == 0.25


@pytest.mark.asyncio
async def test_pyrallel_consumer_fails_open_when_resource_signal_provider_raises(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled with a resource signal provider that fails on its first sample.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        return _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
            poison_message_circuit=poison_message_circuit,
        )

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9922

    consumer = PyrallelConsumer(
        config=config,
        worker=lambda _: None,
        topic="demo",
        resource_signal_provider=_FailingOnceResourceSignalProvider(),
    )

    await consumer.start()

    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)
    # When: PyrallelConsumer starts, samples metrics, and then stops.
    # Then: the first failed sample is exported as unavailable and later samples recover to available.
    assert exporter.system_metrics_updates[0].resource_signal is not None
    assert (
        exporter.system_metrics_updates[0].resource_signal.status
        == ResourceSignalStatus.UNAVAILABLE
    )

    await consumer.stop()

    assert (
        exporter.system_metrics_updates[-1].resource_signal.status
        == ResourceSignalStatus.AVAILABLE
    )
    assert dummy_engine.shutdown_called is True


@pytest.mark.asyncio
async def test_pyrallel_consumer_creates_exporter_on_start_not_init(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled before constructing PyrallelConsumer.
    def _create_engine(execution_config, worker):  # noqa: ARG001
        return _DummyEngine()

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        return _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )
    _DummyPrometheusExporter.instances.clear()

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9914
    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    # When: the consumer is initialized, then started and stopped.
    # Then: the exporter is created only during start and closed during stop.
    assert len(_DummyPrometheusExporter.instances) == 0
    assert consumer._metrics_exporter is None

    await consumer.start()

    assert len(_DummyPrometheusExporter.instances) == 1
    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)
    assert exporter.closed is False

    await consumer.stop()
    assert exporter.closed is True


@pytest.mark.asyncio
async def test_pyrallel_consumer_metrics_cleanup_on_start_failure(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled and the dummy poller raises during start.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        return manager

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _FailingStartPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )
    _DummyPrometheusExporter.instances.clear()

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9912
    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    # When: PyrallelConsumer.start is invoked against the failing poller.
    # Then: the startup error propagates while exporter, engine, and metric task state are cleaned up.
    with pytest.raises(RuntimeError, match="poller start failed"):
        await consumer.start()

    assert len(_DummyPrometheusExporter.instances) == 1
    assert _DummyPrometheusExporter.instances[0].closed is True
    assert dummy_engine.shutdown_called is True
    assert consumer._metrics_exporter is None
    assert consumer._metrics_task is None


@pytest.mark.asyncio
async def test_pyrallel_consumer_stop_updates_metrics_even_when_poller_stop_fails(
    monkeypatch: MonkeyPatch,
):
    # Given: metrics are enabled and the dummy poller raises during stop.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        metrics_exporter=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        return dummy_work_manager

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _FailingStopPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)
    monkeypatch.setattr(
        "pyrallel_consumer.consumer.PrometheusMetricsExporter",
        _DummyPrometheusExporter,
    )

    config = KafkaConfig()
    config.metrics.enabled = True
    config.metrics.port = 9913
    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    await consumer.start()
    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)

    # When: PyrallelConsumer starts and then attempts to stop.
    # Then: the stop error propagates after final metrics update, exporter close, and engine shutdown.
    with pytest.raises(RuntimeError, match="poller failed"):
        await consumer.stop()

    assert exporter.system_metrics_updates
    assert exporter.system_metrics_updates[-1].source == "dummy"
    assert exporter.closed is True
    assert dummy_engine.shutdown_called is True
    assert dummy_work_manager is not None
    assert dummy_work_manager.metrics_exporter is None


@pytest.mark.asyncio
async def test_pyrallel_consumer_uses_configured_ordering_mode(
    monkeypatch: MonkeyPatch,
):
    # Given: KafkaConfig sets parallel consumer ordering mode to partition.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        metrics_exporter=None,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        return dummy_work_manager

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        return _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)

    config = KafkaConfig()
    config.parallel_consumer.ordering_mode = OrderingMode.PARTITION

    PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    # When: PyrallelConsumer is constructed with dummy dependencies.
    # Then: the work manager receives the configured partition ordering mode.
    assert dummy_work_manager is not None
    assert dummy_work_manager.ordering_mode == OrderingMode.PARTITION


@pytest.mark.asyncio
async def test_pyrallel_consumer_stop_still_shuts_down_engine_on_poller_failure(
    monkeypatch: MonkeyPatch,
):
    # Given: the dummy poller raises during stop.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        metrics_exporter=None,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        return dummy_work_manager

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _FailingStopPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)

    config = cast(KafkaConfig, cast(object, SimpleNamespace(parallel_consumer=None)))
    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    # When: PyrallelConsumer.stop is invoked.
    # Then: the stop error propagates while engine shutdown still runs.
    with pytest.raises(RuntimeError, match="poller failed"):
        await consumer.stop()

    assert dummy_engine.shutdown_called is True


@pytest.mark.asyncio
async def test_pyrallel_consumer_wait_closed_is_passive(monkeypatch: MonkeyPatch):
    # Given: dummy consumer dependencies are installed for wait_closed inspection.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(
        *,
        execution_engine,
        max_in_flight_messages,
        metrics_exporter=None,
        ordering_mode=None,
        max_revoke_grace_ms=None,
        poison_message_circuit=None,
        **_kwargs,
    ):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
            metrics_exporter=metrics_exporter,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=max_revoke_grace_ms,
        )
        return dummy_work_manager

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.WorkManager", _create_work_manager)
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)

    config = KafkaConfig()
    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    await consumer.wait_closed()

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    # When: PyrallelConsumer.wait_closed is invoked.
    # Then: the poller wait path runs without stopping the poller or shutting down the engine.
    assert dummy_poller.wait_closed_called is True
    assert dummy_poller.stopped is False
    assert dummy_engine.shutdown_called is False


def test_pyrallel_consumer_delegates_runtime_snapshot_to_poller(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a dummy poller exposes a runtime snapshot.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)

    consumer = PyrallelConsumer(
        config=KafkaConfig(), worker=lambda _: None, topic="demo"
    )

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    # When: PyrallelConsumer.get_runtime_snapshot is invoked.
    # Then: the consumer returns the poller runtime snapshot object.
    assert consumer.get_runtime_snapshot() is dummy_poller.runtime_snapshot


def test_pyrallel_consumer_delegates_pipeline_diagnostics_to_poller(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a dummy poller exposes pipeline diagnostics.
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_poller = None

    def _create_poller(*, consume_topic, kafka_config, execution_engine, work_manager):
        nonlocal dummy_poller
        dummy_poller = _DummyPoller(
            consume_topic=consume_topic,
            kafka_config=kafka_config,
            execution_engine=execution_engine,
            work_manager=work_manager,
        )
        return dummy_poller

    monkeypatch.setattr(
        "pyrallel_consumer.consumer.create_execution_engine", _create_engine
    )
    monkeypatch.setattr("pyrallel_consumer.consumer.BrokerPoller", _create_poller)

    consumer = PyrallelConsumer(
        config=KafkaConfig(), worker=lambda _: None, topic="demo"
    )

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    # When: PyrallelConsumer.get_pipeline_diagnostics is invoked.
    # Then: the consumer returns the poller pipeline diagnostics object.
    assert consumer.get_pipeline_diagnostics() is dummy_poller.pipeline_diagnostics


def test_pyrallel_consumer_pipeline_diagnostics_docstring_marks_stable_sidecar():
    # Given: the pipeline diagnostics accessor docstring is available.
    docstring = PyrallelConsumer.get_pipeline_diagnostics.__doc__

    # When: the docstring text is inspected for stability language.
    # Then: the accessor is documented as stable sidecar surface and not experimental or internal.
    assert docstring is not None
    assert "stable" in docstring.lower()
    assert "sidecar" in docstring.lower()
    assert "experimental" not in docstring.lower()
    assert "internal" not in docstring.lower()
