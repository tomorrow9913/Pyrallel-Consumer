import asyncio
import threading
import time
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
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._execution_engine = AsyncMock()
    broker_poller._commit_ready_offsets = AsyncMock()
    broker_poller._dirty_commit_partitions.add(topic_partition)

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
    broker_poller._commit_ready_offsets.assert_awaited_once_with(force=True)


@pytest.mark.asyncio
async def test_commit_ready_offsets_serializes_commit_calls_and_releases_control_lock(
    broker_poller, topic_partition
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    active_commits = 0
    max_active_commits = 0

    async def fake_commit_offsets(commits_to_make):
        nonlocal active_commits, max_active_commits
        assert commits_to_make == [(topic_partition, 0)]
        assert not broker_poller._control_lock.locked()
        active_commits += 1
        max_active_commits = max(max_active_commits, active_commits)
        await asyncio.sleep(0)
        active_commits -= 1

    broker_poller._commit_offsets = AsyncMock(side_effect=fake_commit_offsets)

    await asyncio.gather(
        broker_poller._commit_ready_offsets(force=True),
        broker_poller._commit_ready_offsets(force=True),
    )

    assert broker_poller._commit_offsets.await_count == 2
    assert max_active_commits == 1


@pytest.mark.asyncio
async def test_commit_ready_offsets_tolerates_tracker_removed_after_candidate_generation(
    broker_poller, topic_partition
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller.consumer = MagicMock(spec=Consumer)
    dispatch_support = MagicMock()

    def build_commit_candidates():
        broker_poller._offset_trackers.pop(topic_partition, None)
        return [(topic_partition, 0)]

    dispatch_support.build_commit_candidates.side_effect = build_commit_candidates
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    await broker_poller._commit_ready_offsets()

    broker_poller.consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_commit_ready_offsets_waits_for_completion_cadence_before_commit(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._commit_debounce_completion_threshold = 3
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._commit_offsets = AsyncMock()
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 2)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(side_effect=[1, 2])
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    await broker_poller._process_completed_events([completion_event])
    await broker_poller._commit_ready_offsets()

    broker_poller._commit_offsets.assert_not_awaited()

    await broker_poller._process_completed_events(
        [
            CompletionEvent(
                id="work-2",
                tp=topic_partition,
                offset=1,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
            CompletionEvent(
                id="work-3",
                tp=topic_partition,
                offset=2,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
        ]
    )
    await broker_poller._commit_ready_offsets()

    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 2)])


@pytest.mark.asyncio
async def test_commit_ready_offsets_force_flushes_dirty_partitions(
    broker_poller, topic_partition, completion_event
):
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._commit_debounce_completion_threshold = 100
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._commit_offsets = AsyncMock()
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(return_value=1)
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    await broker_poller._process_completed_events([completion_event])
    await broker_poller._commit_ready_offsets(force=True)

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
async def test_drain_completion_events_once_preserves_work_manager_refill_overlap(
    broker_poller, completion_event
):
    broker_poller._work_manager = MagicMock()
    broker_poller._work_manager.poll_completed_events = AsyncMock(
        return_value=[completion_event]
    )
    broker_poller._work_manager.schedule = AsyncMock()
    broker_poller._handle_blocking_timeouts = AsyncMock(return_value=[])
    broker_poller._process_completed_events = AsyncMock()

    has_completion = await broker_poller._drain_completion_events_once()

    assert has_completion is True
    broker_poller._work_manager.poll_completed_events.assert_awaited_once_with()
    broker_poller._work_manager.schedule.assert_awaited_once_with()


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
async def test_graceful_stop_drains_before_closing_consumer(
    broker_poller,
):
    events: list[str] = []
    consume_started = threading.Event()

    def fake_consume(num_messages=1, timeout=0.1):
        del num_messages, timeout
        consume_started.set()
        while broker_poller._running:
            time.sleep(0.001)
        return []

    async def fake_cleanup():
        events.append("cleanup")
        broker_poller.consumer = None

    async def fake_drain_shutdown_work(*, timeout_seconds: float) -> bool:
        del timeout_seconds
        events.append(f"drain_consumer_open={broker_poller.consumer is not None}")
        return True

    broker_poller.consumer.consume = MagicMock(side_effect=fake_consume)
    broker_poller._drain_completion_events_once = AsyncMock(return_value=False)
    broker_poller._commit_ready_offsets = AsyncMock()
    broker_poller._cleanup = AsyncMock(side_effect=fake_cleanup)
    broker_poller._drain_shutdown_work = AsyncMock(side_effect=fake_drain_shutdown_work)
    broker_poller._get_consume_timeout_seconds = AsyncMock(return_value=0.001)
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._consumer_task_stop_timeout_seconds = 0.05
    broker_poller._running = True
    broker_poller._consumer_task = asyncio.create_task(broker_poller._run_consumer())

    try:
        assert await asyncio.to_thread(consume_started.wait, 1)

        await broker_poller.stop()
    finally:
        broker_poller._running = False
        if broker_poller._consumer_task is not None:
            broker_poller._consumer_task.cancel()
            await asyncio.gather(
                broker_poller._consumer_task,
                return_exceptions=True,
            )

    assert events == ["drain_consumer_open=True", "cleanup"]
    broker_poller._drain_shutdown_work.assert_awaited_once()
    broker_poller._cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_graceful_stop_cleans_up_when_stop_runtime_raises(
    broker_poller,
):
    class _FailingLifecycleSupport:
        async def stop_runtime(self, **_kwargs):
            raise RuntimeError("stop-runtime failed")

    broker_poller._make_task_lifecycle_support = MagicMock(
        return_value=_FailingLifecycleSupport()
    )
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._cleanup = AsyncMock()
    broker_poller._running = True
    broker_poller._consumer_task = MagicMock()

    with pytest.raises(RuntimeError, match="stop-runtime failed"):
        await broker_poller.stop()

    broker_poller._cleanup.assert_awaited_once()
    assert broker_poller._defer_consumer_cleanup_for_stop is False


@pytest.mark.asyncio
async def test_concurrent_graceful_stop_cleans_up_once(
    broker_poller,
):
    class _SlowLifecycleSupport:
        def __init__(self) -> None:
            self.calls = 0

        async def stop_runtime(self, **_kwargs):
            self.calls += 1
            await asyncio.sleep(0.01)
            broker_poller._shutdown_event.set()

    support = _SlowLifecycleSupport()
    broker_poller._make_task_lifecycle_support = MagicMock(return_value=support)
    broker_poller._shutdown_policy = MagicMock(return_value="graceful")
    broker_poller._drain_shutdown_work = AsyncMock(return_value=True)
    broker_poller._cleanup = AsyncMock()
    broker_poller._running = True
    broker_poller._consumer_task = MagicMock()

    await asyncio.gather(broker_poller.stop(), broker_poller.stop())

    assert support.calls == 1
    broker_poller._drain_shutdown_work.assert_awaited_once()
    broker_poller._cleanup.assert_awaited_once()


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
