import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import Consumer, KafkaException
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import (
    BrokerPoller,
    RevokePreparation,
)
from pyrallel_consumer.control_plane.broker_rebalance_bridge import (
    BrokerRebalanceBridge,
)
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    ExecutionMode,
    OrderingMode,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine  # Added import


def _make_message(
    topic: str,
    partition: int,
    offset: int,
    key: bytes,
    value: bytes,
):
    message = MagicMock()
    message.topic.return_value = topic
    message.partition.return_value = partition
    message.offset.return_value = offset
    message.key.return_value = key
    message.value.return_value = value
    message.error.return_value = None
    return message


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    config.get_admin_config.return_value = {"bootstrap.servers": "broker:9092"}

    parallel_consumer_mock = MagicMock()
    parallel_consumer_mock.poll_batch_size = 1000
    parallel_consumer_mock.worker_pool_size = 8
    config.parallel_consumer = parallel_consumer_mock

    return config


@pytest.fixture
def mock_execution_engine():  # Added fixture
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def mock_consumer():
    consumer = MagicMock(spec=Consumer)
    consumer.assign.return_value = None
    consumer.unassign.return_value = None
    consumer.commit.return_value = None
    consumer.pause.return_value = None
    consumer.resume.return_value = None
    consumer.assignment.return_value = [
        KafkaTopicPartition("test-topic", 0),
        KafkaTopicPartition("test-topic", 1),
    ]
    return consumer


@pytest.fixture
def mock_admin_client():
    return MagicMock(spec=AdminClient)


@pytest.fixture
def mock_offset_tracker_factory():
    """Returns a mock factory for OffsetTracker that creates AsyncMock instances."""
    mock_factory = MagicMock()
    # Configure the factory to return new AsyncMock instances when called
    mock_factory.side_effect = (
        lambda tp, starting_offset, max_revoke_grace_ms, initial_completed_offsets: (
            AsyncMock(
                spec=OffsetTracker,
                topic_partition=tp,
                starting_offset=starting_offset,
                max_revoke_grace_ms=max_revoke_grace_ms,
            )
        )
    )
    return mock_factory


@pytest.fixture
def broker_poller(mock_kafka_config, mock_execution_engine):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    # Patch Kafka client objects
    poller.producer = AsyncMock()
    poller.consumer = MagicMock(spec=Consumer)
    poller.admin = AsyncMock()
    return poller


def test_broker_poller_uses_seventy_percent_resume_threshold(
    mock_kafka_config, mock_execution_engine
):
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 1000

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 700


def test_broker_poller_wires_resolved_process_route_batch_size_to_fallback_work_manager(
    mock_execution_engine,
):
    kafka_config = KafkaConfig(_env_file=None)
    kafka_config.parallel_consumer.execution.mode = ExecutionMode.PROCESS
    kafka_config.parallel_consumer.execution.process_config.route_batch_size = 13

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=13,
    )

    assert poller._work_manager.get_route_batch_size() == 13


def test_broker_poller_fallback_accepts_resolved_route_batch_primitive(
    mock_execution_engine,
):
    kafka_config = KafkaConfig(_env_file=None)

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=13,
    )

    assert poller._work_manager.get_route_batch_size() == 13


def test_broker_poller_fallback_requires_resolved_route_batch_primitive(
    mock_execution_engine,
):
    kafka_config = KafkaConfig(_env_file=None)

    with pytest.raises(ValueError, match="work_manager_route_batch_size"):
        BrokerPoller(
            consume_topic="test-topic",
            kafka_config=kafka_config,
            execution_engine=mock_execution_engine,
        )


def test_broker_poller_wires_async_route_batch_size_as_item_level(
    mock_execution_engine,
):
    kafka_config = KafkaConfig(_env_file=None)
    kafka_config.parallel_consumer.execution.mode = ExecutionMode.ASYNC
    kafka_config.parallel_consumer.execution.process_config.route_batch_size = 13

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    assert poller._work_manager.get_route_batch_size() == 1


@pytest.mark.asyncio
async def test_check_backpressure_updates_effective_inflight_limit_when_adaptive_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 100
    mock_kafka_config.parallel_consumer.adaptive_backpressure.enabled = True
    mock_kafka_config.parallel_consumer.adaptive_backpressure.min_in_flight = 40
    mock_kafka_config.parallel_consumer.adaptive_backpressure.scale_down_step = 20
    mock_kafka_config.parallel_consumer.adaptive_backpressure.cooldown_ms = 0
    mock_kafka_config.parallel_consumer.adaptive_backpressure.high_latency_threshold_ms = 50.0

    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 30
    work_manager.get_total_queued_messages.return_value = 0
    work_manager.get_virtual_queue_sizes.return_value = {}
    work_manager.get_average_completion_latency_seconds.return_value = 0.075
    work_manager.set_max_in_flight_messages.return_value = None

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=work_manager,
    )
    poller.consumer = mock_consumer

    await poller._check_backpressure()

    assert poller.MAX_IN_FLIGHT_MESSAGES == 80
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 56
    work_manager.set_max_in_flight_messages.assert_called_once_with(80)


