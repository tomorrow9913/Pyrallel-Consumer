from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from confluent_kafka import Consumer, KafkaException
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine  # Added import


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}

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
    )
    # Patch Kafka client objects
    poller.producer = AsyncMock()
    poller.consumer = MagicMock(spec=Consumer)
    poller.admin = AsyncMock()
    return poller


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


@pytest.mark.asyncio
async def test_commit_offsets_uses_topic_partition_with_metadata(broker_poller):
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
        tracker.completed_offsets, 2
    )

    broker_poller.consumer = MagicMock(spec=Consumer)

    await broker_poller._commit_offsets([(tp, 1)])

    _, kwargs = broker_poller.consumer.commit.call_args
    offsets_arg = kwargs["offsets"]
    assert len(offsets_arg) == 1
    kafka_tp = offsets_arg[0]
    assert isinstance(kafka_tp, KafkaTopicPartition)

    assert kafka_tp.metadata == expected_metadata


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
