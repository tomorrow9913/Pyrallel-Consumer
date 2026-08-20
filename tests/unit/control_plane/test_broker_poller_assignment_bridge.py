# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_assignment_bridge.py
# Role: Verifies BrokerPoller assignment bridge behavior and offset metadata hydration.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_support import (
    BrokerPoller,
    BrokerRebalanceBridge,
    DtoTopicPartition,
    KafkaTopicPartition,
    MagicMock,
    RevokePreparation,
    asyncio,
    patch,
    pytest,
    threading,
)


@pytest.mark.asyncio
async def test_on_assign_uses_configured_max_revoke_grace_ms(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on assign uses configured max revoke grace ms.
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

    # When: The control-plane behavior is exercised for on assign uses configured max revoke grace ms.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    # Then: The expected on assign uses configured max revoke grace ms behavior is asserted.
    assert broker_poller._offset_trackers[tp].max_revoke_grace_ms == 1234


@pytest.mark.asyncio
async def test_on_assign_initializes_offset_trackers(broker_poller, mock_consumer):
    # Given: Inputs and test doubles are prepared for on assign initializes offset trackers.
    tps_to_assign = [
        KafkaTopicPartition("test-topic", 0, 100),
        KafkaTopicPartition("test-topic", 1, 200),
    ]
    # When: The control-plane behavior is exercised for on assign initializes offset trackers.
    broker_poller._on_assign(mock_consumer, tps_to_assign)

    # Then: The expected on assign initializes offset trackers behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for on assign passes shared trackers to work manager.
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
    # When: The control-plane behavior is exercised for on assign passes shared trackers to work manager.
    expected_tp_1 = DtoTopicPartition(topic="test-topic", partition=1)

    # Then: The expected on assign passes shared trackers to work manager behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for on assign bridge failure raises without partial state.
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    broker_poller._offset_trackers.clear()
    broker_poller._work_manager.on_assign = MagicMock()
    future = MagicMock()
    # When: The control-plane behavior is exercised for on assign bridge failure raises without partial state.
    future.result.side_effect = TimeoutError("assign bridge timed out")

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        # Then: The expected on assign bridge failure raises without partial state behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for on assign bridge timeout covers committed lookup budget.
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    broker_poller._kafka_config.parallel_consumer.execution.max_revoke_grace_ms = 500
    # When: The control-plane behavior is exercised for on assign bridge timeout covers committed lookup budget.
    future = MagicMock()
    future.result.return_value = None

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        # Then: The expected on assign bridge timeout covers committed lookup budget behavior is asserted.
        assert broker_poller._assign_from_callback(
            mock_consumer,
            [KafkaTopicPartition("test-topic", 0, 100)],
        )

    timeout = future.result.call_args.kwargs["timeout"]
    assert timeout >= broker_poller._rebalance_support.committed_lookup_timeout_seconds


def test_on_assign_committed_lookup_runs_off_event_loop(
    broker_poller, mock_consumer
) -> None:
    # Given: Inputs and test doubles are prepared for on assign committed lookup runs off event loop.
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
    # When: The control-plane behavior is exercised for on assign committed lookup runs off event loop.
    try:
        # Then: The expected on assign committed lookup runs off event loop behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for revoke prep bridge timeout cancels scheduled future.
    broker_poller._event_loop = MagicMock()
    broker_poller._event_loop.is_closed.return_value = False
    future = MagicMock()
    # When: The control-plane behavior is exercised for revoke prep bridge timeout cancels scheduled future.
    future.result.side_effect = TimeoutError("revoke prep bridge timed out")

    def capture_future(coroutine, loop):
        del loop
        coroutine.close()
        return future

    with patch("asyncio.run_coroutine_threadsafe", side_effect=capture_future):
        # Then: The expected revoke prep bridge timeout cancels scheduled future behavior is asserted.
        assert (
            broker_poller._prepare_revoke_from_callback(
                [KafkaTopicPartition("test-topic", 0)]
            )
            is None
        )

    future.cancel.assert_called_once_with()


def test_revoke_prep_bridge_drains_started_sync_work_after_timeout() -> None:
    # Given: Inputs and test doubles are prepared for revoke prep bridge drains started sync work after timeout.
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
    # When: The control-plane behavior is exercised for revoke prep bridge drains started sync work after timeout.
    try:
        # Then: The expected revoke prep bridge drains started sync work after timeout behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for on assign hydrates completed offsets from metadata snapshot.
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

    # When: The control-plane behavior is exercised for on assign hydrates completed offsets from metadata snapshot.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    # Then: The expected on assign hydrates completed offsets from metadata snapshot behavior is asserted.
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_uses_committed_partition_metadata_for_snapshot_hydration(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on assign uses committed partition metadata for snapshot hydration.
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

    # When: The control-plane behavior is exercised for on assign uses committed partition metadata for snapshot hydration.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    # Then: The expected on assign uses committed partition metadata for snapshot hydration behavior is asserted.
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_falls_back_to_assignment_metadata_when_committed_metadata_empty(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on assign falls back to assignment metadata when committed metadata empty.
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

    # When: The control-plane behavior is exercised for on assign falls back to assignment metadata when committed metadata empty.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    # Then: The expected on assign falls back to assignment metadata when committed metadata empty behavior is asserted.
    assert set(tracker.completed_offsets) == {103, 105}
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 105


@pytest.mark.asyncio
async def test_on_assign_ignores_metadata_snapshot_in_contiguous_only_mode(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on assign ignores metadata snapshot in contiguous only mode.
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

    # When: The control-plane behavior is exercised for on assign ignores metadata snapshot in contiguous only mode.
    tp = DtoTopicPartition(topic="test-topic", partition=0)
    tracker = broker_poller._offset_trackers[tp]
    # Then: The expected on assign ignores metadata snapshot in contiguous only mode behavior is asserted.
    assert set(tracker.completed_offsets) == set()
    assert tracker.last_committed_offset == 99
    assert tracker.last_fetched_offset == 99