@pytest.mark.asyncio
async def test_broker_poller_adapts_runtime_inflight_limit_when_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 128
    mock_kafka_config.parallel_consumer.adaptive_concurrency.enabled = True
    mock_kafka_config.parallel_consumer.adaptive_concurrency.min_in_flight = 32
    mock_kafka_config.parallel_consumer.adaptive_concurrency.scale_up_step = 16
    mock_kafka_config.parallel_consumer.adaptive_concurrency.scale_down_step = 24
    mock_kafka_config.parallel_consumer.adaptive_concurrency.cooldown_ms = 0

    mock_work_manager.get_total_in_flight_count.return_value = 64
    mock_work_manager.get_total_queued_messages.return_value = 0
    mock_work_manager.get_virtual_queue_sizes.return_value = {}
    mock_work_manager.is_rebalancing.return_value = False

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )
    poller.consumer = mock_consumer
    tracker = MagicMock()
    tracker.last_fetched_offset = 320
    tracker.last_committed_offset = 0
    poller._offset_trackers = {DtoTopicPartition("test-topic", 0): tracker}
    poller._set_runtime_max_in_flight(64, log_change=False)

    await poller._check_backpressure()

    assert poller.MAX_IN_FLIGHT_MESSAGES == 80
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 56
    assert mock_work_manager.set_max_in_flight_messages.call_args_list[-1].args == (80,)


@pytest.mark.asyncio
async def test_check_backpressure_skips_broker_calls_when_no_transition_possible(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 100
    mock_kafka_config.parallel_consumer.adaptive_backpressure.enabled = False
    mock_kafka_config.parallel_consumer.adaptive_concurrency.enabled = False

    work_manager = MagicMock()
    work_manager.get_total_in_flight_count.return_value = 10
    work_manager.get_total_queued_messages.return_value = 0
    work_manager.get_virtual_queue_sizes.return_value = {}

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=work_manager,
    )
    poller.consumer = mock_consumer
    poller.QUEUE_MAX_MESSAGES = 0

    await poller._check_backpressure()

    mock_consumer.assignment.assert_not_called()
    mock_consumer.pause.assert_not_called()
    mock_consumer.resume.assert_not_called()


def test_broker_poller_runtime_snapshot_exposes_adaptive_concurrency_when_enabled(
    mock_kafka_config, mock_execution_engine
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 128
    mock_kafka_config.parallel_consumer.adaptive_concurrency.enabled = True
    mock_kafka_config.parallel_consumer.adaptive_concurrency.min_in_flight = 32
    mock_kafka_config.parallel_consumer.adaptive_concurrency.scale_up_step = 16
    mock_kafka_config.parallel_consumer.adaptive_concurrency.scale_down_step = 24
    mock_kafka_config.parallel_consumer.adaptive_concurrency.cooldown_ms = 500

    mock_work_manager.get_total_in_flight_count.return_value = 12
    mock_work_manager.get_total_queued_messages.return_value = 3
    mock_work_manager.get_virtual_queue_sizes.return_value = {}
    mock_work_manager.get_in_flight_counts.return_value = {}
    mock_work_manager.is_rebalancing.return_value = False

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )
    poller._set_runtime_max_in_flight(80, log_change=False)

    snapshot = poller.get_runtime_snapshot()

    assert snapshot.adaptive_concurrency is not None
    assert snapshot.adaptive_concurrency.configured_max_in_flight == 128
    assert snapshot.adaptive_concurrency.effective_max_in_flight == 80
    assert snapshot.adaptive_concurrency.min_in_flight == 32
    assert snapshot.adaptive_concurrency.scale_up_step == 16
    assert snapshot.adaptive_concurrency.scale_down_step == 24
    assert snapshot.adaptive_concurrency.cooldown_ms == 500


def test_broker_poller_uses_configured_consumer_task_stop_timeout(
    mock_kafka_config, mock_execution_engine
):
    mock_kafka_config.parallel_consumer.execution.consumer_task_stop_timeout_ms = 1234

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    assert poller._consumer_task_stop_timeout_seconds == pytest.approx(1.234)


def test_broker_poller_syncs_ordering_mode_from_injected_work_manager(
    mock_kafka_config, mock_execution_engine
):
    mock_kafka_config.parallel_consumer.ordering_mode = "key_hash"
    injected_work_manager = MagicMock()
    injected_work_manager.get_ordering_mode.return_value = __import__(
        "pyrallel_consumer.dto", fromlist=["OrderingMode"]
    ).OrderingMode.PARTITION

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=injected_work_manager,
    )

    assert poller.ORDERING_MODE == injected_work_manager.get_ordering_mode.return_value


def test_get_partition_index_uses_worker_pool_size_for_key_hash_shards(
    mock_kafka_config, mock_execution_engine
):
    mock_kafka_config.parallel_consumer.worker_pool_size = 4
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    message = MagicMock()
    message.key.return_value = b"fixed-key"

    partition_index = poller._get_partition_index(message)

    assert 0 <= partition_index < 4


