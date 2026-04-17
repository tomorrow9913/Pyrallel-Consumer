# tests/e2e/test_ordering.py
import asyncio
import json
import os
import random
import sys
import time
import uuid
from collections import defaultdict
from multiprocessing import Manager
from pathlib import Path
from typing import Dict, List, Literal, Optional

import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import Consumer, KafkaError, KafkaException, NewTopic
from confluent_kafka.cimpl import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import ExecutionMode, OrderingMode, WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine

# --- Test Configuration ---
E2E_TOPIC = "e2e_ordering_test_topic"
PRODUCER_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "producer.py"
E2E_BOOTSTRAP_SERVERS = "localhost:9092"
E2E_AUTO_OFFSET_RESET: Literal["earliest"] = "earliest"
E2E_ENABLE_AUTO_COMMIT = False


def _require_kafka() -> None:
    strict_ci_gate = os.environ.get(
        "PYRALLEL_E2E_REQUIRE_BROKER", ""
    ).strip().lower() in {"1", "true", "yes", "on"}
    admin = AdminClient(
        {
            "bootstrap.servers": E2E_BOOTSTRAP_SERVERS,
            "socket.timeout.ms": 1000,
        }
    )
    try:
        metadata = admin.list_topics(timeout=5)
        if not getattr(metadata, "brokers", None):
            raise RuntimeError("Kafka metadata response did not include broker entries")
    except Exception as exc:
        message = f"Kafka broker not available for e2e tests: {exc}"
        if strict_ci_gate:
            pytest.fail(message)
        pytest.skip(message)


# --- Result Verification Helper ---
class ResultTracker:
    """처리된 메시지의 순서를 키별/파티션별로 기록하고 검증하는 헬퍼 클래스."""

    def __init__(self):
        self.results: Dict[str, List[int]] = defaultdict(list)
        self.partition_results: Dict[int, List[int]] = defaultdict(list)
        self.processed_count = 0
        self.lock = asyncio.Lock()

    async def record(self, key: str, sequence: int):
        async with self.lock:
            self.results[key].append(sequence)
            self.processed_count += 1

    async def record_partition(self, partition: int, offset: int):
        """파티션별 오프셋 순서를 기록합니다."""
        async with self.lock:
            self.partition_results[partition].append(offset)
            self.processed_count += 1

    def verify_key_hash_ordering(self):
        """KEY_HASH 모드의 순서 보장을 검증합니다.
        동일 키 내에서 sequence가 오름차순이어야 합니다."""
        for key, sequences in self.results.items():
            assert sequences == sorted(
                sequences
            ), f"Ordering failed for key {key}: {sequences}"

    def verify_partition_ordering(self):
        """PARTITION 모드의 순서 보장을 검증합니다.
        동일 파티션 내에서 오프셋이 오름차순이어야 합니다."""
        for partition, offsets in self.partition_results.items():
            assert offsets == sorted(offsets), (
                f"Ordering failed for partition {partition}: "
                f"expected sorted offsets but got {offsets}"
            )

    def get_processed_count(self) -> int:
        return self.processed_count


class _ProcessKeyHashWorker:
    """Picklable sync worker for process-mode Kafka-backed ordering tests."""

    def __init__(self, shared_results, sleep_min_ms: float = 0.0) -> None:
        self._shared_results = shared_results
        self._sleep_min_ms = sleep_min_ms

    def __call__(self, item: WorkItem) -> None:
        if self._sleep_min_ms > 0:
            time.sleep(self._sleep_min_ms / 1000.0)
        payload = json.loads(item.payload.decode("utf-8"))
        self._shared_results.append((payload["key"], payload["sequence"]))


class _ProcessPartitionWorker:
    """Picklable sync worker for process-mode partition ordering tests."""

    def __init__(self, shared_results, sleep_min_ms: float = 0.0) -> None:
        self._shared_results = shared_results
        self._sleep_min_ms = sleep_min_ms

    def __call__(self, item: WorkItem) -> None:
        if self._sleep_min_ms > 0:
            time.sleep(self._sleep_min_ms / 1000.0)
        self._shared_results.append((item.tp.partition, item.offset))


