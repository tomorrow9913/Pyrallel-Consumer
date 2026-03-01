import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from confluent_kafka import OFFSET_INVALID, Consumer, Message
from confluent_kafka import TopicPartition as KafkaTopicPartition
from sortedcontainers import SortedSet

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    config.dlq_enabled = True
    config.DLQ_TOPIC_SUFFIX = ".dlq"
    config.DLQ_FLUSH_TIMEOUT_MS = 5000

    parallel_consumer_mock = MagicMock()
    parallel_consumer_mock.poll_batch_size = 1000
    parallel_consumer_mock.worker_pool_size = 8

    execution_mock = MagicMock()
    execution_mock.max_in_flight = 100
    execution_mock.max_retries = 3
    execution_mock.retry_backoff_ms = 100
    execution_mock.exponential_backoff = True
    execution_mock.max_retry_backoff_ms = 1000
    execution_mock.retry_jitter_ms = 10
    parallel_consumer_mock.execution = execution_mock

    config.parallel_consumer = parallel_consumer_mock
    return config


@pytest.fixture
def mock_execution_engine():
    engine = AsyncMock(spec=BaseExecutionEngine)
    engine.poll_completed_events.return_value = []
    return engine


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
    ]
    consumer.committed.return_value = [
        KafkaTopicPartition("test-topic", 0, OFFSET_INVALID)
    ]
    return consumer


@pytest.fixture
def mock_work_manager():
    """A more sophisticated mock for WorkManager that uses an asyncio.Queue."""
    wm = AsyncMock(spec=WorkManager)

    completion_queue = asyncio.Queue()
    wm.submitted_messages = []

    async def submit_message_side_effect(tp, offset, epoch, key, payload):
        work_item_id = str(uuid.uuid4())
        work_item = WorkItem(
            id=work_item_id, tp=tp, offset=offset, epoch=epoch, key=key, payload=payload
        )
        wm.submitted_messages.append(work_item)

    async def poll_completed_events_side_effect():
        if completion_queue.empty():
            return []

        events = []
        while not completion_queue.empty():
            events.append(completion_queue.get_nowait())
        return events

    def _push_completion_event(event):
        completion_queue.put_nowait(event)

    wm.submit_message.side_effect = submit_message_side_effect
    wm.poll_completed_events.side_effect = poll_completed_events_side_effect
    wm._push_completion_event = _push_completion_event

    wm.schedule.return_value = None
    wm.get_total_in_flight_count.return_value = 0
    wm.get_virtual_queue_sizes.return_value = {}

    return wm


@pytest.fixture
def mock_offset_tracker_class(mocker):
    """
    Returns a MagicMock instance that simulates the OffsetTracker's stateful behavior.
    """
    tracker_mock = MagicMock(spec=OffsetTracker)
    tracker_mock.return_value = tracker_mock

    # Initialize internal state
    tracker_mock.last_committed_offset = -1  # Initial state after _on_assign
    tracker_mock.completed_offsets = SortedSet()
    tracker_mock.epoch = 1  # Initial epoch after _on_assign increments it

    def mark_complete_side_effect(offset):
        tracker_mock.completed_offsets.add(offset)

    def advance_hwm_side_effect():
        new_hwm = tracker_mock.last_committed_offset
        for offset in tracker_mock.completed_offsets:
            if offset == new_hwm + 1:
                new_hwm = offset
            else:
                break
        if new_hwm > tracker_mock.last_committed_offset:
            tracker_mock.last_committed_offset = new_hwm
            tracker_mock.completed_offsets = SortedSet(
                o for o in tracker_mock.completed_offsets if o > new_hwm
            )

    tracker_mock.increment_epoch.side_effect = lambda: setattr(
        tracker_mock, "epoch", tracker_mock.epoch + 1
    )
    tracker_mock.get_current_epoch.side_effect = lambda: tracker_mock.epoch
    tracker_mock.mark_complete.side_effect = mark_complete_side_effect
    tracker_mock.advance_high_water_mark.side_effect = advance_hwm_side_effect
    tracker_mock.update_last_fetched_offset.return_value = None  # This is a void method

    return tracker_mock


