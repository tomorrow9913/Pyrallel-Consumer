# tests/e2e/test_ordering.py
import asyncio
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import pytest
from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import Consumer, KafkaError, KafkaException, NewTopic
from confluent_kafka.cimpl import TopicPartition as KafkaTopicPartition

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller, OrderingMode
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine


async def _dummy_message_processor(topic: str, batch: List[dict[str, Any]]) -> None:
    return


# --- Test Configuration ---
E2E_TOPIC = "e2e_ordering_test_topic"
PRODUCER_SCRIPT = Path(__file__).resolve().parents[2] / "benchmarks" / "producer.py"
E2E_CONF = {
    "bootstrap.servers": "localhost:9092",
    "group.id": "e2e_test_group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
}


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


# --- Pytest Fixtures ---
@pytest.fixture(scope="module")
def kafka_admin_client():
    """테스트용 Kafka AdminClient fixture."""
    return AdminClient({"bootstrap.servers": E2E_CONF["bootstrap.servers"]})


@pytest.fixture(scope="module", autouse=True)
def create_e2e_topic(kafka_admin_client: AdminClient):
    """테스트 시작 시 토픽을 정리하고, 종료 시 다시 정리합니다."""
    topic_name = E2E_TOPIC

    # Clean up before test
    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
        print(f"Topic '{topic_name}' deleted before test run.")
        time.sleep(1)
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise

    # Create topic for test
    num_partitions = 8
    topic = NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
    kafka_admin_client.create_topics([topic])[topic_name].result()
    print(
        f"Topic '{topic_name}' with {num_partitions} partitions created for E2E tests."
    )

    yield

    # Clean up after test
    try:
        kafka_admin_client.delete_topics([topic_name])[topic_name].result(timeout=5)
        print(f"Topic '{topic_name}' deleted after E2E tests.")
    except KafkaException as e:
        if e.args[0].code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
            raise


@pytest.fixture
def base_kafka_config() -> KafkaConfig:
    """테스트용 기본 KafkaConfig 객체를 생성합니다."""
    return KafkaConfig(
        BOOTSTRAP_SERVERS=[E2E_CONF["bootstrap.servers"]],
        CONSUMER_GROUP=E2E_CONF["group.id"],
        AUTO_OFFSET_RESET=E2E_CONF["auto.offset.reset"],
        ENABLE_AUTO_COMMIT=E2E_CONF["enable.auto.commit"],
    )


async def run_ordering_test(
    kafka_config: KafkaConfig,
    ordering_mode: OrderingMode,
    num_messages: int,
    num_keys: int,
    worker_fn=None,
    max_in_flight: Optional[int] = None,
    timeout: int = 60,
    message_processor: Optional[
        Callable[[str, List[dict[str, Any]]], Awaitable[None]]
    ] = None,
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

    if worker_fn is None:
        if ordering_mode == OrderingMode.PARTITION:
            worker_fn = partition_worker
        else:
            worker_fn = default_key_worker

    effective_max_in_flight = (
        max_in_flight if max_in_flight is not None else num_messages
    )

    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.max_in_flight = effective_max_in_flight
    engine = AsyncExecutionEngine(config=execution_config, worker_fn=worker_fn)
    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
        message_processor=message_processor or _dummy_message_processor,
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
        await producer_process.wait()

    return result_tracker, poller


@pytest.mark.asyncio
async def test_key_hash_ordering(base_kafka_config: KafkaConfig):
    """KEY_HASH 모드에서 키별 순서 보장을 테스트합니다."""
    num_messages = 10000
    num_keys = 100

    result_tracker, _ = await run_ordering_test(
        kafka_config=base_kafka_config,
        ordering_mode=OrderingMode.KEY_HASH,
        num_messages=num_messages,
        num_keys=num_keys,
    )

    assert result_tracker.get_processed_count() == num_messages
    result_tracker.verify_key_hash_ordering()


@pytest.mark.asyncio
async def test_partition_ordering(base_kafka_config: KafkaConfig):
    """PARTITION 모드에서 파티션별 순서 보장을 테스트합니다."""
    num_messages = 10000
    num_keys = 100

    result_tracker, _ = await run_ordering_test(
        kafka_config=base_kafka_config,
        ordering_mode=OrderingMode.PARTITION,
        num_messages=num_messages,
        num_keys=num_keys,
    )

    assert result_tracker.get_processed_count() == num_messages
    result_tracker.verify_partition_ordering()


@pytest.mark.asyncio
async def test_unordered(base_kafka_config: KafkaConfig):
    """UNORDERED 모드에서 모든 메시지가 처리되는지 테스트합니다."""
    num_messages = 10000
    num_keys = 100

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
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=base_kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
        message_processor=_dummy_message_processor,
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
    assert poller.MIN_IN_FLIGHT_MESSAGES_TO_RESUME == max_in_flight // 2


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
    )

    poller = BrokerPoller(
        consume_topic=E2E_TOPIC,
        kafka_config=base_kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
        message_processor=_dummy_message_processor,
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
            "bootstrap.servers": E2E_CONF["bootstrap.servers"],
            "group.id": E2E_CONF["group.id"],
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
