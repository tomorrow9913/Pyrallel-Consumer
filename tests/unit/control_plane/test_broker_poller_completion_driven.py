import asyncio
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest
from confluent_kafka import Consumer
from confluent_kafka import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, CompletionStatus
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_kafka_config():
    config = MagicMock(spec=KafkaConfig)
    config.BOOTSTRAP_SERVERS = ["broker:9092"]
    config.get_consumer_config.return_value = {"group.id": "test_group"}
    config.get_producer_config.return_value = {}
    config.dlq_enabled = False

    execution = MagicMock()
    execution.max_retries = 3

    parallel_consumer = MagicMock()
    parallel_consumer.poll_batch_size = 1000
    parallel_consumer.worker_pool_size = 8
    parallel_consumer.execution = execution
    config.parallel_consumer = parallel_consumer

    return config


@pytest.fixture
def mock_execution_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def topic_partition():
    return DtoTopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def completion_event(topic_partition):
    return CompletionEvent(
        id="work-1",
        tp=topic_partition,
        offset=0,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )


@pytest.fixture
def broker_poller(mock_kafka_config, mock_execution_engine):
    poller = BrokerPoller(
        consume_topic="test-topic",
        kafka_config=mock_kafka_config,
        execution_engine=mock_execution_engine,
    )
    poller.consumer = MagicMock(spec=Consumer)
    poller.producer = MagicMock()
    poller.admin = MagicMock()
    poller._cleanup = AsyncMock()
    poller._max_blocking_duration_ms = 0
    poller.MAX_IN_FLIGHT_MESSAGES = 1000
    poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 500
    poller.QUEUE_MAX_MESSAGES = 0
    return poller


def _make_tracker(tp):
    tracker = MagicMock(spec=OffsetTracker)
    tracker.topic_partition = tp
    tracker.last_committed_offset = -1
    tracker.last_fetched_offset = -1
    tracker.completed_offsets = set()
    tracker.get_current_epoch.return_value = 1
    return tracker


def _run_consume_loop_once_then_stop(broker_poller, first_batch):
    call_count = 0

    def fake_consume(num_messages=1, timeout=0.1):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return first_batch
        broker_poller._running = False
        return []

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)


@pytest.mark.asyncio
async def test_run_consumer_schedules_after_completion_even_without_new_messages(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    _run_consume_loop_once_then_stop(broker_poller, [])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    assert broker_poller._work_manager.schedule.await_count == 1


@pytest.mark.asyncio
async def test_run_consumer_schedules_twice_when_messages_and_completions_share_iteration(
    broker_poller, topic_partition, completion_event
):
    tracker = _make_tracker(topic_partition)
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.submit_message = AsyncMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()

    message = MagicMock()
    message.error.return_value = None
    message.topic.return_value = topic_partition.topic
    message.partition.return_value = topic_partition.partition
    message.offset.return_value = 0
    message.key.return_value = b"key-A"
    message.value.return_value = b"payload-0"
    _run_consume_loop_once_then_stop(broker_poller, [message])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    broker_poller._work_manager.submit_message.assert_awaited_once_with(
        tp=topic_partition,
        offset=0,
        epoch=1,
        key=b"key-A",
        payload=b"payload-0",
    )
    assert broker_poller._work_manager.schedule.await_count == 2
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    assert broker_poller._work_manager.schedule.await_args_list == [call(), call()]


@pytest.mark.asyncio
async def test_run_consumer_falls_back_without_duplicate_enqueue_for_sync_batch_submit(
    broker_poller, topic_partition
):
    tracker = _make_tracker(topic_partition)
    broker_poller._offset_trackers[topic_partition] = tracker

    class _SyncBatchWorkManager:
        def __init__(self) -> None:
            self.batch_calls = 0
            self.submit_message = AsyncMock()
            self.poll_completed_events = AsyncMock(side_effect=[[], []])
            self.schedule = AsyncMock()
            self.get_total_in_flight_count = MagicMock(return_value=0)
            self.get_virtual_queue_sizes = MagicMock(return_value={})

        def submit_message_batch(self, _grouped_messages) -> None:
            self.batch_calls += 1
            return None

    broker_poller._work_manager = _SyncBatchWorkManager()
    broker_poller._process_completed_events = AsyncMock()

    message = MagicMock()
    message.error.return_value = None
    message.topic.return_value = topic_partition.topic
    message.partition.return_value = topic_partition.partition
    message.offset.return_value = 0
    message.key.return_value = b"key-A"
    message.value.return_value = b"payload-0"
    _run_consume_loop_once_then_stop(broker_poller, [message])

    async def passthrough_to_thread(fn, *args, **kwargs):
        return fn(*args, **kwargs)

    broker_poller._running = True
    with patch("asyncio.to_thread", side_effect=passthrough_to_thread), patch(
        "asyncio.sleep", new=AsyncMock()
    ):
        await broker_poller._run_consumer()

    assert broker_poller._work_manager.batch_calls == 0
    broker_poller._work_manager.submit_message.assert_awaited_once_with(
        tp=topic_partition,
        offset=0,
        epoch=1,
        key=b"key-A",
        payload=b"payload-0",
    )


@pytest.mark.asyncio
async def test_completion_monitor_reschedules_without_waiting_for_consumer_loop(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        side_effect=[[completion_event], []]
    )
    broker_poller._work_manager.get_total_in_flight_count.side_effect = [1, 0]
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._commit_offsets = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    async def wait_for_completion(timeout_seconds=None):
        broker_poller._running = False
        return True

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )

    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    broker_poller._execution_engine.wait_for_completion.assert_awaited_once()
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])
    broker_poller._work_manager.schedule.assert_awaited_once_with()
    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 0)])


