import asyncio
import json
import os
import queue
import time
import uuid
from collections import Counter
from multiprocessing import Manager, Queue
from pathlib import Path
from typing import Any, Callable

import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import (
    Consumer,
    KafkaError,
    KafkaException,
    NewTopic,
    Producer,
)
from confluent_kafka.cimpl import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import ExecutionMode, OrderingMode, WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine

BOOTSTRAP_SERVERS = "localhost:9092"
DLQ_SUFFIX = ".dlq"
E2E_CONF = {
    "bootstrap.servers": BOOTSTRAP_SERVERS,
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
}


class _QueueBackedResults:
    def __init__(self, result_queue) -> None:
        self._result_queue = result_queue

    def append(self, entry) -> None:
        self._result_queue.put(entry)


def _drain_result_queue(result_queue) -> list:
    entries = []
    while True:
        try:
            entries.append(result_queue.get_nowait())
        except queue.Empty:
            return entries


def _require_kafka() -> None:
    strict_ci_gate = os.environ.get(
        "PYRALLEL_E2E_REQUIRE_BROKER", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    admin = AdminClient(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 1000,
        }
    )
    try:
        metadata = admin.list_topics(timeout=5)
        if not getattr(metadata, "brokers", None):
            raise RuntimeError("Kafka metadata response did not include broker entries")
    except Exception as exc:
        message = f"Kafka broker not available for recovery e2e tests: {exc}"
        if strict_ci_gate:
            pytest.fail(message)
        pytest.skip(message)


class _BlockingPartitionWorker:
    def __init__(
        self,
        shared_results,
        started_event,
        release_event,
        shared_state,
        label: str,
        block_partition: int,
        block_offset: int,
    ) -> None:
        self._shared_results = shared_results
        self._started_event = started_event
        self._release_event = release_event
        self._shared_state = shared_state
        self._label = label
        self._block_partition = block_partition
        self._block_offset = block_offset

    def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        self._shared_results.append(
            (
                "started",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )
        should_block = (
            item.tp.partition == self._block_partition
            and item.offset == self._block_offset
            and not self._shared_state.get("blocked_once", False)
        )
        if should_block:
            self._shared_state["blocked_once"] = True
            self._started_event.set()
            deadline = time.monotonic() + 15
            while not self._release_event.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting to release blocked worker")
                time.sleep(0.01)
        else:
            time.sleep(0.02)

        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )


class _RecordingWorker:
    def __init__(self, shared_results, label: str, sleep_ms: float = 5.0) -> None:
        self._shared_results = shared_results
        self._label = label
        self._sleep_ms = sleep_ms

    def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        if self._sleep_ms > 0:
            time.sleep(self._sleep_ms / 1000.0)
        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )


class _AsyncBlockingPartitionWorker:
    def __init__(
        self,
        shared_results,
        started_event,
        release_event,
        shared_state,
        label: str,
        block_partition: int,
        block_offset: int,
    ) -> None:
        self._shared_results = shared_results
        self._started_event = started_event
        self._release_event = release_event
        self._shared_state = shared_state
        self._label = label
        self._block_partition = block_partition
        self._block_offset = block_offset

    async def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        self._shared_results.append(
            (
                "started",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )
        should_block = (
            item.tp.partition == self._block_partition
            and item.offset == self._block_offset
            and not self._shared_state.get("blocked_once", False)
        )
        if should_block:
            self._shared_state["blocked_once"] = True
            self._started_event.set()
            deadline = time.monotonic() + 15
            while not self._release_event.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting to release blocked worker")
                await asyncio.sleep(0.01)
        else:
            await asyncio.sleep(0.02)

        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )


class _AsyncRecordingWorker:
    def __init__(self, shared_results, label: str, sleep_ms: float = 5.0) -> None:
        self._shared_results = shared_results
        self._label = label
        self._sleep_ms = sleep_ms

    async def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        if self._sleep_ms > 0:
            await asyncio.sleep(self._sleep_ms / 1000.0)
        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
            )
        )