@pytest.mark.asyncio
async def test_on_assign_uses_configured_max_revoke_grace_ms(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.execution.max_revoke_grace_ms = 1234
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    tps_to_assign = [KafkaTopicPartition("test-topic", 0, 100)]
    broker_poller._on_assign(mock_consumer, tps_to_assign)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    assert broker_poller._offset_trackers[tp].max_revoke_grace_ms == 1234


@pytest.mark.asyncio
async def test_on_assign_initializes_offset_trackers(broker_poller, mock_consumer):
    tps_to_assign = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]
    broker_poller._on_assign(mock_consumer, tps_to_assign)

    assert len(broker_poller._offset_trackers) == 2
    for ktp in tps_to_assign:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        assert tp in broker_poller._offset_trackers
        tracker = broker_poller._offset_trackers[tp]
        assert tracker.topic_partition == tp
        assert tracker.epoch == 1  # Epoch should be incremented


@pytest.mark.asyncio
async def test_on_assign_passes_shared_trackers_to_work_manager(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    tps_to_assign = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]

    broker_poller._on_assign(mock_consumer, tps_to_assign)

    mock_work_manager.on_assign.assert_called_once()
    assignments = mock_work_manager.on_assign.call_args.args[0]
    expected_tp_0 = DtoTopicPartition(topic="test-topic", partition=0)
    expected_tp_1 = DtoTopicPartition(topic="test-topic", partition=1)

    assert set(assignments) == {expected_tp_0, expected_tp_1}
    assert assignments[expected_tp_0] is broker_poller._offset_trackers[expected_tp_0]
    assert assignments[expected_tp_1] is broker_poller._offset_trackers[expected_tp_1]
    assert assignments[expected_tp_0].get_current_epoch() == 1
    assert assignments[expected_tp_1].get_current_epoch() == 1
    assert broker_poller._offset_trackers[expected_tp_0].last_committed_offset == 99
    assert broker_poller._offset_trackers[expected_tp_1].last_committed_offset == 199


def test_on_assign_bridge_failure_raises_without_partial_state(
    broker_poller, mock_consumer
):
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    broker_poller._offset_trackers.clear()
    broker_poller._work_manager.on_assign = MagicMock()
    future = MagicMock()
    future.result.side_effect = TimeoutError("assign bridge timed out")

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        with pytest.raises(RuntimeError, match="Assign bridge failed"):
            broker_poller._on_assign(
                mock_consumer,
                [KafkaTopicPartition("test-topic", 0, 100)],
            )

    assert broker_poller._offset_trackers == {}
    broker_poller._work_manager.on_assign.assert_not_called()


def test_on_assign_bridge_timeout_covers_committed_lookup_budget(
    broker_poller, mock_consumer
):
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    broker_poller._kafka_config.parallel_consumer.execution.max_revoke_grace_ms = 500
    future = MagicMock()
    future.result.return_value = None

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        assert broker_poller._assign_from_callback(
            mock_consumer,
            [KafkaTopicPartition("test-topic", 0, 100)],
        )

    timeout = future.result.call_args.kwargs["timeout"]
    assert timeout >= broker_poller._rebalance_support.committed_lookup_timeout_seconds


def test_on_assign_committed_lookup_runs_off_event_loop(
    broker_poller, mock_consumer
) -> None:
    loop = asyncio.new_event_loop()
    loop_thread_id: list[int] = []
    loop_ready = threading.Event()

    def run_loop() -> None:
        loop_thread_id.append(threading.get_ident())
        loop_ready.set()
        loop.run_forever()

    def committed(
        partitions: list[KafkaTopicPartition], *, timeout: float
    ) -> list[KafkaTopicPartition]:
        del timeout
        assert threading.get_ident() != loop_thread_id[0]
        return [
            KafkaTopicPartition(tp.topic, tp.partition, tp.offset) for tp in partitions
        ]

    broker_poller._event_loop = loop
    mock_consumer.committed.side_effect = committed
    loop_thread = threading.Thread(target=run_loop)
    loop_thread.start()
    try:
        assert loop_ready.wait(timeout=1.0)
        assert broker_poller._assign_from_callback(
            mock_consumer,
            [KafkaTopicPartition("test-topic", 0, 100)],
        )
    finally:
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=1.0)
        loop.close()


def test_revoke_prep_bridge_timeout_cancels_scheduled_future(
    broker_poller,
):
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    future = MagicMock()
    future.result.side_effect = TimeoutError("revoke prep bridge timed out")

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        assert (
            broker_poller._prepare_revoke_from_callback(
                [KafkaTopicPartition("test-topic", 0)]
            )
            is None
        )

    future.cancel.assert_called_once_with()


