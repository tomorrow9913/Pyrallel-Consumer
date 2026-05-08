# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_revoke_commit.py
# Role: Verifies BrokerPoller revoke handling, metadata commits, and commit-offset safeguards.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_support import (
    AsyncMock,
    BrokerPoller,
    CompletionEvent,
    CompletionStatus,
    Consumer,
    DtoTopicPartition,
    KafkaException,
    KafkaTopicPartition,
    MagicMock,
    OffsetTracker,
    OrderingMode,
    RevokePreparation,
    _make_message,
    patch,
    pytest,
)


@pytest.mark.asyncio
async def test_on_revoke_removes_offset_trackers(broker_poller, mock_consumer):
    # Given: Inputs and test doubles are prepared for on revoke removes offset trackers.
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
    # When: The control-plane behavior is exercised for on revoke removes offset trackers.
    broker_poller._on_revoke(mock_consumer, tps_to_revoke)

    # Then: The expected on revoke removes offset trackers behavior is asserted.
    assert len(broker_poller._offset_trackers) == 0
    for ktp in tps_to_revoke:
        tp = DtoTopicPartition(topic=ktp.topic, partition=ktp.partition)
        assert tp not in broker_poller._offset_trackers


@pytest.mark.asyncio
async def test_on_revoke_clears_pending_dlq_events_for_revoked_partitions(
    broker_poller, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on revoke clears pending dlq events for revoked partitions.
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

    # When: The control-plane behavior is exercised for on revoke clears pending dlq events for revoked partitions.
    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    # Then: The expected on revoke clears pending dlq events for revoked partitions behavior is asserted.
    assert (revoked_tp, 10) not in broker_poller._pending_dlq_events
    assert broker_poller._pending_dlq_events == {(retained_tp, 10): retained_event}


@pytest.mark.asyncio
async def test_on_revoke_commits_metadata_snapshot_when_enabled(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on revoke commits metadata snapshot when enabled.
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

    # When: The control-plane behavior is exercised for on revoke commits metadata snapshot when enabled.
    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    # Then: The expected on revoke commits metadata snapshot when enabled behavior is asserted.
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata == broker_poller._metadata_encoder.encode_metadata(
        {6, 7}, 5
    )


@pytest.mark.asyncio
async def test_on_revoke_uses_coordinator_candidate_to_avoid_offset_rollback(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on revoke uses coordinator candidate to avoid offset rollback.
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
    tracker.last_fetched_offset = 12
    broker_poller._offset_trackers[tp] = tracker

    candidate = MagicMock()
    candidate.safe_offset = 12
    candidate.assignment_epoch = tracker.get_current_epoch()
    coordinator = MagicMock()
    coordinator.remaining_candidates.return_value = {tp: candidate}
    broker_poller._commit_coordinator = coordinator

    # When: The control-plane behavior is exercised for on revoke uses coordinator candidate to avoid offset rollback.
    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    # Then: The expected on revoke uses coordinator candidate to avoid offset rollback behavior is asserted.
    assert offsets_arg[0].offset == 13
    coordinator.stop_accepting_partitions.assert_called_once_with([tp])


@pytest.mark.asyncio
async def test_on_revoke_metadata_snapshot_limits_offsets_encoded(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on revoke metadata snapshot limits offsets encoded.
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
    # When: The control-plane behavior is exercised for on revoke metadata snapshot limits offsets encoded.
    decoded_offsets = broker_poller._metadata_encoder.decode_metadata(kafka_tp.metadata)
    # Then: The expected on revoke metadata snapshot limits offsets encoded behavior is asserted.
    assert len(decoded_offsets) <= broker_poller.MAX_COMPLETED_OFFSETS_FOR_METADATA
    assert all(offset >= 5 for offset in decoded_offsets)


@pytest.mark.asyncio
async def test_on_revoke_omits_metadata_snapshot_in_contiguous_only_mode(
    mock_kafka_config, mock_execution_engine, mock_consumer
):
    # Given: Inputs and test doubles are prepared for on revoke omits metadata snapshot in contiguous only mode.
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

    # When: The control-plane behavior is exercised for on revoke omits metadata snapshot in contiguous only mode.
    broker_poller._on_revoke(mock_consumer, [KafkaTopicPartition("test-topic", 0)])

    offsets_arg = mock_consumer.commit.call_args.kwargs["offsets"]
    # Then: The expected on revoke omits metadata snapshot in contiguous only mode behavior is asserted.
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert kafka_tp.offset == 5
    assert kafka_tp.metadata in (None, "")


@pytest.mark.asyncio
async def test_run_consumer_uses_non_blocking_consume_when_work_remains(
    broker_poller, mock_consumer
):
    # Given: Inputs and test doubles are prepared for run consumer uses non blocking consume when work remains.
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

    # When: The control-plane behavior is exercised for run consumer uses non blocking consume when work remains.
    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        await broker_poller._run_consumer()

    # Then: The expected run consumer uses non blocking consume when work remains behavior is asserted.
    assert consume_timeouts == [0.0]


@pytest.mark.asyncio
async def test_run_consumer_keeps_default_consume_timeout_when_idle(
    broker_poller, mock_consumer
):
    # Given: Inputs and test doubles are prepared for run consumer keeps default consume timeout when idle.
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

    # When: The control-plane behavior is exercised for run consumer keeps default consume timeout when idle.
    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread):
        await broker_poller._run_consumer()

    # Then: The expected run consumer keeps default consume timeout when idle behavior is asserted.
    assert consume_timeouts == [0.1]


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
        # Given: Inputs and test doubles are prepared for on revoke commit failure still processes remaining partitions.
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
        # When: The control-plane behavior is exercised for on revoke commit failure still processes remaining partitions.
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Both trackers must be removed regardless of commit failure
        # Then: The expected on revoke commit failure still processes remaining partitions behavior is asserted.
        assert len(broker_poller._offset_trackers) == 0
        # Partition 1 commit must have been attempted
        assert mock_consumer.commit.call_count == 2

    def test_on_revoke_commit_failure_still_deletes_tracker(
        self, broker_poller, mock_consumer
    ):
        """Tracker for the failed partition must be deleted even if commit throws."""
        # Given: Inputs and test doubles are prepared for on revoke commit failure still deletes tracker.
        self._setup_trackers(broker_poller)
        revoked_tps = {
            DtoTopicPartition(topic="test-topic", partition=0),
            DtoTopicPartition(topic="test-topic", partition=1),
        }
        broker_poller._dirty_commit_partitions.update(revoked_tps)

        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [
            KafkaTopicPartition("test-topic", 0),
            KafkaTopicPartition("test-topic", 1),
        ]
        # When: The control-plane behavior is exercised for on revoke commit failure still deletes tracker.
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Even though all commits fail, trackers must be cleaned up
        # Then: The expected on revoke commit failure still deletes tracker behavior is asserted.
        assert len(broker_poller._offset_trackers) == 0
        assert broker_poller._dirty_commit_partitions.isdisjoint(revoked_tps)

    def test_on_revoke_commit_failure_logs_warning(
        self, broker_poller, mock_consumer, caplog
    ):
        """Commit failure in _on_revoke must be logged with partition details."""
        # Given: Inputs and test doubles are prepared for on revoke commit failure logs warning.
        self._setup_trackers(broker_poller)

        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [KafkaTopicPartition("test-topic", 0)]
        # When: The control-plane behavior is exercised for on revoke commit failure logs warning.
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Must log the failure with identifiable info
        # Then: The expected on revoke commit failure logs warning behavior is asserted.
        assert any(
            "commit" in record.message.lower() and "test-topic" in record.message
            for record in caplog.records
        )

    def test_on_revoke_commit_failure_records_metric(
        self, broker_poller, mock_consumer
    ):
        """Commit failure in _on_revoke must increment the commit failure metric."""
        # Given: Inputs and test doubles are prepared for on revoke commit failure records metric.
        self._setup_trackers(broker_poller)
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        mock_consumer.commit.side_effect = KafkaException("Broker unavailable")

        tps_to_revoke = [KafkaTopicPartition("test-topic", 0)]
        # When: The control-plane behavior is exercised for on revoke commit failure records metric.
        broker_poller._on_revoke(mock_consumer, tps_to_revoke)

        # Then: The expected on revoke commit failure records metric behavior is asserted.
        exporter.record_commit_failure.assert_called_once_with(
            DtoTopicPartition(topic="test-topic", partition=0),
            "kafka_exception",
        )

    def test_on_revoke_prep_bridge_failure_records_rebalance_metric(
        self, broker_poller, mock_consumer
    ):
        # Given: Inputs and test doubles are prepared for on revoke prep bridge failure records rebalance metric.
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        broker_poller._event_loop = MagicMock()
        broker_poller._event_loop.is_closed.return_value = False
        # When: The control-plane behavior is exercised for on revoke prep bridge failure records rebalance metric.
        broker_poller._prepare_revoke_from_callback = MagicMock(return_value=None)

        # Then: The expected on revoke prep bridge failure records rebalance metric behavior is asserted.
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
        # Given: Inputs and test doubles are prepared for on revoke cleanup bridge failure records rebalance metric.
        exporter = MagicMock()
        broker_poller.set_metrics_exporter(exporter)
        revoked_tp = DtoTopicPartition(topic="test-topic", partition=0)
        broker_poller._prepare_revoke_from_callback = MagicMock(
            return_value=RevokePreparation(
                revoked_tps=[revoked_tp], offsets_to_commit=[]
            )
        )
        # When: The control-plane behavior is exercised for on revoke cleanup bridge failure records rebalance metric.
        broker_poller._cleanup_revoke_from_callback = MagicMock(return_value=False)

        # Then: The expected on revoke cleanup bridge failure records rebalance metric behavior is asserted.
        with pytest.raises(RuntimeError, match="Revoke bridge failed"):
            broker_poller._on_revoke(
                mock_consumer, [KafkaTopicPartition("test-topic", 0)]
            )

        exporter.record_commit_failure.assert_called_once_with(
            DtoTopicPartition(topic="test-topic", partition=0),
            "rebalance_bridge_failed",
        )


@pytest.mark.asyncio
async def test_commit_offsets_uses_topic_partition_with_metadata(broker_poller):
    # Given: Inputs and test doubles are prepared for commit offsets uses topic partition with metadata.
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

    # When: The control-plane behavior is exercised for commit offsets uses topic partition with metadata.
    await broker_poller._commit_offsets([(tp, 1)])

    _, kwargs = broker_poller.consumer.commit.call_args
    offsets_arg = kwargs["offsets"]
    # Then: The expected commit offsets uses topic partition with metadata behavior is asserted.
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert isinstance(kafka_tp, KafkaTopicPartition)

    assert kafka_tp.metadata == expected_metadata


@pytest.mark.asyncio
async def test_commit_offsets_uses_safe_offset_without_rescanning_tracker(
    broker_poller,
):
    # Given: Inputs and test doubles are prepared for commit offsets uses safe offset without rescanning tracker.
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

    # When: The control-plane behavior is exercised for commit offsets uses safe offset without rescanning tracker.
    await broker_poller._commit_offsets([(tp, 1)])

    # Then: The expected commit offsets uses safe offset without rescanning tracker behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for restored sparse offsets skip dispatch and commit after gap.
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

    # When: The control-plane behavior is exercised for restored sparse offsets skip dispatch and commit after gap.
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
    # Then: The expected restored sparse offsets skip dispatch and commit after gap behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for commit offsets records final commit failure for each partition.
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

    # When: The control-plane behavior is exercised for commit offsets records final commit failure for each partition.
    await broker_poller._commit_offsets([(tp, 0) for tp in tps])

    # Then: The expected commit offsets records final commit failure for each partition behavior is asserted.
    assert broker_poller.consumer.commit.call_count == 2
    assert exporter.commit_failures == [(tp, "kafka_exception") for tp in tps]
    for tracker in broker_poller._offset_trackers.values():
        assert tracker.last_committed_offset == -1