class _RetryThenSucceedWorker:
    def __init__(
        self,
        shared_results,
        label: str,
        target_partition: int,
        target_offset: int,
        fail_first_attempts: int,
        sleep_ms: float = 5.0,
        success_started_event=None,
        success_release_event=None,
        attempt_log_path: str | None = None,
    ) -> None:
        self._shared_results = shared_results
        self._attempt_counts: dict[str, int] = {}
        self._label = label
        self._target_partition = target_partition
        self._target_offset = target_offset
        self._fail_first_attempts = fail_first_attempts
        self._sleep_ms = sleep_ms
        self._success_started_event = success_started_event
        self._success_release_event = success_release_event
        self._attempt_log_path = attempt_log_path

    def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        key = f"{item.tp.partition}:{item.offset}"
        attempt = int(self._attempt_counts.get(key, 0)) + 1
        self._attempt_counts[key] = attempt
        if self._shared_results is not None:
            self._shared_results.append(
                (
                    "attempt",
                    self._label,
                    item.tp.partition,
                    item.offset,
                    payload["sequence"],
                    attempt,
                )
            )
        if self._attempt_log_path is not None:
            with open(self._attempt_log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{item.tp.partition},{item.offset},{attempt}\n")

        if self._sleep_ms > 0:
            time.sleep(self._sleep_ms / 1000.0)

        if (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
            and attempt <= self._fail_first_attempts
        ):
            raise RuntimeError(f"intentional retry trigger on attempt {attempt}")

        should_block_before_success = (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
            and attempt == self._fail_first_attempts + 1
            and self._success_started_event is not None
            and self._success_release_event is not None
        )
        if should_block_before_success:
            self._success_started_event.set()
            deadline = time.monotonic() + 15
            while not self._success_release_event.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting to release retry success")
                time.sleep(0.01)

        if self._shared_results is not None:
            self._shared_results.append(
                (
                    "completed",
                    self._label,
                    item.tp.partition,
                    item.offset,
                    payload["sequence"],
                    attempt,
                )
            )


class _AsyncRetryThenSucceedWorker:
    def __init__(
        self,
        shared_results,
        label: str,
        target_partition: int,
        target_offset: int,
        fail_first_attempts: int,
        sleep_ms: float = 5.0,
        success_started_event=None,
        success_release_event=None,
        attempt_log_path: str | None = None,
    ) -> None:
        self._shared_results = shared_results
        self._attempt_counts: dict[str, int] = {}
        self._label = label
        self._target_partition = target_partition
        self._target_offset = target_offset
        self._fail_first_attempts = fail_first_attempts
        self._sleep_ms = sleep_ms
        self._success_started_event = success_started_event
        self._success_release_event = success_release_event
        self._attempt_log_path = attempt_log_path

    async def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        key = f"{item.tp.partition}:{item.offset}"
        attempt = int(self._attempt_counts.get(key, 0)) + 1
        self._attempt_counts[key] = attempt
        if self._shared_results is not None:
            self._shared_results.append(
                (
                    "attempt",
                    self._label,
                    item.tp.partition,
                    item.offset,
                    payload["sequence"],
                    attempt,
                )
            )
        if self._attempt_log_path is not None:
            with open(self._attempt_log_path, "a", encoding="utf-8") as handle:
                handle.write(f"{item.tp.partition},{item.offset},{attempt}\n")

        if self._sleep_ms > 0:
            await asyncio.sleep(self._sleep_ms / 1000.0)

        if (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
            and attempt <= self._fail_first_attempts
        ):
            raise RuntimeError(f"intentional retry trigger on attempt {attempt}")

        should_block_before_success = (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
            and attempt == self._fail_first_attempts + 1
            and self._success_started_event is not None
            and self._success_release_event is not None
        )
        if should_block_before_success:
            self._success_started_event.set()
            deadline = time.monotonic() + 15
            while not self._success_release_event.is_set():
                if time.monotonic() >= deadline:
                    raise TimeoutError("timed out waiting to release retry success")
                await asyncio.sleep(0.01)

        if self._shared_results is not None:
            self._shared_results.append(
                (
                    "completed",
                    self._label,
                    item.tp.partition,
                    item.offset,
                    payload["sequence"],
                    attempt,
                )
            )


class _AlwaysFailWorker:
    def __init__(
        self,
        shared_results,
        label: str,
        target_partition: int,
        target_offset: int,
        sleep_ms: float = 5.0,
    ) -> None:
        self._shared_results = shared_results
        self._attempt_counts: dict[str, int] = {}
        self._label = label
        self._target_partition = target_partition
        self._target_offset = target_offset
        self._sleep_ms = sleep_ms

    def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        key = f"{item.tp.partition}:{item.offset}"
        attempt = int(self._attempt_counts.get(key, 0)) + 1
        self._attempt_counts[key] = attempt
        self._shared_results.append(
            (
                "attempt",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
                attempt,
            )
        )

        if self._sleep_ms > 0:
            time.sleep(self._sleep_ms / 1000.0)

        if (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
        ):
            raise RuntimeError(f"intentional dlq trigger on attempt {attempt}")

        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
                attempt,
            )
        )