def test_revoke_prep_bridge_drains_started_sync_work_after_timeout() -> None:
    loop = asyncio.new_event_loop()
    loop_thread = threading.Thread(target=loop.run_forever)
    started = threading.Event()
    release = threading.Event()
    returned = threading.Event()
    revoked_tp = DtoTopicPartition("test-topic", 0)
    preparation = RevokePreparation(
        revoked_tps=[revoked_tp],
        offsets_to_commit=[KafkaTopicPartition("test-topic", 0, 11)],
    )
    result: list[RevokePreparation | None] = []

    def prepare_revoke_sync(
        partitions: list[KafkaTopicPartition],
    ) -> RevokePreparation:
        assert partitions == [KafkaTopicPartition("test-topic", 0)]
        started.set()
        assert release.wait(timeout=1.0)
        return preparation

    bridge = BrokerRebalanceBridge(
        get_event_loop=lambda: loop,
        timeout_seconds=lambda: 0.01,
        assign_timeout_seconds=lambda: 0.01,
        control_lock=asyncio.Lock(),
        assign_sync=lambda assignments: None,
        prepare_revoke_sync=prepare_revoke_sync,
        cleanup_revoke_sync=lambda revoked_tps, failed_tps: None,
        logger=MagicMock(),
    )

    def invoke_bridge() -> None:
        result.append(
            bridge.prepare_revoke_from_callback([KafkaTopicPartition("test-topic", 0)])
        )
        returned.set()

    loop_thread.start()
    callback_thread = threading.Thread(target=invoke_bridge)
    callback_thread.start()
    try:
        assert started.wait(timeout=1.0)
        assert returned.wait(timeout=0.05) is False
        release.set()
        callback_thread.join(timeout=1.0)
        assert returned.is_set()
        assert result == [preparation]
    finally:
        release.set()
        callback_thread.join(timeout=1.0)
        loop.call_soon_threadsafe(loop.stop)
        loop_thread.join(timeout=1.0)
        loop.close()


@pytest.mark.asyncio
async def test_on_assign_hydrates_completed_offsets_from_metadata_snapshot(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    metadata = broker_poller._metadata_encoder.encode_metadata({103, 105}, 100)
    assigned = [
        KafkaTopicPartition("test-topic", 0, 100, metadata=metadata),
    ]

    broker_poller._on_assign(mock_consumer, assigned)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_uses_committed_partition_metadata_for_snapshot_hydration(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    assigned = [KafkaTopicPartition("test-topic", 0, 100)]
    committed_metadata = broker_poller._metadata_encoder.encode_metadata(
        {103, 105}, 100
    )
    mock_consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, 100, metadata=committed_metadata)
    ]

    broker_poller._on_assign(mock_consumer, assigned)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_falls_back_to_assignment_metadata_when_committed_metadata_empty(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    assignment_metadata = broker_poller._metadata_encoder.encode_metadata(
        {103, 105}, 100
    )
    assigned = [KafkaTopicPartition("test-topic", 0, 100, metadata=assignment_metadata)]
    mock_consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, 100, metadata="")
    ]

    broker_poller._on_assign(mock_consumer, assigned)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_ignores_metadata_snapshot_in_contiguous_only_mode(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_work_manager = MagicMock()
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "contiguous_only"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )

    metadata = broker_poller._metadata_encoder.encode_metadata({103, 105}, 100)
    assigned = [
        KafkaTopicPartition("test-topic", 0, 100, metadata=metadata),
    ]

    broker_poller._on_assign(mock_consumer, assigned)

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    assert set(tracker.completed_offsets) == set()
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 99


@pytest.mark.asyncio
async def test_on_revoke_removes_offset_trackers(broker_poller, mock_consumer):
    tps_assigned = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]
    # Manually set up some offset trackers for revocation
    for ktp in tps_assigned:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        tracker = AsyncMock(
            spec=OffsetTracker,
            topic_partition=tp,
            starting_offset=ktp.offset,
            max_revoke_grace_ms=0,
        )
        tracker.last_committed_offset = 50  # Simulate some progress
        tracker.in_flight_count = 5  # Simulate in-flight messages
        tracker.completed_offsets = {51, 52}  # Simulate some completed offsets
        tracker.advance_high_water_mark.return_value = None  # Mock this method
        broker_poller._offset_trackers[tp] = tracker

    tps_to_revoke = [
        KafkaTopicPartition("test-topic", 0),
        KafkaTopicPartition("test-topic", 1),
    ]
    broker_poller._on_revoke(mock_consumer, tps_to_revoke)

    assert len(broker_poller._offset_trackers) == 0
    for ktp in tps_to_revoke:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        assert tp not in broker_poller._offset_trackers
        # Verify advance_high_water_mark was called for each revoked tracker
        # Need to access the mock objects that were *in* the dictionary before deletion
        # This requires a bit more advanced mocking if we want to assert on calls to specific instances.
        # For simplicity, we assume the deletion implies the tracker was handled.
        # A more robust test might check mock_offset_tracker_factory calls or global mocks.


@pytest.mark.asyncio
async def test_on_revoke_clears_pending_dlq_events_for_revoked_partitions(
    broker_poller, mock_consumer
):
    revoked_tp = DtoTopicPartition(topic="test-topic", partition=0)
    retained_tp = DtoTopicPartition(topic="test-topic", partition=1)
    for tp in (revoked_tp, retained_tp):
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.last_committed_offset = -1
        tracker.last_fetched_offset = 10
        broker_poller._offset_trackers[tp] = tracker

    revoked_event = CompletionEvent(
        id="revoked-pending-dlq",
        tp=revoked_tp,
        offset=10,
        epoch=0,
        status=CompletionStatus.FAILURE,
        error="dlq unavailable",
        attempt=3,
    )
    retained_event = CompletionEvent(
        id="retained-pending-dlq",
        tp=retained_tp,
        offset=10,
        epoch=0,
        status=CompletionStatus.FAILURE,
        error="dlq unavailable",
        attempt=3,
    )
    broker_poller._pending_dlq_events[(revoked_tp, 10)] = revoked_event
    broker_poller._pending_dlq_events[(retained_tp, 10)] = retained_event

    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    assert (revoked_tp, 10) not in broker_poller._pending_dlq_events
    assert broker_poller._pending_dlq_events == {(retained_tp, 10): retained_event}