async def _wait_for_shared_results(
    shared_results,
    expected_count: int,
    timeout_seconds: float,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if len(shared_results) >= expected_count:
            return
        await asyncio.sleep(0.05)
    raise TimeoutError(
        f"Timed out waiting for {expected_count} process-mode results; "
        f"only observed {len(shared_results)}"
    )


# --- Pytest Fixtures ---
@pytest.fixture
def kafka_admin_client():
    """테스트용 Kafka AdminClient fixture."""
    return AdminClient({"bootstrap.servers": E2E_BOOTSTRAP_SERVERS})


@pytest.fixture(autouse=True)
def create_e2e_topic(kafka_admin_client: AdminClient):
    """각 테스트 전에 토픽을 삭제/재생성하여 격리합니다."""
    _require_kafka()
    topic_name = E2E_TOPIC

    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
        time.sleep(2)
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:  # type: ignore[attr-defined]
            raise

    num_partitions = 8
    topic = NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
    kafka_admin_client.create_topics([topic])[topic_name].result()
    time.sleep(1)

    yield

    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:  # type: ignore[attr-defined]
            raise


@pytest.fixture
def base_kafka_config() -> KafkaConfig:
    """테스트용 기본 KafkaConfig 객체를 생성합니다 (고유 consumer group)."""
    return KafkaConfig(
        BOOTSTRAP_SERVERS=[E2E_BOOTSTRAP_SERVERS],
        CONSUMER_GROUP=f"e2e_test_{uuid.uuid4().hex[:8]}",
        AUTO_OFFSET_RESET=E2E_AUTO_OFFSET_RESET,
        ENABLE_AUTO_COMMIT=E2E_ENABLE_AUTO_COMMIT,
    )


async def run_ordering_test(
    kafka_config: KafkaConfig,
    ordering_mode: OrderingMode,
    num_messages: int,
    num_keys: int,
    execution_mode: ExecutionMode = ExecutionMode.ASYNC,
    worker_fn=None,
    max_in_flight: Optional[int] = None,
    timeout: int = 60,
):
    stop_event = asyncio.Event()
    result_tracker = ResultTracker()

    async def default_key_worker(item: WorkItem):
        payload = json.loads(item.payload.decode("utf-8"))
        await result_tracker.record(key=payload["key"], sequence=payload["sequence"])
        if result_tracker.get_processed_count() >= num_messages:
            stop_event.set()

    async def partition_worker(item: WorkItem):
        await result_tracker.record_partition(
            partition=item.tp.partition, offset=item.offset
        )
        if result_tracker.get_processed_count() >= num_messages:
            stop_event.set()

    if worker_fn is None and execution_mode == ExecutionMode.ASYNC:
        if ordering_mode == OrderingMode.PARTITION:
            worker_fn = partition_worker
        else:
            worker_fn = default_key_worker

    effective_max_in_flight = (
        max_in_flight if max_in_flight is not None else num_messages
    )

    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.max_in_flight = effective_max_in_flight
    if execution_mode == ExecutionMode.PROCESS:
        with Manager() as manager:
            shared_results = manager.list()
            if worker_fn is None:
                if ordering_mode == OrderingMode.PARTITION:
                    worker_fn = _ProcessPartitionWorker(
                        shared_results,
                        sleep_min_ms=1.0,
                    )
                else:
                    worker_fn = _ProcessKeyHashWorker(
                        shared_results,
                        sleep_min_ms=1.0,
                    )

            execution_config.mode = ExecutionMode.PROCESS
            execution_config.process_config.process_count = 2
            execution_config.process_config.queue_size = 256
            execution_config.process_config.batch_size = 1
            execution_config.process_config.max_batch_wait_ms = 0
            process_engine = ProcessExecutionEngine(
                config=execution_config, worker_fn=worker_fn
            )
            work_manager = WorkManager(
                execution_engine=process_engine,
                max_in_flight_messages=execution_config.max_in_flight,
                ordering_mode=ordering_mode,
            )

            poller = BrokerPoller(
                consume_topic=E2E_TOPIC,
                kafka_config=kafka_config,
                execution_engine=process_engine,
                work_manager=work_manager,
            )
            poller.ORDERING_MODE = ordering_mode

            producer_process = await asyncio.create_subprocess_exec(
                sys.executable,
                str(PRODUCER_SCRIPT),
                "--num-messages",
                str(num_messages),
                "--num-keys",
                str(num_keys),
                "--num-partitions",
                "8",
                "--topic",
                E2E_TOPIC,
            )

            await poller.start()

            try:
                await _wait_for_shared_results(
                    shared_results=shared_results,
                    expected_count=num_messages,
                    timeout_seconds=timeout,
                )
            finally:
                await poller.stop()
                await process_engine.shutdown()
                await producer_process.wait()

            if ordering_mode == OrderingMode.PARTITION:
                for partition, offset in list(shared_results):
                    result_tracker.partition_results[partition].append(offset)
                    result_tracker.processed_count += 1
            else:
                for key, sequence in list(shared_results):
                    result_tracker.results[key].append(sequence)
                    result_tracker.processed_count += 1

            return result_tracker, poller

    execution_config.mode = ExecutionMode.ASYNC
    async_engine = AsyncExecutionEngine(config=execution_config, worker_fn=worker_fn)
    work_manager = WorkManager(
        execution_engine=async_engine,
        max_in_flight_messages=execution_config.max_in_flight,
        ordering_mode=ordering_mode,
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=kafka_config,
        execution_engine=async_engine,
        work_manager=work_manager,
    )
    poller.ORDERING_MODE = ordering_mode

    producer_process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(PRODUCER_SCRIPT),
        "--num-messages",
        str(num_messages),
        "--num-keys",
        str(num_keys),
        "--num-partitions",
        "8",
        "--topic",
        E2E_TOPIC,
    )

    await poller.start()

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout)
    finally:
        await poller.stop()
        await async_engine.shutdown()
        await producer_process.wait()

    return result_tracker, poller


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_key_hash_ordering(
    base_kafka_config: KafkaConfig, execution_mode: ExecutionMode
):
    """KEY_HASH 모드에서 키별 순서 보장을 테스트합니다."""
    num_messages = 2000
    num_keys = 200

    result_tracker, _ = await run_ordering_test(
        kafka_config=base_kafka_config,
        ordering_mode=OrderingMode.KEY_HASH,
        execution_mode=execution_mode,
        num_messages=num_messages,
        num_keys=num_keys,
    )

    assert result_tracker.get_processed_count() == num_messages
    result_tracker.verify_key_hash_ordering()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "execution_mode",
    [ExecutionMode.ASYNC, ExecutionMode.PROCESS],
    ids=["async", "process"],
)
async def test_partition_ordering(
    base_kafka_config: KafkaConfig, execution_mode: ExecutionMode
):
    """PARTITION 모드에서 파티션별 순서 보장을 테스트합니다."""
    num_messages = 500
    num_keys = 50

    result_tracker, _ = await run_ordering_test(
        kafka_config=base_kafka_config,
        ordering_mode=OrderingMode.PARTITION,
        execution_mode=execution_mode,
        num_messages=num_messages,
        num_keys=num_keys,
    )

    assert result_tracker.get_processed_count() == num_messages
    result_tracker.verify_partition_ordering()


