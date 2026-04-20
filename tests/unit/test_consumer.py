from types import SimpleNamespace
from typing import cast

import pytest
from _pytest.monkeypatch import MonkeyPatch

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import (
    OrderingMode,
    ResourceSignalSnapshot,
    ResourceSignalStatus,
)


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
        self.completion_updates = []
        self.closed = False
        _DummyPrometheusExporter.instances.append(self)

    def update_from_system_metrics(self, metrics) -> None:
        self.system_metrics_updates.append(metrics)

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
    ):
        self.execution_engine = execution_engine
        self.max_in_flight_messages = max_in_flight_messages
        self.metrics_exporter = metrics_exporter
        self.ordering_mode = ordering_mode
        self.max_revoke_grace_ms = max_revoke_grace_ms
        self.poison_message_circuit = poison_message_circuit

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

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    async def wait_closed(self):
        self.wait_closed_called = True

    def get_metrics(self):
        return self.metrics


class _DummyResourceSignalProvider:
    def snapshot(self) -> ResourceSignalSnapshot:
        return ResourceSignalSnapshot(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.25,
            memory_utilization=0.5,
        )


class _FailingStopPoller(_DummyPoller):
    async def stop(self):
        self.stopped = True
        raise RuntimeError("poller failed")


class _FailingStartPoller(_DummyPoller):
    async def start(self):
        raise RuntimeError("poller start failed")


@pytest.mark.asyncio
async def test_pyrallel_consumer_starts_and_stops(monkeypatch: MonkeyPatch):
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

    config = cast(KafkaConfig, cast(object, SimpleNamespace(parallel_consumer=None)))

    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    assert consumer._poller is dummy_poller
    assert consumer._execution_engine is dummy_engine
    assert dummy_work_manager is not None
    assert (
        dummy_work_manager.max_in_flight_messages
        == consumer.config.parallel_consumer.execution.max_in_flight_messages
    )
    assert dummy_work_manager.ordering_mode == OrderingMode.KEY_HASH
    assert (
        dummy_work_manager.max_revoke_grace_ms
        == consumer.config.parallel_consumer.execution.max_revoke_grace_ms
    )
    assert dummy_work_manager.poison_message_circuit is not None
    assert dummy_work_manager.poison_message_circuit.enabled is False
    assert dummy_work_manager.metrics_exporter is None

    await consumer.start()
    await consumer.stop()

    dummy_poller = cast(_DummyPoller, cast(object, dummy_poller))
    assert dummy_poller.started is True
    assert dummy_poller.stopped is True
    assert dummy_engine.shutdown_called is True
    assert dummy_poller.metrics.source == "dummy"


def test_pyrallel_consumer_wires_poison_message_circuit(
    monkeypatch: MonkeyPatch,
) -> None:
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

    config = KafkaConfig(_env_file=None)
    config.parallel_consumer.poison_message.enabled = True
    config.parallel_consumer.poison_message.failure_threshold = 2
    config.parallel_consumer.poison_message.cooldown_ms = 7500
    config.parallel_consumer.execution.max_retries = 4

    PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    assert dummy_work_manager is not None
    circuit = dummy_work_manager.poison_message_circuit
    assert circuit is not None
    assert circuit.enabled is True
    assert circuit.failure_threshold == 2
    assert circuit.cooldown_ms == 7500


@pytest.mark.asyncio
async def test_pyrallel_consumer_auto_wires_metrics_exporter_when_enabled(
    monkeypatch: MonkeyPatch,
):
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

    assert dummy_work_manager is not None
    assert dummy_work_manager.metrics_exporter is None

    await consumer.start()
    exporter = cast(_DummyPrometheusExporter, consumer._metrics_exporter)
    assert exporter.config.port == 9911
    assert dummy_work_manager.metrics_exporter is exporter
    await consumer.stop()

    assert exporter.system_metrics_updates == [
        dummy_poller.metrics,
        dummy_poller.metrics,
    ]
    assert exporter.closed is True
    assert dummy_engine.shutdown_called is True
    assert dummy_work_manager.metrics_exporter is None
    assert consumer._metrics_exporter is None
    assert consumer._metrics_task is None


@pytest.mark.asyncio
async def test_pyrallel_consumer_publishes_resource_signal_snapshot(
    monkeypatch: MonkeyPatch,
):
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
    assert exporter.system_metrics_updates[0].resource_signal is not None
    assert (
        exporter.system_metrics_updates[0].resource_signal.status
        == ResourceSignalStatus.AVAILABLE
    )
    assert exporter.system_metrics_updates[0].resource_signal.cpu_utilization == 0.25


@pytest.mark.asyncio
async def test_pyrallel_consumer_creates_exporter_on_start_not_init(
    monkeypatch: MonkeyPatch,
):
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

    assert dummy_work_manager is not None
    assert dummy_work_manager.ordering_mode == OrderingMode.PARTITION


@pytest.mark.asyncio
async def test_pyrallel_consumer_stop_still_shuts_down_engine_on_poller_failure(
    monkeypatch: MonkeyPatch,
):
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

    with pytest.raises(RuntimeError, match="poller failed"):
        await consumer.stop()

    assert dummy_engine.shutdown_called is True


@pytest.mark.asyncio
async def test_pyrallel_consumer_wait_closed_is_passive(monkeypatch: MonkeyPatch):
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
    assert dummy_poller.wait_closed_called is True
    assert dummy_poller.stopped is False
    assert dummy_engine.shutdown_called is False