@pytest.mark.asyncio
async def test_on_revoke_commits_metadata_snapshot_when_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    broker_poller.consumer = mock_consumer

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 4
    tracker.last_fetched_offset = 7
    tracker.mark_complete(6)
    tracker.mark_complete(7)
    broker_poller._offset_trackers[tp] = tracker

    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata == broker_poller._metadata_encoder.encode_metadata(
        {6, 7}, 5
    )


@pytest.mark.asyncio
async def test_on_revoke_metadata_snapshot_limits_offsets_encoded(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    broker_poller.consumer = mock_consumer

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 4
    for offset in range(0, 5000):
        tracker.mark_complete(offset)
    broker_poller._offset_trackers[tp] = tracker

    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    kafka_tp = offsets_arg[0]
    decoded_offsets = broker_poller._metadata_encoder.decode_metadata(kafka_tp.metadata)
    assert len(decoded_offsets) <= broker_poller.MAX_COMPLETED_OFFSETS_FOR_METADATA
    assert all(offset >= 5 for offset in decoded_offsets)


@pytest.mark.asyncio
async def test_on_revoke_omits_metadata_snapshot_in_contiguous_only_mode(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    mock_kafka_config.parallel_consumer.rebalance_state_strategy = "contiguous_only"
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    broker_poller.consumer = mock_consumer

    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.last_committed_offset = 4
    tracker.last_fetched_offset = 7
    tracker.mark_complete(6)
    tracker.mark_complete(7)
    broker_poller._offset_trackers[tp] = tracker

    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata in (None, "")


@pytest.mark.asyncio
async def test_run_consumer_uses_non_blocking_consume_when_work_remains(
    broker_poller, mock_consumer
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    broker_poller._offset_trackers[tp] = tracker

    work_manager = MagicMock()
    work_manager.poll_completed_events = AsyncMock(return_value=[])
    work_manager.schedule = AsyncMock()
    work_manager.get_total_in_flight_count.return_value = 1
    work_manager.get_virtual_queue_sizes.return_value = {tp: {b"key": 1}}
    broker_poller._work_manager = work_manager
    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._max_blocking_duration_ms = 0
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 50
    broker_poller.QUEUE_MAX_MESSAGES = 0

    consume_timeouts = []

    def fake_consume(num_messages=1, timeout=0.1):
        consume_timeouts.append(timeout)
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        await broker_poller._run_consumer()

    assert consume_timeouts == [0.0]


@pytest.mark.asyncio
async def test_run_consumer_keeps_default_consume_timeout_when_idle(
    broker_poller, mock_consumer
):
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._max_blocking_duration_ms = 0
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 50
    broker_poller.QUEUE_MAX_MESSAGES = 0

    consume_timeouts = []

    def fake_consume(num_messages=1, timeout=0.1):
        consume_timeouts.append(timeout)
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        await broker_poller._run_consumer()

    assert consume_timeouts == [0.1]


# =====================================================================
# P0-②: Commit path exception defense tests
# =====================================================================


class TestOnRevokeCommitExceptionDefense:
    """_on_revoke must handle commit failures gracefully."""

    def _setup_trackers(self, broker_poller):
        """Set up two partition trackers with committable offsets."""
        trackers = {}
        for partition_id, offset in [(0, 50), (1, 80)]:
            tp = DtoTopicPartition(topic="test-topic", partition=partition_id)
            tracker = MagicMock(spec=OffsetTracker, topic_partition=tp)
            tracker.last_committed_offset = offset
            tracker.advance_high_water_mark.return_value = None
            broker_poller._offset_trackers[tp] = tracker
            trackers[partition_id] = tracker
        return trackers

    def test_on_revoke_commit_failure_still_processes_remaining_partitions(
        self, broker_poller, mock_consumer
    ):
        """When commit fails for partition 0, partition 1 must still be committed and cleaned up."""
        self._setup_trackers(broker_poller)

        # First commit (partition 0) raises KafkaException, second (partition 1) succeeds
        call_count = 0

        def commit_side_effect(offsets=None, asynchronous=False):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise KafkaException("Broker unavailable")
            return None

        mock_consumer.commit.side_effect = commit_side_effect

        tps_to_revoke = [
            KafkaTopicPartition("test-topic", 0),
            KafkaTopicPartition("test-topic", 1),
        ]
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Both trackers must be removed regardless of commit failure
        assert len(broker_poller._offset_trackers) == 0
        # Partition 1 commit must have been attempted
        assert mock_consumer.commit.call_count == 2

    def test_on_revoke_commit_failure_still_deletes_tracker(
        self, broker_poller, mock_consumer
    ):
        """Tracker for the failed partition must be deleted even if commit throws."""
        self._setup_trackers(broker_poller)

        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [
            KafkaTopicPartition("test-topic", 0),
            KafkaTopicPartition("test-topic", 1),
        ]
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Even though all commits fail, trackers must be cleaned up
        assert len(broker_poller._offset_trackers) == 0

    def test_on_revoke_commit_failure_logs_warning(
        self, broker_poller, mock_consumer, caplog
    ):
        """Commit failure in _on_revoke must be logged with partition details."""
        self._setup_trackers(broker_poller)

        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [KafkaTopicPartition("test-topic", 0)]
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Must log the failure with identifiable info
        assert any(
            "commit" in record.message.lower() and "test-topic" in record.message
            for record in caplog.records
        )

    def test_on_revoke_commit_failure_records_metric(
        self, broker_poller, mock_consumer
    ):
        """Commit failure in _on_revoke must increment the commit failure metric."""
        self._setup_trackers(broker_poller)
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [KafkaTopicPartition("test-topic", 0)]
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        exporter.record_commit_failure.assert_called_once_with(
            DtoTopicPartition(topic="test-topic", partition=0),
            "kafka_exception",
        )

    def test_on_revoke_prep_bridge_failure_records_rebalance_metric(
        self, broker_poller, mock_consumer
    ):
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        broker_poller._event_loop = MagicMock()
        broker_poller._event_loop.is_closed.return_value = False
        broker_poller._prepare_revoke_from_callback = MagicMock(return_value=None)

        with pytest.raises(RuntimeError, match="Revoke bridge failed"):
            broker_poller._on_revoke(
                mock_consumer, [KafkaTopicPartition("test-topic", 0)]
            )

        exporter.record_commit_failure.assert_called_once_with(
            DtoTopicPartition(topic="test-topic", partition=0),
            "rebalance_bridge_failed",
        )

    def test_on_revoke_cleanup_bridge_failure_records_rebalance_metric(
        self, broker_poller, mock_consumer
    ):
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        revoked_tp = DtoTopicPartition(topic="test-topic", partition=0)
        broker_poller._prepare_revoke_from_callback = MagicMock(
            return_value=RevokePreparation(
                revoked_tps=[revoked_tp], offsets_to_commit=[]
            )
        )
        broker_poller._cleanup_revoke_from_callback = MagicMock(return_value=False)

        broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

        exporter.record_commit_failure.assert_called_once_with(
            DtoTopicPartition(topic="test-topic", partition=0),
            "rebalance_bridge_failed",
        )


@pytest.mark.asyncio
async def test_commit_offsets_uses_topic_partition_with_metadata(broker_poller):
    broker_poller._kafka_config.parallel_consumer.rebalance_state_strategy = (
        "metadata_snapshot"
    )
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.mark_complete(0)
    tracker.mark_complete(1)
    broker_poller._offset_trackers[tp] = tracker

    expected_metadata = broker_poller._metadata_encoder.encode_metadata(  # type: ignore[attr-defined]
        {offset for offset in tracker.completed_offsets if offset >= 2}, 2
    )

    broker_poller.consumer = MagicMock(spec=Consumer)

    await broker_poller._commit_offsets([(tp, 1)])

    _, kwargs = broker_poller.consumer.commit.call_args
    offsets_arg = kwargs["offsets"]
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert isinstance(kafka_tp, KafkaTopicPartition)

    assert kafka_tp.metadata == expected_metadata


@pytest.mark.asyncio
async def test_commit_offsets_uses_safe_offset_without_rescanning_tracker(
    broker_poller,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.mark_complete(0)
    tracker.mark_complete(1)
    tracker.mark_complete(3)
    tracker.advance_high_water_mark = MagicMock(
        side_effect=AssertionError("advance_high_water_mark should not be called")
    )
    broker_poller._offset_trackers[tp] = tracker
    broker_poller.consumer = MagicMock(spec=Consumer)

    await broker_poller._commit_offsets([(tp, 1)])

    assert tracker.last_committed_offset == 1
    assert tracker.completed_offsets == {3}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ordering_mode",
    [OrderingMode.UNORDERED, OrderingMode.KEY_HASH, OrderingMode.PARTITION],
)
async def test_restored_sparse_offsets_skip_dispatch_and_commit_after_gap(
    broker_poller,
    ordering_mode: OrderingMode,
):
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = OffsetTracker(
        topic_partition=tp,
        starting_offset=4,
        max_revoke_grace_ms=0,
        initial_completed_offsets={4, 6, 7},
    )
    tracker.rehydrate_assignment_state(
        last_committed_offset=3,
        last_fetched_offset=7,
    )
    broker_poller._offset_trackers[tp] = tracker
    broker_poller.ORDERING_MODE = ordering_mode
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.submit_message = AsyncMock()
    broker_poller._work_manager.get_min_in_flight_offset.return_value = None

    await broker_poller._make_dispatch_support().dispatch_messages(
        [
            _make_message("test-topic", 0, 4, b"key-4", b"value-4"),
            _make_message("test-topic", 0, 5, b"key-5", b"value-5"),
            _make_message("test-topic", 0, 6, b"key-6", b"value-6"),
            _make_message("test-topic", 0, 7, b"key-7", b"value-7"),
        ]
    )

    submitted_offsets = [
        call.kwargs["offset"]
        for call in broker_poller._work_manager.submit_message.await_args_list
    ]
    assert submitted_offsets == [5]
    assert broker_poller.get_metrics().completed_offset_skips_total == 3
    assert tracker.last_committed_offset == 3
    assert tracker.last_fetched_offset == 7

    tracker.mark_complete(5)
    high_water_mark = tracker.get_committable_high_water_mark()
    assert high_water_mark == 7

    broker_poller.consumer = MagicMock(spec=Consumer)
    await broker_poller._commit_offsets([(tp, high_water_mark)])

    committed_offsets = broker_poller.consumer.commit.call_args.kwargs["offsets"]
    assert committed_offsets[0].offset == 8
    assert tracker.last_committed_offset == 7


@pytest.mark.asyncio
async def test_commit_offsets_records_final_commit_failure_for_each_partition(
    broker_poller,
):
    class _Exporter:
        def __init__(self) -> None:
            self.commit_failures: list[tuple[DtoTopicPartition, str]] = []

        def record_commit_failure(self, tp: DtoTopicPartition, reason: str) -> None:
            self.commit_failures.append((tp, reason))

    exporter = _Exporter()
    broker_poller.set_metrics_exporter(exporter)
    tps = [
        DtoTopicPartition(topic="test-topic", partition=0),
        DtoTopicPartition(topic="test-topic", partition=1),
    ]
    for tp in tps:
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.mark_complete(0)
        broker_poller._offset_trackers[tp] = tracker
    broker_poller.consumer = MagicMock(spec=Consumer)
    broker_poller.consumer.commit.side_effect = KafkaException("Broker unavailable")

    await broker_poller._commit_offsets([(tp, 0) for tp in tps])

    assert broker_poller.consumer.commit.call_count == 2
    assert exporter.commit_failures == [(tp, "kafka_exception") for tp in tps]
    for tracker in broker_poller._offset_trackers.values():
        assert tracker.last_committed_offset == -1


class TestRunConsumerCommitExceptionDefense:
    """_run_consumer commit failure must not kill the consumer loop."""

    @pytest.mark.asyncio
    async def test_commit_failure_does_not_kill_consumer_loop(self, broker_poller):
        """When commit raises KafkaException, the loop should continue, not terminate."""
        tp = DtoTopicPartition(topic="test-topic", partition=0)
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.last_committed_offset = -1
        tracker.last_fetched_offset = 2
        tracker.mark_complete(0)
        tracker.mark_complete(1)
        tracker.mark_complete(2)
        broker_poller._offset_trackers[tp] = tracker

        # Consumer.consume returns empty after first iteration to let commit path run
        iteration = 0

        def fake_consume(num_messages=1, timeout=0.1):
            nonlocal iteration
            iteration += 1
            if iteration >= 3:
                broker_poller._running = False
            return []

        broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

        # Commit raises on first call, succeeds on second
        commit_calls = 0

        def commit_side_effect(offsets=None, asynchronous=False):
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise KafkaException("Broker unavailable")
            return None

        broker_poller.consumer.commit = MagicMock(side_effect=commit_side_effect)

        # Patch asyncio.to_thread to call functions directly
        async def passthrough_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        broker_poller._running = True
        broker_poller.MAX_IN_FLIGHT_MESSAGES = 1000
        broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
        broker_poller.QUEUE_MAX_MESSAGES = 0
        broker_poller._max_blocking_duration_ms = 0
        broker_poller.producer = MagicMock()  # sync mock for _cleanup flush
        broker_poller._work_manager = MagicMock()
        broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
        broker_poller._work_manager.get_total_in_flight_count.return_value = 0
        broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
        broker_poller._work_manager.schedule = AsyncMock()
        with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
            await broker_poller._run_consumer()

        # Loop must have continued past the commit failure (iteration >= 3)
        assert (
            iteration >= 3
        ), f"Consumer loop died after {iteration} iterations — commit failure killed the loop"

    @pytest.mark.asyncio
    async def test_commit_failure_retries_once_then_succeeds(self, broker_poller):
        """Transient commit failure should be retried once, then succeed."""
        tp = DtoTopicPartition(topic="test-topic", partition=0)
        tracker = OffsetTracker(
            topic_partition=tp,
            starting_offset=0,
            max_revoke_grace_ms=0,
            initial_completed_offsets=set(),
        )
        tracker.last_committed_offset = -1
        tracker.last_fetched_offset = 0
        tracker.mark_complete(0)
        broker_poller._offset_trackers[tp] = tracker
        broker_poller._dirty_commit_partitions.add(tp)
        broker_poller._commit_debounce_completion_threshold = 1
        broker_poller._completions_since_last_commit = 1

        iteration = 0

        def fake_consume(num_messages=1, timeout=0.1):
            nonlocal iteration
            iteration += 1
            if iteration >= 2:
                broker_poller._running = False
            return []

        broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

        commit_calls = 0

        def commit_side_effect(offsets=None, asynchronous=False):
            nonlocal commit_calls
            commit_calls += 1
            if commit_calls == 1:
                raise KafkaException("Transient error")
            return None

        broker_poller.consumer.commit = MagicMock(side_effect=commit_side_effect)

        async def passthrough_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        broker_poller._running = True
        broker_poller.MAX_IN_FLIGHT_MESSAGES = 1000
        broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
        broker_poller.QUEUE_MAX_MESSAGES = 0
        broker_poller._max_blocking_duration_ms = 0
        broker_poller.producer = MagicMock()  # sync mock for _cleanup flush
        broker_poller._work_manager = MagicMock()
        broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
        broker_poller._work_manager.get_total_in_flight_count.return_value = 0
        broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
        broker_poller._work_manager.schedule = AsyncMock()
        with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
            await broker_poller._run_consumer()

        # commit should have been called twice (1 failure + 1 retry success)
        assert commit_calls == 2
        # After successful retry, advance_high_water_mark should have been called
        assert tracker.last_committed_offset == 0


@pytest.mark.asyncio
async def test_stop_reraises_terminal_consumer_loop_error(broker_poller, mock_consumer):
    broker_poller.consumer = mock_consumer
    broker_poller.producer = None
    broker_poller._running = True
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 70
    broker_poller.QUEUE_MAX_MESSAGES = 0
    mock_consumer.consume.side_effect = RuntimeError("boom")

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", new=passthrough_to_thread):
        broker_poller._consumer_task = asyncio.create_task(
            broker_poller._run_consumer()
        )
        await asyncio.sleep(0)

        with pytest.raises(RuntimeError, match="boom"):
            await broker_poller.stop()

    assert broker_poller._pipeline_poll_error_total == 1


@pytest.mark.asyncio
async def test_consumer_loop_downstream_exception_does_not_count_as_poll_error(
    broker_poller,
    mock_consumer,
):
    message = _make_message("test-topic", 0, 1, b"key", b"value")
    mock_consumer.consume.return_value = [message]
    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._running = True
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 70
    broker_poller.QUEUE_MAX_MESSAGES = 0
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    dispatch_support = MagicMock()
    dispatch_support.dispatch_messages = AsyncMock(
        side_effect=RuntimeError("dispatch boom")
    )
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    with patch("asyncio.to_thread", new=passthrough_to_thread):
        await broker_poller._run_consumer()

    assert isinstance(broker_poller._fatal_error, RuntimeError)
    assert broker_poller._pipeline_poll_records_total == 1
    assert broker_poller._pipeline_poll_error_total == 0


@pytest.mark.asyncio
async def test_start_skips_completion_monitor_when_disabled(
    mock_kafka_config, mock_execution_engine
):
    mock_kafka_config.parallel_consumer.strict_completion_monitor_enabled = False
    broker_poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    created_tasks = []

    def fake_create_task(coro, name=None):
        coro.close()
        task = MagicMock()
        task.get_name.return_value = name
        created_tasks.append((name, task))
        return task

    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=MagicMock(),
        ) as mock_producer,
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=MagicMock(),
        ) as mock_admin,
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=MagicMock(),
        ) as mock_consumer_ctor,
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    assert broker_poller._running is True
    assert broker_poller._completion_monitor_task is None
    assert broker_poller._consumer_task is created_tasks[0][1]
    assert created_tasks == [("broker-poller-loop", created_tasks[0][1])]
    mock_producer.assert_called_once()
    mock_admin.assert_called_once_with({"bootstrap.servers": "broker:9092"})
    mock_consumer_ctor.assert_called_once()


