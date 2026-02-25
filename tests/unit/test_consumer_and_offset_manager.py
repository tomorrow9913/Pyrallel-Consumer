from types import SimpleNamespace

import pytest

from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.offset_manager import OffsetTracker


class _DummyEngine:
    def __init__(self):
        self.shutdown_called = False

    async def shutdown(self):
        self.shutdown_called = True


class _DummyWorkManager:
    def __init__(self, *, execution_engine, max_in_flight_messages):
        self.execution_engine = execution_engine
        self.max_in_flight_messages = max_in_flight_messages


class _DummyPoller:
    def __init__(self, *, consume_topic, kafka_config, execution_engine, work_manager):
        self.consume_topic = consume_topic
        self.kafka_config = kafka_config
        self.execution_engine = execution_engine
        self.work_manager = work_manager
        self.started = False
        self.stopped = False
        self.metrics = SimpleNamespace(source="dummy")

    async def start(self):
        self.started = True

    async def stop(self):
        self.stopped = True

    def get_metrics(self):
        return self.metrics


@pytest.mark.asyncio
async def test_pyrallel_consumer_starts_and_stops(monkeypatch):
    dummy_engine = _DummyEngine()

    def _create_engine(execution_config, worker):  # noqa: ARG001
        return dummy_engine

    dummy_work_manager = None

    def _create_work_manager(*, execution_engine, max_in_flight_messages):
        nonlocal dummy_work_manager
        dummy_work_manager = _DummyWorkManager(
            execution_engine=execution_engine,
            max_in_flight_messages=max_in_flight_messages,
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

    config = SimpleNamespace(parallel_consumer=None)

    consumer = PyrallelConsumer(config=config, worker=lambda _: None, topic="demo")

    assert consumer._poller is dummy_poller
    assert consumer._execution_engine is dummy_engine
    assert dummy_work_manager is not None
    assert (
        dummy_work_manager.max_in_flight_messages
        == consumer.config.parallel_consumer.execution.max_in_flight_messages
    )

    await consumer.start()
    await consumer.stop()

    assert dummy_poller.started is True
    assert dummy_poller.stopped is True
    assert dummy_engine.shutdown_called is True
    assert consumer.get_metrics().source == "dummy"


@pytest.mark.asyncio
async def test_offset_tracker_safe_offsets_and_counts():
    tracker = OffsetTracker()

    await tracker.add(0, 1)
    await tracker.add(0, 2)
    await tracker.add(1, 5)

    safe = await tracker.get_safe_offsets({0: 10, 1: 7})
    assert sorted(safe) == [(0, 1), (1, 5)]

    count_after_add = await tracker.get_total_in_flight_count()
    assert count_after_add == 3

    await tracker.remove(0, 1)
    await tracker.remove(0, 2)
    await tracker.remove(1, 5)

    safe_after_remove = await tracker.get_safe_offsets({0: 3, 1: 4})
    assert safe_after_remove == [(0, 4), (1, 5)]

    count_after_remove = await tracker.get_total_in_flight_count()
    assert count_after_remove == 0