@pytest.mark.asyncio
async def test_unordered(base_kafka_config: KafkaConfig):
    """UNORDERED 모드에서 모든 메시지가 처리되는지 테스트합니다."""
    num_messages = 2000
    num_keys = 200

    result_tracker, _ = await run_ordering_test(
        kafka_config=base_kafka_config,
        ordering_mode=OrderingMode.UNORDERED,
        num_messages=num_messages,
        num_keys=num_keys,
    )

    assert result_tracker.get_processed_count() == num_messages


@pytest.mark.asyncio
async def test_backpressure(base_kafka_config: KafkaConfig):
    """Backpressure pause/resume 동작을 검증합니다."""
    num_messages = 500
    num_keys = 50
    max_in_flight = 20

    stop_event = asyncio.Event()
    result_tracker = ResultTracker()

    async def slow_worker(item: WorkItem):
        payload = json.loads(item.payload.decode("utf-8"))
        await asyncio.sleep(random.uniform(0.001, 0.01))
        await result_tracker.record(key=payload["key"], sequence=payload["sequence"])
        if result_tracker.get_processed_count() >= num_messages:
            stop_event.set()

    execution_config: ExecutionConfig = base_kafka_config.parallel_consumer.execution
    execution_config.max_in_flight = max_in_flight
    engine = AsyncExecutionEngine(config=execution_config, worker_fn=slow_worker)
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=max_in_flight,
        ordering_mode=OrderingMode.KEY_HASH,
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=base_kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )
    poller.ORDERING_MODE = OrderingMode.KEY_HASH

    producer_process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(PRODUCER_SCRIPT),
        "--num-messages",
        str(num_messages),
        "--num-keys",
        str(num_keys),
        "--num-partitions",
        "8",
        "--topic",
        E2E_TOPIC,
    )

    await poller.start()

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=120)
    finally:
        await poller.stop()
        await producer_process.wait()

    assert result_tracker.get_processed_count() == num_messages
    assert poller.MAX_IN_FLIGHT_MESSAGES == max_in_flight
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == max(1, int(max_in_flight * 0.7))