@pytest.mark.asyncio
async def test_stop_cancels_consumer_task_after_timeout(broker_poller):
    timed_out_task = MagicMock()
    timed_out_task.cancel = MagicMock()
    broker_poller._running = True
    broker_poller._consumer_task = timed_out_task
    broker_poller._shutdown_event.set()

    with (
        patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
        patch("asyncio.gather", new=AsyncMock()) as gather_mock,
    ):
        await broker_poller.stop()

    timed_out_task.cancel.assert_called_once_with()
    gather_mock.assert_awaited_once_with(timed_out_task, return_exceptions=True)
    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_wait_closed_reraises_terminal_error_when_shutdown_is_complete(
    broker_poller,
):
    broker_poller._running = False
    broker_poller._consumer_task = None
    broker_poller._shutdown_event.set()
    broker_poller._fatal_error = RuntimeError("closed-boom")

    with pytest.raises(RuntimeError, match="closed-boom"):
        await broker_poller.wait_closed()


@pytest.mark.asyncio
async def test_run_consumer_keeps_consumer_task_until_cleanup_finishes(
    broker_poller, mock_consumer
):
    cleanup_started = asyncio.Event()
    allow_cleanup_finish = asyncio.Event()

    async def fake_cleanup():
        cleanup_started.set()
        await allow_cleanup_finish.wait()

    def fake_consume(num_messages=1, timeout=0.1):
        broker_poller._running = False
        return []

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller.consumer = mock_consumer
    broker_poller.producer = MagicMock()
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._max_blocking_duration_ms = 0
    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)
    broker_poller._cleanup = AsyncMock(side_effect=fake_cleanup)
    broker_poller._running = True

    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        consumer_task = asyncio.create_task(broker_poller._run_consumer())
        broker_poller._consumer_task = consumer_task
        await cleanup_started.wait()
        assert broker_poller._consumer_task is consumer_task
        assert not broker_poller._shutdown_event.is_set()
        allow_cleanup_finish.set()
        await consumer_task

    assert broker_poller._shutdown_event.is_set()
    assert broker_poller._consumer_task is None