class _AsyncAlwaysFailWorker:
    def __init__(
        self,
        shared_results,
        label: str,
        target_partition: int,
        target_offset: int,
        sleep_ms: float = 5.0,
    ) -> None:
        self._shared_results = shared_results
        self._attempt_counts: dict[str, int] = {}
        self._label = label
        self._target_partition = target_partition
        self._target_offset = target_offset
        self._sleep_ms = sleep_ms

    async def __call__(self, item: WorkItem) -> None:
        payload = json.loads(item.payload.decode("utf-8"))
        key = f"{item.tp.partition}:{item.offset}"
        attempt = int(self._attempt_counts.get(key, 0)) + 1
        self._attempt_counts[key] = attempt
        self._shared_results.append(
            (
                "attempt",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
                attempt,
            )
        )

        if self._sleep_ms > 0:
            await asyncio.sleep(self._sleep_ms / 1000.0)

        if (
            item.tp.partition == self._target_partition
            and item.offset == self._target_offset
        ):
            raise RuntimeError(f"intentional dlq trigger on attempt {attempt}")

        self._shared_results.append(
            (
                "completed",
                self._label,
                item.tp.partition,
                item.offset,
                payload["sequence"],
                attempt,
            )
        )


async def _wait_until(
    predicate: Callable[[], bool], timeout_seconds: float, message: str
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(message)


async def _wait_for_event(event, timeout_seconds: float, message: str) -> None:
    await _wait_until(event.is_set, timeout_seconds, message)


def _topic_name(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _build_kafka_config(group_id: str) -> KafkaConfig:
    kafka_config = KafkaConfig(
        bootstrap_servers=[BOOTSTRAP_SERVERS],
        consumer_group=group_id,
        auto_offset_reset=E2E_CONF["auto.offset.reset"],
        enable_auto_commit=E2E_CONF["enable.auto.commit"],
    )
    kafka_config.dlq_enabled = True
    kafka_config.dlq_topic_suffix = DLQ_SUFFIX
    kafka_config.parallel_consumer.rebalance_state_strategy = "metadata_snapshot"
    return kafka_config


def _build_process_runtime(
    *,
    topic: str,
    kafka_config: KafkaConfig,
    worker_fn: Any,
    max_in_flight: int = 16,
    process_count: int = 1,
) -> tuple[BrokerPoller, ProcessExecutionEngine]:
    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.mode = ExecutionMode.PROCESS
    execution_config.max_in_flight = max_in_flight
    execution_config.process_config.process_count = process_count
    execution_config.process_config.queue_size = 64
    execution_config.process_config.batch_size = 1
    execution_config.process_config.max_batch_wait_ms = 0
    # Recovery E2E workers use fork-inherited synchronization primitives to
    # coordinate broker timing; keep the picklability contract covered in unit
    # tests while allowing these broker-backed harness workers.
    execution_config.process_config.require_picklable_worker = False
    execution_config.max_retries = 2
    execution_config.retry_backoff_ms = 10
    execution_config.max_retry_backoff_ms = 20
    execution_config.retry_jitter_ms = 0

    engine = ProcessExecutionEngine(config=execution_config, worker_fn=worker_fn)
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
        ordering_mode=OrderingMode.PARTITION,
    )
    poller = BrokerPoller(
        consume_topic=topic,
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )
    poller.ORDERING_MODE = OrderingMode.PARTITION
    return poller, engine


def _build_async_runtime(
    *,
    topic: str,
    kafka_config: KafkaConfig,
    worker_fn: Any,
    max_in_flight: int = 16,
) -> tuple[BrokerPoller, AsyncExecutionEngine]:
    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.mode = ExecutionMode.ASYNC
    execution_config.max_in_flight = max_in_flight
    execution_config.max_retries = 2
    execution_config.retry_backoff_ms = 10
    execution_config.max_retry_backoff_ms = 20
    execution_config.retry_jitter_ms = 0

    engine = AsyncExecutionEngine(config=execution_config, worker_fn=worker_fn)
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
        ordering_mode=OrderingMode.PARTITION,
    )
    poller = BrokerPoller(
        consume_topic=topic,
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )
    poller.ORDERING_MODE = OrderingMode.PARTITION
    return poller, engine