@pytest.mark.asyncio
async def test_completion_monitor_noops_when_wait_for_completion_times_out(
    broker_poller, topic_partition
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock()
    broker_poller._work_manager.get_total_in_flight_count.side_effect = [1, 0]
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()

    async def wait_for_completion(timeout_seconds=None):
        del timeout_seconds
        broker_poller._running = False
        return False

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )

    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    broker_poller._work_manager.poll_completed_events.assert_not_called()
    broker_poller._process_completed_events.assert_not_called()
    broker_poller._work_manager.schedule.assert_not_called()


@pytest.mark.asyncio
async def test_drain_completion_events_once_processes_blocking_timeout_events(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(return_value=[])
    broker_poller._work_manager.get_total_in_flight_count.return_value = 0
    broker_poller._work_manager.get_virtual_queue_sizes.return_value = {}
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[completion_event])
    broker_poller._process_completed_events = AsyncMock()
    broker_poller._max_blocking_duration_ms = 1

    has_completion = await broker_poller._drain_completion_events_once()

    assert has_completion is True
    broker_poller._process_completed_events.assert_awaited_once_with([completion_event])


@pytest.mark.asyncio
async def test_completion_monitor_submits_next_same_key_work_without_new_consume(
    broker_poller, topic_partition
):
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager.on_assign({topic_partition: tracker})
    broker_poller._work_manager._blocking_cache_ttl = 0
    broker_poller._work_manager._blocking_cache_counter = 0
    broker_poller._max_blocking_duration_ms = 0

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=1,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-1",
    )
    await broker_poller._work_manager.schedule()

    assert broker_poller._execution_engine.submit.await_count == 1
    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]

    broker_poller._execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=first_item.id,
            tp=topic_partition,
            offset=first_item.offset,
            epoch=tracker.get_current_epoch(),
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    async def wait_for_completion(timeout_seconds=None):
        del timeout_seconds
        broker_poller._running = False
        return True

    broker_poller._execution_engine.wait_for_completion.side_effect = (
        wait_for_completion
    )
    broker_poller._running = True
    with patch("asyncio.sleep", new=AsyncMock()):
        await broker_poller._run_completion_monitor()

    assert broker_poller._execution_engine.submit.await_count == 2
    second_item = broker_poller._execution_engine.submit.await_args_list[1].args[0]
    assert second_item.offset == 1
    assert second_item.key == b"key-A"


@pytest.mark.asyncio
async def test_start_skips_completion_monitor_task_when_disabled(
    broker_poller,
    mock_kafka_config,
):
    mock_kafka_config.parallel_consumer.strict_completion_monitor_enabled = False
    created_coroutines: list[str] = []

    def fake_create_task(coro, *, name=None):
        del name
        created_coroutines.append(coro.cr_code.co_name)
        coro.close()
        return MagicMock()

    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=broker_poller.producer,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=broker_poller.admin,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=broker_poller.consumer,
        ),
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    assert created_coroutines == ["_run_consumer"]
    assert broker_poller._consumer_task is not None
    assert broker_poller._completion_monitor_task is None


@pytest.mark.asyncio
async def test_start_stores_consumer_task_handle(broker_poller):
    created_tasks = []

    def fake_create_task(coro, *, name=None):
        task = MagicMock()
        task.name = name
        created_tasks.append((coro.cr_code.co_name, name, task))
        coro.close()
        return task

    with (
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Producer",
            return_value=broker_poller.producer,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.AdminClient",
            return_value=broker_poller.admin,
        ),
        patch(
            "pyrallel_consumer.control_plane.broker_poller.Consumer",
            return_value=broker_poller.consumer,
        ),
        patch("asyncio.create_task", side_effect=fake_create_task),
    ):
        await broker_poller.start()

    assert broker_poller._consumer_task is created_tasks[-1][2]
    assert created_tasks[-1][0] == "_run_consumer"


