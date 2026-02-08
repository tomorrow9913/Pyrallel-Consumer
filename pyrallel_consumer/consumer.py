# -*- coding: utf-8 -*-
import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, cast  # Import cast

from confluent_kafka import Consumer, KafkaException, Message, Producer, TopicPartition
from confluent_kafka.admin import AdminClient

# Local imports aligned with project structure and prod.md's intent
from .config import KafkaConfig
from .logger import LogManager
from .offset_manager import OffsetTracker
from .worker import batch_deserialize

logger = LogManager.get_logger(__name__)

MessageProcessor = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


class OrderingMode(Enum):
    KEY_HASH = "key_hash"
    PARTITION = "partition"
    UNORDERED = "unordered"


class ParallelKafkaConsumer:
    """
    내부적으로 병렬 처리를 위한 컨슈머 클래스 (Refactored based on prod.md)
    1. 메시지 배치로 소비
    2. 배치 단위로 역직렬화 (ThreadPool + worker)
    3. 비즈니스 로직 병렬 처리 (asyncio.gather)
    4. 오프셋 관리 위임 (OffsetTracker)
    """

    def __init__(
        self,
        consume_topic: str,
        kafka_config: KafkaConfig,
        message_processor: MessageProcessor,
    ) -> None:
        # 해당 변수들은 동작 확인 이후 configurations로 이동 필요.
        self.TIME_OUT_SEC = 1
        self.BATCH_SIZE = 1000
        self.WORKER_POOL_SIZE = 8

        self.ORDERING_MODE: OrderingMode = OrderingMode.KEY_HASH
        self._consume_topic: str = consume_topic

        self._kafka_config = kafka_config
        self._message_processor = message_processor

        self.producer: Producer | None = None
        self.consumer: Consumer | None = None
        self.admin: AdminClient | None = None

        self._running = False
        self._shutdown_event = asyncio.Event()

        # 오프셋 관리를 OffsetTracker로 위임
        self._offset_tracker = OffsetTracker()

        # 역직렬화 및 워커 풀
        self._deserialization_thread_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="deserialize_worker"
        )

        # Backpressure related attributes (from T8)
        self.MAX_IN_FLIGHT_MESSAGES = 100  # Placeholder, will be configurable
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 50  # Placeholder, will be configurable
        self._is_paused: bool = False

    def _get_partition_index(self, msg: Message) -> int:
        """가상 파티션 인덱스 계산 로직"""
        # msg.key() can be None, hash(None) is TypeError. Cast to bytes if None.
        return hash(cast(bytes, msg.key() or b"")) % self.WORKER_POOL_SIZE

    async def _get_total_queued_messages(self) -> int:
        """현재 가상 파티션 큐에 대기 중인 메시지 수를 반환합니다."""
        # TODO: Proper implementation with actual virtual partition queues (T5)
        return 0  # Placeholder for now

    async def _check_backpressure(self) -> None:
        """백프레셔 로직을 확인하고 consumer.pause/resume을 호출합니다."""
        assert (
            self.consumer is not None
        ), "Consumer must be initialized for backpressure checks."

        total_in_flight = await self._offset_tracker.get_total_in_flight_count()
        total_queued = await self._get_total_queued_messages()
        current_load = total_in_flight + total_queued

        assigned_partitions = self.consumer.assignment()
        if not assigned_partitions:
            logger.debug("No partitions assigned, skipping backpressure check.")
            return

        if not self._is_paused and current_load > self.MAX_IN_FLIGHT_MESSAGES:
            logger.warning(
                f"Backpressure activated: current_load ({current_load}) > MAX_IN_FLIGHT_MESSAGES ({self.MAX_IN_FLIGHT_MESSAGES}). Pausing consumer."
            )
            self.consumer.pause(assigned_partitions)
            self._is_paused = True
        elif self._is_paused and current_load < self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME:
            logger.info(
                f"Backpressure released: current_load ({current_load}) < MIN_IN_FLIGHT_MESSAGES_TO_RESUME ({self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME}). Resuming consumer."
            )
            self.consumer.resume(assigned_partitions)
            self._is_paused = False

    async def _run_consumer(self) -> None:
        """안전한 종료와 오프셋 추적이 결합된 컨슈머 메인 루프"""
        logger.info("Starting Consumer Loop...")
        # Ensure consumer is initialized before entering the loop
        assert (
            self.consumer is not None
        ), "Kafka consumer must be initialized before running the loop."

        try:
            while self._running:
                # Add backpressure check here
                await self._check_backpressure()

                messages = await asyncio.to_thread(
                    self.consumer.consume, num_messages=self.BATCH_SIZE, timeout=0.1
                )
                if not messages:
                    continue

                active_partitions: Dict[int, int] = {}  # Type annotation added
                # 1. 오프셋 추적 시작 및 배치 분배 준비
                for msg in messages:
                    if msg.error():
                        logger.warning(f"Consumed message with error: {msg.error()}")
                        continue

                    # Store results of method calls in variables and assert on them
                    partition_id_val = msg.partition()
                    offset_val = msg.offset()

                    assert (
                        partition_id_val is not None
                    ), "Message partition cannot be None for a valid message."
                    assert (
                        offset_val is not None
                    ), "Message offset cannot be None for a valid message."

                    await self._offset_tracker.add(partition_id_val, offset_val)
                    active_partitions[partition_id_val] = max(
                        active_partitions.get(partition_id_val, -1), offset_val
                    )

                # 2. 가상 파티션으로 배치 분배
                virtual_partitions: list[list[Message]] = [
                    [] for _ in range(self.WORKER_POOL_SIZE)
                ]
                for msg in messages:
                    if msg.error():
                        continue
                    # Partition ID is guaranteed to be int here by previous asserts
                    p_idx = self._get_partition_index(msg)
                    virtual_partitions[p_idx].append(msg)

                # 3. 배치 병렬 처리
                tasks = [
                    asyncio.create_task(
                        self._process_virtual_partition_batch(
                            self._consume_topic, p_msgs
                        )
                    )
                    for p_msgs in virtual_partitions
                    if p_msgs
                ]
                if tasks:
                    await asyncio.gather(*tasks)

                # 4. 안전한 오프셋 계산 및 커밋
                if active_partitions:
                    parts_to_commit_data = await self._offset_tracker.get_safe_offsets(
                        active_partitions
                    )
                    if parts_to_commit_data:
                        parts_to_commit = [
                            TopicPartition(self._consume_topic, p_id, offset)
                            for p_id, offset in parts_to_commit_data
                        ]
                        logger.info(
                            f"Committing offsets: {[f'p{tp.partition}@{tp.offset}' for tp in parts_to_commit]}"
                        )
                        await asyncio.to_thread(
                            self.consumer.commit,
                            offsets=parts_to_commit,
                            asynchronous=False,
                        )

        except Exception as e:
            logger.error(f"Consumer loop error: {e}", exc_info=True)
        finally:
            await self._cleanup()
            self._shutdown_event.set()

    async def _process_virtual_partition_batch(
        self, topic: str, messages: list[Message]
    ) -> None:
        """가상 파티션의 메시지 배치를 처리합니다."""
        try:
            loop = asyncio.get_event_loop()
            success_batch, failed_batch = await loop.run_in_executor(
                self._deserialization_thread_pool, batch_deserialize, messages
            )

            if failed_batch:
                for raw_msg, error in failed_batch:
                    logger.error(f"Deserialization failed. Error: {error}")

            if success_batch:
                await self._message_processor(topic, success_batch)

        except Exception as e:
            logger.error(
                f"Batch processing failed for topic {topic}: {e}", exc_info=True
            )
        finally:
            # 성공/실패 여부와 관계없이 작업이 끝난 메시지의 오프셋을 트래커에서 제거
            for msg in messages:
                # Assert partition and offset are not None as this is for messages that were processed
                partition_id_val = msg.partition()
                offset_val = msg.offset()
                assert (
                    partition_id_val is not None
                ), "Message partition cannot be None for a processed message."
                assert (
                    offset_val is not None
                ), "Message offset cannot be None for a processed message."
                await self._offset_tracker.remove(partition_id_val, offset_val)

    def _delivery_report(self, err: KafkaException | None, msg: Message) -> None:
        if err is not None:
            logger.error(f"Delivery failed: {err}")
        else:
            logger.debug(f"Delivered to {msg.topic()} [{msg.partition()}]")

    async def _cleanup(self):
        """자원 해제 로직"""
        logger.info("Cleaning up Kafka resources...")
        if self.producer:
            await asyncio.to_thread(self.producer.flush, timeout=5)
        if self.consumer:
            self.consumer.close()
        self._deserialization_thread_pool.shutdown(wait=True)
        logger.info("Kafka resources closed.")

    def _on_assign(self, consumer: Consumer, partitions: list[TopicPartition]):
        logger.info(f"Partitions assigned: {partitions}")
        # TODO: Hydration logic from prod.md (T9.4)

    def _on_revoke(self, consumer: Consumer, partitions: list[TopicPartition]):
        """파티션 소유권을 잃기 전 호출됨."""
        logger.warning(f"Partitions revoked: {partitions}")
        # TODO: Final Graceful Commit logic from prod.md (T9.3)

    async def start(self) -> None:
        """컨슈머 시작"""
        try:
            p_conf = self._kafka_config.get_producer_config()
            self.producer = Producer(p_conf)

            self.admin = AdminClient(
                {"bootstrap.servers": self._kafka_config.BOOTSTRAP_SERVERS[0]}
            )

            c_conf = self._kafka_config.get_consumer_config()
            self.consumer = Consumer(c_conf)
            self.consumer.subscribe(
                [self._consume_topic],
                on_assign=self._on_assign,
                on_revoke=self._on_revoke,
            )

            self._running = True
            asyncio.create_task(self._run_consumer())

            logger.info(f"Kafka Consumer subscribed to: {self._consume_topic}")
            logger.info("Pyrallel-Consumer started successfully.")

        except Exception as e:
            logger.error(f"Failed to start Pyrallel-Consumer: {e}", exc_info=True)
            raise

    async def stop(self) -> None:
        """안전한 종료"""
        if not self._running:
            return
        logger.info("Shutdown signal received.")
        self._running = False
        await self._shutdown_event.wait()
        logger.info("Pyrallel-Consumer stopped gracefully.")