def _build_recovery_runtime(
    *,
    execution_mode: ExecutionMode,
    topic: str,
    kafka_config: KafkaConfig,
    worker_fn: Any,
    max_in_flight: int = 16,
    process_count: int = 1,
) -> tuple[BrokerPoller, Any]:
    if execution_mode == ExecutionMode.ASYNC:
        return _build_async_runtime(
            topic=topic,
            kafka_config=kafka_config,
            worker_fn=worker_fn,
            max_in_flight=max_in_flight,
        )
    return _build_process_runtime(
        topic=topic,
        kafka_config=kafka_config,
        worker_fn=worker_fn,
        max_in_flight=max_in_flight,
        process_count=process_count,
    )


def _create_topic(admin: AdminClient, topic_name: str, num_partitions: int) -> None:
    topic = NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
    admin.create_topics([topic])[topic_name].result(timeout=10)
    time.sleep(1)


def _delete_topic(admin: AdminClient, topic_name: str) -> None:
    try:
        admin.delete_topics([topic_name])[topic_name].result(timeout=10)
        time.sleep(1)
    except KafkaException as exc:
        if exc.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise


def _produce_partition_messages(
    topic: str, partition: int, count: int, start_sequence: int = 0
) -> None:
    producer = Producer({"bootstrap.servers": BOOTSTRAP_SERVERS})
    try:
        for sequence in range(start_sequence, start_sequence + count):
            payload = json.dumps({"sequence": sequence}).encode("utf-8")
            producer.produce(
                topic=topic,
                partition=partition,
                key=f"key-{partition}".encode("utf-8"),
                value=payload,
            )
        producer.flush(10)
    finally:
        producer.flush(10)


def _fetch_committed_offset(group_id: str, topic: str, partition: int) -> int:
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "enable.auto.commit": False,
        }
    )
    try:
        committed = consumer.committed(
            [KafkaTopicPartition(topic, partition)], timeout=10
        )
        return committed[0].offset
    finally:
        consumer.close()


def _consume_single_record(topic: str, group_id: str, timeout_seconds: float = 10.0):
    consumer = Consumer(
        {
            "bootstrap.servers": BOOTSTRAP_SERVERS,
            "group.id": group_id,
            "enable.auto.commit": False,
            "auto.offset.reset": "earliest",
        }
    )
    try:
        consumer.subscribe([topic])
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            msg = consumer.poll(0.5)
            if msg is None:
                continue
            if msg.error() is not None:
                raise KafkaException(msg.error())
            return msg
    finally:
        consumer.close()
    raise AssertionError(f"timed out waiting for DLQ record on topic {topic}")