@pytest.mark.asyncio
async def test_stop_awaits_and_clears_consumer_task_handle(broker_poller):
    async def fake_consumer_task():
        await asyncio.sleep(0)
        broker_poller._shutdown_event.set()

    broker_poller._running = True
    broker_poller._consumer_task_stop_timeout_seconds = 0.05
    broker_poller._consumer_task = asyncio.create_task(fake_consumer_task())

    await broker_poller.stop()

    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_stop_cancels_consumer_task_when_wait_times_out(broker_poller):
    cancelled = asyncio.Event()

    async def hanging_consumer_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            broker_poller._shutdown_event.set()
            raise

    broker_poller._running = True
    broker_poller._consumer_task_stop_timeout_seconds = 0.01
    broker_poller._consumer_task = asyncio.create_task(hanging_consumer_task())

    await broker_poller.stop()

    assert cancelled.is_set()
    assert broker_poller._consumer_task is None


@pytest.mark.asyncio
async def test_stop_uses_stable_consumer_task_reference_when_timeout_races_with_cleanup(
    broker_poller,
):
    cancelled = asyncio.Event()

    async def hanging_consumer_task():
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled.set()
            broker_poller._shutdown_event.set()
            raise

    async def fake_wait_for(task, timeout):
        broker_poller._consumer_task = None
        broker_poller._shutdown_event.set()
        raise asyncio.TimeoutError

    broker_poller._running = True
    broker_poller._consumer_task = asyncio.create_task(hanging_consumer_task())

    with patch("asyncio.wait_for", side_effect=fake_wait_for):
        await broker_poller.stop()

    assert cancelled.is_set() or broker_poller._shutdown_event.is_set()


@pytest.mark.asyncio
async def test_wait_closed_returns_immediately_when_not_running_and_no_task(
    broker_poller,
):
    await broker_poller.wait_closed()


@pytest.mark.asyncio
async def test_stale_completion_does_not_resubmit_next_same_key_work(
    broker_poller, topic_partition, caplog
):
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=0,
        max_revoke_grace_ms=0,
        initial_completed_offsets=set(),
    )
    tracker.increment_epoch()
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._work_manager.on_assign({topic_partition: tracker})
    broker_poller._work_manager._blocking_cache_ttl = 0
    broker_poller._work_manager._blocking_cache_counter = 0

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=1,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-1",
    )
    await broker_poller._work_manager.schedule()

    assert broker_poller._execution_engine.submit.await_count == 1
    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]

    stale_completion = CompletionEvent(
        id=first_item.id,
        tp=topic_partition,
        offset=first_item.offset,
        epoch=tracker.get_current_epoch() - 1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    broker_poller._execution_engine.poll_completed_events.return_value = [
        stale_completion
    ]

    with caplog.at_level("WARNING"):
        completed_events = await broker_poller._work_manager.poll_completed_events()
        await broker_poller._process_completed_events(completed_events)

    assert broker_poller._execution_engine.submit.await_count == 1
    assert "Discarding zombie completion" in caplog.text


@pytest.mark.asyncio
async def test_on_assign_shared_tracker_allows_key_hash_backlog_to_resume(
    broker_poller, topic_partition
):
    broker_poller._on_assign(
        broker_poller.consumer,
        [KafkaTopicPartition(topic_partition.topic, topic_partition.partition, 0)],
    )
    broker_poller.ORDERING_MODE = broker_poller._work_manager._ordering_mode
    broker_poller._work_manager._blocking_cache_ttl = 0
    broker_poller._work_manager._blocking_cache_counter = 0

    tracker = broker_poller._offset_trackers[topic_partition]
    shared_tracker = broker_poller._work_manager._offset_trackers[topic_partition]
    assert shared_tracker is tracker
    assert shared_tracker.get_current_epoch() == tracker.get_current_epoch() == 1

    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=0,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-0",
    )
    await broker_poller._work_manager.submit_message(
        tp=topic_partition,
        offset=1,
        epoch=tracker.get_current_epoch(),
        key=b"key-A",
        payload=b"payload-1",
    )
    await broker_poller._work_manager.schedule()

    assert broker_poller._execution_engine.submit.await_count == 1
    first_item = broker_poller._execution_engine.submit.await_args_list[0].args[0]

    broker_poller._execution_engine.poll_completed_events.return_value = [
        CompletionEvent(
            id=first_item.id,
            tp=topic_partition,
            offset=first_item.offset,
            epoch=tracker.get_current_epoch(),
            status=CompletionStatus.SUCCESS,
            error=None,
            attempt=1,
        )
    ]

    completed_events = await broker_poller._work_manager.poll_completed_events()
    await broker_poller._process_completed_events(completed_events)
    await broker_poller._work_manager.schedule()

    assert broker_poller._execution_engine.submit.await_count == 2
    second_item = broker_poller._execution_engine.submit.await_args_list[1].args[0]
    assert second_item.offset == 1
