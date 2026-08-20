# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_config_backpressure.py
# Role: Verifies BrokerPoller configuration wiring, route-batch defaults, backpressure, and runtime snapshots.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_support import (
    BrokerPoller,
    DtoTopicPartition,
    ExecutionMode,
    KafkaConfig,
    MagicMock,
    pytest,
)


def test_broker_poller_uses_seventy_percent_resume_threshold(
    mock_kafka_config, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for broker poller uses seventy percent resume threshold.
    mock_kafka_config.parallel_consumer.execution.max_in_flight = 1000

    # When: The control-plane behavior is exercised for broker poller uses seventy percent resume threshold.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    # Then: The expected broker poller uses seventy percent resume threshold behavior is asserted.
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 700


def test_broker_poller_wires_resolved_process_route_batch_size_to_fallback_work_manager(
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for broker poller wires resolved process route batch size to fallback work manager.
    kafka_config = KafkaConfig(_env_file=None)
    kafka_config.parallel_consumer.execution.mode = ExecutionMode.PROCESS
    kafka_config.parallel_consumer.execution.process_config.route_batch_size = 13

    # When: The control-plane behavior is exercised for broker poller wires resolved process route batch size to fallback work manager.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=13,
    )

    # Then: The expected broker poller wires resolved process route batch size to fallback work manager behavior is asserted.
    assert poller._work_manager.get_route_batch_size() == 13


def test_broker_poller_fallback_accepts_resolved_route_batch_primitive(
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for broker poller fallback accepts resolved route batch primitive.
    kafka_config = KafkaConfig(_env_file=None)

    # When: The control-plane behavior is exercised for broker poller fallback accepts resolved route batch primitive.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=13,
    )

    # Then: The expected broker poller fallback accepts resolved route batch primitive behavior is asserted.
    assert poller._work_manager.get_route_batch_size() == 13


def test_broker_poller_fallback_requires_resolved_route_batch_primitive(
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for broker poller fallback requires resolved route batch primitive.
    # When: The control-plane behavior is exercised for broker poller fallback requires resolved route batch primitive.
    kafka_config = KafkaConfig(_env_file=None)

    # Then: The expected broker poller fallback requires resolved route batch primitive behavior is asserted.
    with pytest.raises(ValueError, match="work_manager_route_batch_size"):
        BrokerPoller(
            consume_topic="test-topic",
            kafka_config=kafka_config,
            execution_engine=mock_execution_engine,
        )


def test_broker_poller_wires_async_route_batch_size_as_item_level(
    mock_execution_engine,
):
    # Given: Inputs and test doubles are prepared for broker poller wires async route batch size as item level.
    kafka_config = KafkaConfig(_env_file=None)
    kafka_config.parallel_consumer.execution.mode = ExecutionMode.ASYNC
    kafka_config.parallel_consumer.execution.process_config.route_batch_size = 13

    # When: The control-plane behavior is exercised for broker poller wires async route batch size as item level.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    # Then: The expected broker poller wires async route batch size as item level behavior is asserted.
    assert poller._work_manager.get_route_batch_size() == 1


@pytest.mark.asyncio
async def test_check_backpressure_updates_effective_inflight_limit_when_adaptive_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for check backpressure updates effective inflight limit when adaptive enabled.
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

    # When: The control-plane behavior is exercised for check backpressure updates effective inflight limit when adaptive enabled.
    await poller._check_backpressure()

    # Then: The expected check backpressure updates effective inflight limit when adaptive enabled behavior is asserted.
    assert poller.MAX_IN_FLIGHT_MESSAGES == 80
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 56
    work_manager.set_max_in_flight_messages.assert_called_once_with(80)


@pytest.mark.asyncio
async def test_broker_poller_adapts_runtime_inflight_limit_when_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for broker poller adapts runtime inflight limit when enabled.
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

    # When: The control-plane behavior is exercised for broker poller adapts runtime inflight limit when enabled.
    await poller._check_backpressure()

    # Then: The expected broker poller adapts runtime inflight limit when enabled behavior is asserted.
    assert poller.MAX_IN_FLIGHT_MESSAGES == 80
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == 56
    assert mock_work_manager.set_max_in_flight_messages.call_args_list[-1].args == (80,)


@pytest.mark.asyncio
async def test_check_backpressure_skips_broker_calls_when_no_transition_possible(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for check backpressure skips broker calls when no transition possible.
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
    # When: The control-plane behavior is exercised for check backpressure skips broker calls when no transition possible.
    mock_consumer.pause.assert_not_called()
    # Then: The expected check backpressure skips broker calls when no transition possible behavior is asserted.
    mock_consumer.resume.assert_not_called()


def test_broker_poller_runtime_snapshot_exposes_adaptive_concurrency_when_enabled(
    mock_kafka_config, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for broker poller runtime snapshot exposes adaptive concurrency when enabled.
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

    # When: The control-plane behavior is exercised for broker poller runtime snapshot exposes adaptive concurrency when enabled.
    snapshot = poller.get_runtime_snapshot()

    # Then: The expected broker poller runtime snapshot exposes adaptive concurrency when enabled behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for broker poller uses configured consumer task stop timeout.
    mock_kafka_config.parallel_consumer.execution.consumer_task_stop_timeout_ms = 1234

    # When: The control-plane behavior is exercised for broker poller uses configured consumer task stop timeout.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )

    # Then: The expected broker poller uses configured consumer task stop timeout behavior is asserted.
    assert poller._consumer_task_stop_timeout_seconds == pytest.approx(1.234)


def test_broker_poller_syncs_ordering_mode_from_injected_work_manager(
    mock_kafka_config, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for broker poller syncs ordering mode from injected work manager.
    mock_kafka_config.parallel_consumer.ordering_mode = "key_hash"
    injected_work_manager = MagicMock()
    injected_work_manager.get_ordering_mode.return_value = __import__(
        "pyrallel_consumer.dto", fromlist=["OrderingMode"]
    ).OrderingMode.PARTITION

    # When: The control-plane behavior is exercised for broker poller syncs ordering mode from injected work manager.
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=injected_work_manager,
    )

    # Then: The expected broker poller syncs ordering mode from injected work manager behavior is asserted.
    assert poller.ORDERING_MODE == injected_work_manager.get_ordering_mode.return_value


def test_get_partition_index_uses_worker_pool_size_for_key_hash_shards(
    mock_kafka_config, mock_execution_engine
):
    # Given: Inputs and test doubles are prepared for get partition index uses worker pool size for key hash shards.
    mock_kafka_config.parallel_consumer.worker_pool_size = 4
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager_route_batch_size=1,
    )
    message = MagicMock()
    message.key.return_value = b"fixed-key"

    # When: The control-plane behavior is exercised for get partition index uses worker pool size for key hash shards.
    partition_index = poller._get_partition_index(message)

    # Then: The expected get partition index uses worker pool size for key hash shards behavior is asserted.
    assert 0 <= partition_index < 4