def _read_attempt_log(path: Path) -> list[tuple[int, int, int]]:
    if not path.exists():
        return []
    attempts = []
    for line in path.read_text(encoding="utf-8").splitlines():
        partition, offset, attempt = line.split(",", 2)
        attempts.append((int(partition), int(offset), int(attempt)))
    return attempts


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_process_rebalance_keeps_commit_safe_while_work_is_inflight(
    execution_mode: ExecutionMode,
) -> None:
    _require_kafka()
    mode_label = execution_mode.value
    topic = _topic_name(f"{mode_label}-recovery-rebalance")
    group_id = _topic_name(f"{mode_label}-recovery-group")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    partition = 1
    produced_count = 5

    _create_topic(admin, topic, num_partitions=2)
    _produce_partition_messages(topic, partition=0, count=2)
    _produce_partition_messages(topic, partition=partition, count=produced_count)

    all_entries = []
    partition_entries = []
    completed_entries = []
    secondary_assignments = set()
    final_committed_offset = -1001
    with Manager() as manager:
        shared_results = manager.list()
        started_event = manager.Event()
        release_event = manager.Event()
        shared_state = manager.dict(blocked_once=False)

        blocking_worker_cls = (
            _AsyncBlockingPartitionWorker
            if execution_mode == ExecutionMode.ASYNC
            else _BlockingPartitionWorker
        )
        recording_worker_cls = (
            _AsyncRecordingWorker
            if execution_mode == ExecutionMode.ASYNC
            else _RecordingWorker
        )

        primary_worker = blocking_worker_cls(
            shared_results=shared_results,
            started_event=started_event,
            release_event=release_event,
            shared_state=shared_state,
            label="primary",
            block_partition=partition,
            block_offset=0,
        )
        secondary_worker = recording_worker_cls(
            shared_results=shared_results, label="secondary"
        )

        primary_config = _build_kafka_config(group_id)
        secondary_config = _build_kafka_config(group_id)
        primary_poller, primary_engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=primary_config,
            worker_fn=primary_worker,
            max_in_flight=1,
        )
        secondary_poller, secondary_engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=secondary_config,
            worker_fn=secondary_worker,
            max_in_flight=1,
        )

        await primary_poller.start()
        try:
            await _wait_for_event(
                started_event,
                timeout_seconds=20,
                message="primary poller never started in-flight partition work",
            )

            blocked_commit = _fetch_committed_offset(group_id, topic, partition)
            assert blocked_commit in (-1001, 0), (
                "commit advanced before the blocked recovery work completed: "
                f"offset={blocked_commit}"
            )

            await secondary_poller.start()
            await _wait_until(
                lambda: len(getattr(secondary_poller, "_offset_trackers", {})) > 0,
                timeout_seconds=10,
                message="secondary poller never received any partition assignment",
            )
            secondary_assignments = {
                (tp.topic, tp.partition)
                for tp in getattr(secondary_poller, "_offset_trackers", {}).keys()
            }
            await asyncio.sleep(2)

            release_event.set()
            await _wait_until(
                lambda: (
                    len(
                        {
                            entry[3]
                            for entry in list(shared_results)
                            if entry[0] == "completed" and entry[2] == partition
                        }
                    )
                    >= produced_count
                ),
                timeout_seconds=30,
                message="rebalance scenario did not complete all produced offsets",
            )
            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    == produced_count
                ),
                timeout_seconds=15,
                message="rebalance scenario never committed the final safe offset",
            )
            final_committed_offset = _fetch_committed_offset(group_id, topic, partition)
            all_entries = list(shared_results)
        finally:
            release_event.set()
            if not all_entries:
                all_entries = list(shared_results)
            await secondary_poller.stop()
            await secondary_engine.shutdown()
            await primary_poller.stop()
            await primary_engine.shutdown()
            partition_entries = [
                entry for entry in all_entries if entry[2] == partition
            ]
            completed_entries = [
                entry for entry in partition_entries if entry[0] == "completed"
            ]
            _delete_topic(admin, topic)

    completed_offsets = [entry[3] for entry in completed_entries]
    assert set(completed_offsets) == set(range(produced_count))
    assert final_committed_offset == produced_count
    assert secondary_assignments, (
        "expected the secondary poller to receive at least one partition assignment after rebalance; "
        f"all entries={all_entries}"
    )