@pytest.fixture
def broker_poller(
    mock_kafka_config,
    mock_execution_engine,
    mock_consumer,
    mock_work_manager,
    mock_offset_tracker_class,
    mocker,
):
    mocker.patch(
        "pyrallel_consumer.control_plane.broker_poller.OffsetTracker",
        new=mock_offset_tracker_class,
    )

    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
        work_manager=mock_work_manager,
    )
    poller.producer = AsyncMock()
    poller.consumer = mock_consumer
    poller.admin = AsyncMock()
    return poller


@pytest.fixture
def setup_assigned_partitions(broker_poller, mock_consumer):
    """Assigns partitions to the broker_poller before each test."""
    test_tp_kafka = KafkaTopicPartition("test-topic", 0, 0)

    broker_poller._on_assign(mock_consumer, [test_tp_kafka])

    test_tp_dto = DtoTopicPartition(test_tp_kafka.topic, test_tp_kafka.partition)
    mock_tracker_from_poller = broker_poller._offset_trackers[test_tp_dto]

    return test_tp_kafka, mock_tracker_from_poller


@pytest.mark.asyncio
async def test_run_consumer_loop_basic_flow(
    broker_poller,
    mock_consumer,
    mock_execution_engine,
    setup_assigned_partitions,
    mock_work_manager,
):
    test_tp_kafka, mock_offset_tracker_instance = setup_assigned_partitions
    DtoTopicPartition(test_tp_kafka.topic, test_tp_kafka.partition)

    messages_to_consume = []
    for i in range(3):
        msg = MagicMock(spec=Message)
        msg.topic.return_value = test_tp_kafka.topic
        msg.partition.return_value = test_tp_kafka.partition
        msg.offset.return_value = i
        msg.key.return_value = f"key-{i}".encode()
        msg.value.return_value = f"value-{i}".encode()
        msg.error.return_value = None
        messages_to_consume.append(msg)

    mock_consumer_messages_remaining = messages_to_consume.copy()

    def custom_consume_side_effect(num_messages, timeout):
        if mock_consumer_messages_remaining:
            batch_to_return = mock_consumer_messages_remaining[:]
            mock_consumer_messages_remaining.clear()
            return batch_to_return
        return []

    mock_consumer.consume.side_effect = custom_consume_side_effect

    broker_poller._running = True
    consumer_task = asyncio.create_task(broker_poller._run_consumer())

    timeout_seconds = 2
    start_time = asyncio.get_event_loop().time()
    while len(mock_work_manager.submitted_messages) < 3:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for messages to be submitted.")
        await asyncio.sleep(0.01)

    for item in mock_work_manager.submitted_messages:
        completion_event = CompletionEvent(
            id=item.id,
            tp=item.tp,
            offset=item.offset,
            epoch=item.epoch,
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
        mock_work_manager._push_completion_event(completion_event)

    start_time = asyncio.get_event_loop().time()
    while mock_offset_tracker_instance.mark_complete.call_count < 3:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError(
                f"Timeout waiting for messages to be marked complete. "
                f"Actual count: {mock_offset_tracker_instance.mark_complete.call_count}"
            )
        await asyncio.sleep(0.01)

    start_time = asyncio.get_event_loop().time()
    while mock_consumer.commit.call_count == 0:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for consumer to commit.")
        await asyncio.sleep(0.01)

    broker_poller._running = False
    await consumer_task

    assert mock_consumer.consume.call_count >= 1
    assert mock_work_manager.submit_message.call_count == 3
    assert len(mock_work_manager.submitted_messages) == 3

    submitted_item = mock_work_manager.submitted_messages[0]
    original_msg = messages_to_consume[0]
    assert submitted_item.tp == DtoTopicPartition(
        original_msg.topic(), original_msg.partition()
    )
    assert submitted_item.offset == original_msg.offset()
    assert submitted_item.epoch == mock_offset_tracker_instance.get_current_epoch()

    assert mock_work_manager.schedule.call_count >= 1
    assert mock_work_manager.poll_completed_events.call_count >= 1

    assert mock_offset_tracker_instance.mark_complete.call_count == 3
    mock_offset_tracker_instance.mark_complete.assert_any_call(0)
    mock_offset_tracker_instance.mark_complete.assert_any_call(1)
    mock_offset_tracker_instance.mark_complete.assert_any_call(2)

    assert mock_offset_tracker_instance.advance_high_water_mark.call_count >= 1

    mock_consumer.commit.assert_called_once()
    commit_args = mock_consumer.commit.call_args
    committed_tp = commit_args.kwargs["offsets"][0]
    assert committed_tp.topic == test_tp_kafka.topic
    assert committed_tp.partition == test_tp_kafka.partition
    assert committed_tp.offset == 3
    assert committed_tp.metadata is not None
    assert len(committed_tp.metadata) > 0


@pytest.mark.asyncio
async def test_backpressure_pause_resume_hysteresis(
    broker_poller,
    mock_consumer,
    mock_work_manager,
    setup_assigned_partitions,
):
    """
    Test backpressure logic with hysteresis:
    1. Load > MAX -> Pause
    2. MIN < Load < MAX -> Stay Paused (Hysteresis)
    3. Load < MIN -> Resume
    """
    # Setup custom thresholds for testing
    broker_poller.MAX_IN_FLIGHT_MESSAGES = 100
    broker_poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 50

    # Setup assigned partitions (required for pause/resume to work)
    test_tp_kafka, _ = setup_assigned_partitions
    # Ensure consumer.assignment() returns the assigned partition
    mock_consumer.assignment.return_value = [test_tp_kafka]

    # 1. Initial state check
    assert broker_poller._is_paused is False

    # 2. Case: High Load -> Trigger Pause
    # Simulate total load = 101 (In-flight: 90, Queued: 11)
    mock_work_manager.get_total_in_flight_count.return_value = 90
    # Provide nested dict structure matching WorkManager.get_virtual_queue_sizes
    mock_work_manager.get_virtual_queue_sizes.return_value = {
        DtoTopicPartition("test-topic", 0): {"virtual-0": 11}
    }

    await broker_poller._check_backpressure()

    assert broker_poller._is_paused is True
    mock_consumer.pause.assert_called_once()
    # Verify it paused the assigned partitions
    mock_consumer.pause.assert_called_with([test_tp_kafka])
    mock_consumer.resume.assert_not_called()

    # Reset mocks
    mock_consumer.pause.reset_mock()
    mock_consumer.resume.reset_mock()

    # 3. Case: Hysteresis Zone (Load drops, but not enough)
    # Load = 80 (MIN=50 < 80 < MAX=100)
    # Should REMAIN PAUSED
    mock_work_manager.get_total_in_flight_count.return_value = 80
    mock_work_manager.get_virtual_queue_sizes.return_value = {}

    await broker_poller._check_backpressure()

    assert broker_poller._is_paused is True
    mock_consumer.pause.assert_not_called()
    mock_consumer.resume.assert_not_called()

    # 4. Case: Low Load -> Trigger Resume
    # Load = 40 ( < MIN=50)
    mock_work_manager.get_total_in_flight_count.return_value = 40

    await broker_poller._check_backpressure()

    assert broker_poller._is_paused is False
    mock_consumer.resume.assert_called_once()
    mock_consumer.resume.assert_called_with([test_tp_kafka])
    mock_consumer.pause.assert_not_called()


@pytest.mark.asyncio
async def test_dlq_publish_on_failure_with_retries_exhausted(
    broker_poller,
    mock_consumer,
    mock_execution_engine,
    setup_assigned_partitions,
    mock_work_manager,
    mock_kafka_config,
):
    """
    Test DLQ publishing when a message fails after max retries.
    Verify:
    1. Message published to DLQ with correct topic suffix
    2. Original key/value preserved
    3. Headers include error metadata
    4. Offset committed only after DLQ publish succeeds
    """
    # Configure DLQ
    mock_kafka_config.dlq_enabled = True
    mock_kafka_config.DLQ_TOPIC_SUFFIX = ".dlq"
    mock_kafka_config.parallel_consumer.execution.max_retries = 3

    test_tp_kafka, mock_offset_tracker_instance = setup_assigned_partitions
    test_tp_dto = DtoTopicPartition(test_tp_kafka.topic, test_tp_kafka.partition)

    # Create a message
    msg = MagicMock(spec=Message)
    msg.topic.return_value = test_tp_kafka.topic
    msg.partition.return_value = test_tp_kafka.partition
    msg.offset.return_value = 100
    msg.key.return_value = b"test-key"
    msg.value.return_value = b"test-value"
    msg.error.return_value = None

    # Mock consume to return the message once
    mock_consumer.consume.side_effect = lambda num_messages, timeout: [msg]

    # Mock producer
    broker_poller.producer.produce = MagicMock()
    broker_poller.producer.flush = MagicMock()

    # Start consumer loop
    broker_poller._running = True
    consumer_task = asyncio.create_task(broker_poller._run_consumer())

    # Wait for message to be submitted
    timeout_seconds = 2
    start_time = asyncio.get_event_loop().time()
    while len(mock_work_manager.submitted_messages) < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for message submission")
        await asyncio.sleep(0.01)

    submitted_item = mock_work_manager.submitted_messages[0]

    # Simulate failure after max retries
    failure_event = CompletionEvent(
        id=submitted_item.id,
        tp=test_tp_dto,
        offset=100,
        epoch=submitted_item.epoch,
        status=CompletionStatus.FAILURE,
        error="Processing failed",
        attempt=3,
    )
    mock_work_manager._push_completion_event(failure_event)

    # Wait for completion processing
    start_time = asyncio.get_event_loop().time()
    while mock_offset_tracker_instance.mark_complete.call_count < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for completion")
        await asyncio.sleep(0.01)

    # Wait for DLQ publish
    start_time = asyncio.get_event_loop().time()
    while broker_poller.producer.produce.call_count < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for DLQ publish")
        await asyncio.sleep(0.01)

    # Stop consumer
    broker_poller._running = False
    await consumer_task

    # Verify DLQ publish
    broker_poller.producer.produce.assert_called_once()
    call_kwargs = broker_poller.producer.produce.call_args.kwargs

    assert call_kwargs["topic"] == "test-topic.dlq"

    assert call_kwargs["key"] == b"test-key"
    assert call_kwargs["value"] == b"test-value"

    headers = dict(call_kwargs["headers"])
    assert headers["x-error-reason"] == b"Processing failed"
    assert headers["x-retry-attempt"] == b"3"
    assert headers["source-topic"] == b"test-topic"
    assert headers["partition"] == b"0"
    assert headers["offset"] == b"100"
    assert "epoch" in headers

    broker_poller.producer.flush.assert_called()

    assert mock_offset_tracker_instance.mark_complete.call_count == 1


@pytest.mark.asyncio
async def test_dlq_disabled_failure_commits_normally(
    broker_poller,
    mock_consumer,
    mock_execution_engine,
    setup_assigned_partitions,
    mock_work_manager,
    mock_kafka_config,
):
    """
    Test that when DLQ is disabled, failures are logged and offset is committed
    without attempting to publish to DLQ.
    """
    # Disable DLQ
    mock_kafka_config.dlq_enabled = False
    mock_kafka_config.parallel_consumer.execution.max_retries = 3

    test_tp_kafka, mock_offset_tracker_instance = setup_assigned_partitions
    test_tp_dto = DtoTopicPartition(test_tp_kafka.topic, test_tp_kafka.partition)

    # Create a message
    msg = MagicMock(spec=Message)
    msg.topic.return_value = test_tp_kafka.topic
    msg.partition.return_value = test_tp_kafka.partition
    msg.offset.return_value = 100
    msg.key.return_value = b"test-key"
    msg.value.return_value = b"test-value"
    msg.error.return_value = None

    # Mock consume
    mock_consumer.consume.side_effect = lambda num_messages, timeout: [msg]

    # Mock producer
    broker_poller.producer.produce = MagicMock()

    # Start consumer loop
    broker_poller._running = True
    consumer_task = asyncio.create_task(broker_poller._run_consumer())

    # Wait for message submission
    timeout_seconds = 2
    start_time = asyncio.get_event_loop().time()
    while len(mock_work_manager.submitted_messages) < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for message submission")
        await asyncio.sleep(0.01)

    submitted_item = mock_work_manager.submitted_messages[0]

    # Simulate failure
    failure_event = CompletionEvent(
        id=submitted_item.id,
        tp=test_tp_dto,
        offset=100,
        epoch=submitted_item.epoch,
        status=CompletionStatus.FAILURE,
        error="Processing failed",
        attempt=3,
    )
    mock_work_manager._push_completion_event(failure_event)

    # Wait for completion processing
    start_time = asyncio.get_event_loop().time()
    while mock_offset_tracker_instance.mark_complete.call_count < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for completion")
        await asyncio.sleep(0.01)

    # Wait a bit to ensure no DLQ publish
    await asyncio.sleep(0.1)

    # Stop consumer
    broker_poller._running = False
    await consumer_task

    # Verify NO DLQ publish
    broker_poller.producer.produce.assert_not_called()

    # Verify commit still happened (offset marked complete)
    assert mock_offset_tracker_instance.mark_complete.call_count == 1


@pytest.mark.asyncio
async def test_dlq_publish_retry_on_failure(
    broker_poller,
    mock_consumer,
    mock_execution_engine,
    setup_assigned_partitions,
    mock_work_manager,
    mock_kafka_config,
    mocker,
):
    """
    Test that DLQ publish is retried on failure using configured retry settings.
    """
    mock_kafka_config.dlq_enabled = True
    mock_kafka_config.DLQ_TOPIC_SUFFIX = ".dlq"
    mock_kafka_config.parallel_consumer.execution.max_retries = 2
    mock_kafka_config.parallel_consumer.execution.retry_backoff_ms = 10
    mock_kafka_config.parallel_consumer.execution.exponential_backoff = False
    mock_kafka_config.parallel_consumer.execution.max_retry_backoff_ms = 30000
    mock_kafka_config.parallel_consumer.execution.retry_jitter_ms = 0

    test_tp_kafka, mock_offset_tracker_instance = setup_assigned_partitions
    test_tp_dto = DtoTopicPartition(test_tp_kafka.topic, test_tp_kafka.partition)

    msg = MagicMock(spec=Message)
    msg.topic.return_value = test_tp_kafka.topic
    msg.partition.return_value = test_tp_kafka.partition
    msg.offset.return_value = 100
    msg.key.return_value = b"test-key"
    msg.value.return_value = b"test-value"
    msg.error.return_value = None

    mock_consumer.consume.side_effect = lambda num_messages, timeout: [msg]

    produce_call_count = 0

    def produce_side_effect(*args, **kwargs):
        nonlocal produce_call_count
        produce_call_count += 1
        if produce_call_count == 1:
            raise Exception("DLQ publish failed")

    broker_poller.producer.produce = MagicMock(side_effect=produce_side_effect)
    broker_poller.producer.flush = MagicMock()

    original_publish_to_dlq = broker_poller._publish_to_dlq

    async def mock_publish_to_dlq(*args, **kwargs):
        with mocker.patch("asyncio.sleep", new_callable=AsyncMock):
            return await original_publish_to_dlq(*args, **kwargs)

    broker_poller._publish_to_dlq = mock_publish_to_dlq

    broker_poller._running = True
    consumer_task = asyncio.create_task(broker_poller._run_consumer())

    timeout_seconds = 2
    start_time = asyncio.get_event_loop().time()
    while len(mock_work_manager.submitted_messages) < 1:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for message submission")
        await asyncio.sleep(0.01)

    submitted_item = mock_work_manager.submitted_messages[0]

    failure_event = CompletionEvent(
        id=submitted_item.id,
        tp=test_tp_dto,
        offset=100,
        epoch=submitted_item.epoch,
        status=CompletionStatus.FAILURE,
        error="Processing failed",
        attempt=2,
    )
    mock_work_manager._push_completion_event(failure_event)

    start_time = asyncio.get_event_loop().time()
    while broker_poller.producer.produce.call_count < 2:
        if asyncio.get_event_loop().time() - start_time > timeout_seconds:
            raise AssertionError("Timeout waiting for DLQ retry")
        await asyncio.sleep(0.01)

    broker_poller._running = False
    await consumer_task

    assert broker_poller.producer.produce.call_count == 2

    broker_poller.producer.flush.assert_called()