@pytest.mark.asyncio
async def test_offset_commit_correctness(base_kafka_config: KafkaConfig):
    """Gap 기반 오프셋 커밋의 정확성을 검증합니다."""
    num_messages = 500
    num_keys = 50

    stop_event = asyncio.Event()
    result_tracker = ResultTracker()
    partition_max_offsets: Dict[int, int] = defaultdict(lambda: -1)

    async def random_delay_worker(item: WorkItem):
        await asyncio.sleep(random.uniform(0.001, 0.02))
        payload = json.loads(item.payload.decode("utf-8"))
        async with result_tracker.lock:
            result_tracker.results[payload["key"]].append(payload["sequence"])
            result_tracker.processed_count += 1
            if item.offset > partition_max_offsets[item.tp.partition]:
                partition_max_offsets[item.tp.partition] = item.offset
            if result_tracker.processed_count >= num_messages:
                stop_event.set()

    execution_config: ExecutionConfig = base_kafka_config.parallel_consumer.execution
    execution_config.max_in_flight = num_messages
    engine = AsyncExecutionEngine(
        config=execution_config, worker_fn=random_delay_worker
    )
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
        ordering_mode=OrderingMode.KEY_HASH,
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=base_kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )
    poller.ORDERING_MODE = OrderingMode.KEY_HASH

    producer_process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(PRODUCER_SCRIPT),
        "--num-messages",
        str(num_messages),
        "--num-keys",
        str(num_keys),
        "--num-partitions",
        "8",
        "--topic",
        E2E_TOPIC,
    )

    await poller.start()

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=120)
        # 커밋이 반영될 시간을 확보합니다
        await asyncio.sleep(2)
    finally:
        await poller.stop()
        await producer_process.wait()

    assert result_tracker.get_processed_count() == num_messages

    verify_consumer = Consumer(
        {
            "bootstrap.servers": E2E_BOOTSTRAP_SERVERS,
            "group.id": base_kafka_config.CONSUMER_GROUP,
        }
    )
    try:
        partitions_to_check = [
            KafkaTopicPartition(E2E_TOPIC, p) for p in partition_max_offsets.keys()
        ]
        committed = verify_consumer.committed(partitions_to_check, timeout=10)

        for tp_committed in committed:
            partition_id = tp_committed.partition
            committed_offset = tp_committed.offset
            if committed_offset < 0:
                continue

            max_processed = partition_max_offsets.get(partition_id, -1)
            if max_processed >= 0:
                assert committed_offset <= max_processed + 1, (
                    f"Partition {partition_id}: committed offset {committed_offset} "
                    f"exceeds max processed offset {max_processed} + 1"
                )
                assert committed_offset > 0, (
                    f"Partition {partition_id}: expected positive committed offset, "
                    f"got {committed_offset}"
                )
    finally:
        verify_consumer.close()