@pytest.mark.asyncio
async def test_process_graceful_stop_drains_inflight_before_close() -> None:
    _require_kafka()
    topic = _topic_name("process-recovery-stop-drain")
    group_id = _topic_name("process-recovery-group")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    partition = 0
    produced_count = 3

    _create_topic(admin, topic, num_partitions=1)
    _produce_partition_messages(topic, partition=partition, count=produced_count)

    all_entries = []
    final_committed_offset = -1001
    stop_task: asyncio.Task[None] | None = None
    with Manager() as manager:
        shared_results = manager.list()
        started_event = manager.Event()
        release_event = manager.Event()
        shared_state = manager.dict(blocked_once=False)

        worker = _BlockingPartitionWorker(
            shared_results=shared_results,
            started_event=started_event,
            release_event=release_event,
            shared_state=shared_state,
            label="stop-drain",
            block_partition=partition,
            block_offset=0,
        )

        kafka_config = _build_kafka_config(group_id)
        kafka_config.parallel_consumer.poll_batch_size = produced_count
        poller, engine = _build_process_runtime(
            topic=topic,
            kafka_config=kafka_config,
            worker_fn=worker,
            max_in_flight=1,
        )

        await poller.start()
        try:
            await _wait_for_event(
                started_event,
                timeout_seconds=20,
                message="poller never started blocked in-flight stop-drain work",
            )

            stop_task = asyncio.create_task(poller.stop())
            await _wait_until(
                lambda: poller._shutdown_event.is_set(),
                timeout_seconds=10,
                message="graceful stop never reached the shutdown drain boundary",
            )
            await asyncio.sleep(0)
            assert not stop_task.done(), (
                "graceful stop returned after the consumer loop stopped but before "
                "in-flight process work was released"
            )
            blocked_commit = _fetch_committed_offset(group_id, topic, partition)
            assert blocked_commit in (-1001, 0), (
                "commit advanced before the blocked stop-drain work completed: "
                f"offset={blocked_commit}"
            )

            release_event.set()
            await asyncio.wait_for(stop_task, timeout=30)
            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    == produced_count
                ),
                timeout_seconds=15,
                message="graceful stop did not commit all drained in-flight work",
            )
            final_committed_offset = _fetch_committed_offset(group_id, topic, partition)
        finally:
            release_event.set()
            if stop_task is not None and not stop_task.done():
                stop_task.cancel()
                await asyncio.gather(stop_task, return_exceptions=True)
            if getattr(poller, "_running", False) or getattr(
                poller, "_consumer_task", None
            ):
                await poller.stop()
            await engine.shutdown()
            all_entries = list(shared_results)
            _delete_topic(admin, topic)

    completed_offsets = [
        entry[3]
        for entry in all_entries
        if entry[0] == "completed" and entry[2] == partition
    ]
    assert set(completed_offsets) == set(range(produced_count))
    assert final_committed_offset == produced_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_process_restart_preserves_offset_continuity(
    execution_mode: ExecutionMode,
) -> None:
    _require_kafka()
    mode_label = execution_mode.value
    topic = _topic_name(f"{mode_label}-recovery-restart")
    group_id = _topic_name(f"{mode_label}-recovery-group")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    partition = 0
    produced_count = 6
    restart_after_commit = 3

    _create_topic(admin, topic, num_partitions=1)
    _produce_partition_messages(topic, partition=partition, count=restart_after_commit)

    all_entries = []
    committed_before_restart = -1001
    final_committed_offset = -1001
    with Manager() as manager:
        shared_results = manager.list()

        recording_worker_cls = (
            _AsyncRecordingWorker
            if execution_mode == ExecutionMode.ASYNC
            else _RecordingWorker
        )

        first_worker = recording_worker_cls(
            shared_results=shared_results, label="first", sleep_ms=10.0
        )
        second_worker = recording_worker_cls(
            shared_results=shared_results, label="second", sleep_ms=10.0
        )

        first_config = _build_kafka_config(group_id)
        second_config = _build_kafka_config(group_id)
        # Keep the first runtime from buffering the whole partition before restart.
        first_config.parallel_consumer.poll_batch_size = 1
        second_config.parallel_consumer.poll_batch_size = 1
        first_poller, first_engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=first_config,
            worker_fn=first_worker,
            max_in_flight=1,
        )
        second_poller, second_engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=second_config,
            worker_fn=second_worker,
            max_in_flight=1,
        )

        await first_poller.start()
        try:
            await _wait_until(
                lambda: (
                    len(
                        {
                            entry[3]
                            for entry in list(shared_results)
                            if entry[0] == "completed" and entry[1] == "first"
                        }
                    )
                    >= restart_after_commit
                ),
                timeout_seconds=30,
                message="first poller did not process the expected pre-restart subset",
            )
            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    >= restart_after_commit
                ),
                timeout_seconds=15,
                message="committed offset did not advance before restart",
            )
            committed_before_restart = _fetch_committed_offset(
                group_id, topic, partition
            )
        finally:
            await first_poller.stop()
            await first_engine.shutdown()

        assert committed_before_restart == restart_after_commit
        _produce_partition_messages(
            topic,
            partition=partition,
            count=produced_count - restart_after_commit,
            start_sequence=restart_after_commit,
        )

        await second_poller.start()
        try:
            await _wait_until(
                lambda: (
                    len(
                        {
                            entry[3]
                            for entry in list(shared_results)
                            if entry[0] == "completed" and entry[1] == "second"
                        }
                    )
                    >= 1
                ),
                timeout_seconds=30,
                message="second poller did not process any post-restart work",
            )
            await _wait_until(
                lambda: (
                    len(
                        {
                            entry[3]
                            for entry in list(shared_results)
                            if entry[0] == "completed" and entry[2] == partition
                        }
                    )
                    >= produced_count
                ),
                timeout_seconds=30,
                message="restart scenario did not complete all produced offsets",
            )
            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    == produced_count
                ),
                timeout_seconds=15,
                message="restart scenario never committed the final safe offset",
            )
            final_committed_offset = _fetch_committed_offset(group_id, topic, partition)
            all_entries = list(shared_results)
        finally:
            if not all_entries:
                all_entries = list(shared_results)
            await second_poller.stop()
            await second_engine.shutdown()
            _delete_topic(admin, topic)

    completed_entries = [
        entry
        for entry in all_entries
        if entry[0] == "completed" and entry[2] == partition
    ]
    completed_offsets = [entry[3] for entry in completed_entries]
    second_completed_offsets = [
        entry[3] for entry in completed_entries if entry[1] == "second"
    ]
    duplicate_offsets = [
        offset for offset, count in Counter(completed_offsets).items() if count > 1
    ]

    assert committed_before_restart == restart_after_commit
    assert set(completed_offsets) == set(range(produced_count))
    post_restart_message = f"expected post-restart work, got entries={all_entries}"
    assert second_completed_offsets, post_restart_message
    assert min(second_completed_offsets) >= committed_before_restart, (
        "restart replayed offsets before the last committed position; "
        f"committed_before_restart={committed_before_restart}, "
        f"second_completed_offsets={second_completed_offsets}"
    )
    assert all(offset >= committed_before_restart for offset in duplicate_offsets), (
        "restart duplicated offsets below the committed boundary; "
        f"committed_before_restart={committed_before_restart}, "
        f"duplicate_offsets={duplicate_offsets}"
    )
    assert final_committed_offset == produced_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_process_retry_path_commits_only_after_success(
    execution_mode: ExecutionMode,
    tmp_path: Path,
) -> None:
    _require_kafka()
    mode_label = execution_mode.value
    topic = _topic_name(f"{mode_label}-recovery-retry")
    group_id = _topic_name(f"{mode_label}-recovery-group")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    partition = 0
    produced_count = 3
    target_offset = 0
    fail_first_attempts = 1

    _create_topic(admin, topic, num_partitions=1)
    _produce_partition_messages(topic, partition=partition, count=produced_count)

    attempt_log_path = tmp_path / f"{mode_label}-retry-attempts.log"
    target_attempts = 0
    final_committed_offset = -1001
    try:
        retry_worker_cls = (
            _AsyncRetryThenSucceedWorker
            if execution_mode == ExecutionMode.ASYNC
            else _RetryThenSucceedWorker
        )

        worker = retry_worker_cls(
            shared_results=None,
            label="retry",
            target_partition=partition,
            target_offset=target_offset,
            fail_first_attempts=fail_first_attempts,
            sleep_ms=1000.0,
            attempt_log_path=str(attempt_log_path),
        )
        kafka_config = _build_kafka_config(group_id)
        poller, engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=kafka_config,
            worker_fn=worker,
            max_in_flight=1,
        )
        await poller.start()
        try:

            def _target_success_attempt_started() -> bool:
                return any(
                    attempt_partition == partition
                    and attempt_offset == target_offset
                    and attempt == fail_first_attempts + 1
                    for attempt_partition, attempt_offset, attempt in _read_attempt_log(
                        attempt_log_path
                    )
                )

            await _wait_until(
                _target_success_attempt_started,
                timeout_seconds=20,
                message="retry scenario never reached the blocked success attempt",
            )
            blocked_commit = _fetch_committed_offset(group_id, topic, partition)
            assert blocked_commit in (-1001, 0), (
                "commit advanced before the retrying offset completed successfully: "
                f"offset={blocked_commit}"
            )

            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    == produced_count
                ),
                timeout_seconds=15,
                message="retry scenario never committed the final safe offset",
            )
            final_committed_offset = _fetch_committed_offset(group_id, topic, partition)
        finally:
            await poller.stop()
            await engine.shutdown()
            _delete_topic(admin, topic)
    finally:
        attempts = _read_attempt_log(attempt_log_path)

    target_attempts = max(
        attempt
        for attempt_partition, attempt_offset, attempt in attempts
        if attempt_partition == partition and attempt_offset == target_offset
    )
    assert target_attempts == fail_first_attempts + 1
    assert final_committed_offset == produced_count


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_process_dlq_path_commits_after_retry_exhaustion(
    execution_mode: ExecutionMode,
) -> None:
    _require_kafka()
    mode_label = execution_mode.value
    topic = _topic_name(f"{mode_label}-recovery-dlq")
    group_id = _topic_name(f"{mode_label}-recovery-group")
    admin = AdminClient({"bootstrap.servers": BOOTSTRAP_SERVERS})
    partition = 0
    produced_count = 3
    target_offset = 0
    dlq_topic = topic + DLQ_SUFFIX

    _create_topic(admin, topic, num_partitions=1)
    _produce_partition_messages(topic, partition=partition, count=produced_count)

    all_entries = []
    target_attempts = 0
    final_committed_offset = -1001
    result_queue: Any = Queue()
    try:
        shared_results = _QueueBackedResults(result_queue)

        always_fail_worker_cls = (
            _AsyncAlwaysFailWorker
            if execution_mode == ExecutionMode.ASYNC
            else _AlwaysFailWorker
        )

        worker = always_fail_worker_cls(
            shared_results=shared_results,
            label="dlq",
            target_partition=partition,
            target_offset=target_offset,
        )
        kafka_config = _build_kafka_config(group_id)
        poller, engine = _build_recovery_runtime(
            execution_mode=execution_mode,
            topic=topic,
            kafka_config=kafka_config,
            worker_fn=worker,
            max_in_flight=1,
        )
        max_retries = kafka_config.parallel_consumer.execution.max_retries

        await poller.start()
        try:
            await _wait_until(
                lambda: (
                    _fetch_committed_offset(group_id, topic, partition)
                    == produced_count
                ),
                timeout_seconds=20,
                message="dlq scenario never committed the final safe offset",
            )
            final_committed_offset = _fetch_committed_offset(group_id, topic, partition)
            all_entries = _drain_result_queue(result_queue)
        finally:
            all_entries.extend(_drain_result_queue(result_queue))
            await poller.stop()
            await engine.shutdown()
            all_entries.extend(_drain_result_queue(result_queue))

        try:
            dlq_msg = _consume_single_record(
                dlq_topic,
                group_id=_topic_name("process-recovery-dlq-reader"),
            )
        finally:
            _delete_topic(admin, dlq_topic)
            _delete_topic(admin, topic)
    finally:
        result_queue.close()
        result_queue.join_thread()

    completed_entries = [entry for entry in all_entries if entry[0] == "completed"]
    completed_offsets = [entry[3] for entry in completed_entries]
    target_completions = [
        entry
        for entry in completed_entries
        if entry[2] == partition and entry[3] == target_offset
    ]
    headers = dict(dlq_msg.headers() or [])
    dlq_payload = json.loads(dlq_msg.value().decode("utf-8"))
    target_attempts = int(headers["x-retry-attempt"].decode("utf-8"))

    assert target_attempts >= max_retries
    assert set(completed_offsets) == {1, 2}
    assert not target_completions
    assert dlq_msg.key() == b"key-0"
    assert dlq_payload["sequence"] == target_offset
    assert headers["x-error-reason"].startswith(b"intentional dlq trigger")
    assert headers["x-retry-attempt"] == str(max_retries).encode("utf-8")
    assert headers["source-topic"] == topic.encode("utf-8")
    assert headers["partition"] == b"0"
    assert headers["offset"] == b"0"
    assert final_committed_offset == produced_count
