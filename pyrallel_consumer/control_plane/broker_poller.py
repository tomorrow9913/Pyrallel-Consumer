# -*- coding: utf-8 -*-
import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, cast  # Import cast

from confluent_kafka import Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

# Local imports aligned with project structure and prod.md's intent
from ..config import KafkaConfig  # Adjusted import
from ..dto import TopicPartition as DtoTopicPartition
from ..logger import LogManager  # Adjusted import
from ..worker import batch_deserialize  # Adjusted import
from .offset_tracker import OffsetTracker  # Adjusted import

logger = LogManager.get_logger(__name__)

MessageProcessor = Callable[[str, list[dict[str, Any]]], Awaitable[None]]


class OrderingMode(Enum):
    """
    메시지 처리의 정렬 모드를 나타내는 열거형입니다.

    Args:
        Enum (_type_): 열거형 기본 클래스

    Attributes:
        KEY_HASH (str): 키 해시 기반 정렬 모드
        PARTITION (str): 파티션 기반 정렬 모드
        UNORDERED (str): 비정렬 모드
    """

    KEY_HASH = "key_hash"
    PARTITION = "partition"
    UNORDERED = "unordered"


class BrokerPoller:  # Renamed class
    """ """

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
        # self._offset_tracker = OffsetTracker() # This needs to be per topic-partition

        # OffsetTracker instances per TopicPartition
        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}

        # 역직렬화 및 워커 풀
        self._deserialization_thread_pool = ThreadPoolExecutor(
            max_workers=4, thread_name_prefix="deserialize_worker"
        )

        # Backpressure related attributes (from T8)
        self.MAX_IN_FLIGHT_MESSAGES = 100  # Placeholder, will be configurable
        self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME = 50  # Placeholder, will be configurable
        self._is_paused: bool = False

    def _get_partition_index(self, msg: Message) -> int:
        """
        가상 파티션 인덱스 계산 로직

        Args:
            msg (Message): Kafka 메시지 객체

        Returns:
            int: 가상 파티션 인덱스

        Raises:
            TypeError: msg.key()가 None일 때 발생할 수 있음
        """
        # msg.key() can be None, hash(None) is TypeError. Cast to bytes if None.
        return hash(cast(bytes, msg.key() or b"")) % self.WORKER_POOL_SIZE

    async def _get_total_queued_messages(self) -> int:
        """
        현재 가상 파티션 큐에 대기 중인 메시지 수를 반환합니다.

        Args:
            None

        Returns:
            int: 현재 가상 파티션 큐에 대기 중인 메시지 수
        """
        # TODO: Proper implementation with actual virtual partition queues (T5)
        return 0  # Placeholder for now

    async def _check_backpressure(self) -> None:
        """백프레셔 로직을 확인하고 consumer.pause/resume을 호출합니다."""
        assert (
            self.consumer is not None
        ), "Consumer must be initialized for backpressure checks."

        # Need to sum in-flight messages from all offset trackers
        total_in_flight = sum(
            ot.in_flight_count for ot in self._offset_trackers.values()
        )  # Assuming OffsetTracker has in_flight_count property
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
        """
        안전한 종료와 오프셋 추적이 결합된 컨슈머 메인 루프

        이 메서드는 비동기적으로 실행되며, 메시지를 소비하고 처리하는 동안
        오프셋을 추적하고 안전한 종료를 보장합니다.

        Args:
            None
        Returns:
            None
        Raises:
            Exception: 컨슈머 루프 중 발생하는 모든 예외
        """
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

                active_tps: Dict[DtoTopicPartition, int] = {}
                # 1. 오프셋 추적 시작 및 배치 분배 준비
                for msg in messages:
                    if msg.error():
                        logger.warning(f"Consumed message with error: {msg.error()}")
                        continue

                    # Store results of method calls in variables and assert on them
                    tp = DtoTopicPartition(msg.topic(), msg.partition())
                    offset_val = msg.offset()

                    assert (
                        offset_val is not None
                    ), "Message offset cannot be None for a valid message."

                    if tp not in self._offset_trackers:
                        logger.warning(
                            f"Received message for untracked partition {tp}. Skipping."
                        )
                        continue

                    # Pass epoch along with the offset tracking
                    offset_tracker = self._offset_trackers[tp]
                    offset_tracker.in_flight_offsets.add(offset_val)  # Track in-flight
                    offset_tracker.update_last_fetched_offset(offset_val)

                    active_tps[tp] = max(active_tps.get(tp, -1), offset_val)

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

                # 3. 배치 병렬 처리 (need to pass epoch here)
                tasks = []
                for p_msgs in virtual_partitions:
                    if p_msgs:
                        # Assuming all messages in p_msgs belong to the same topic-partition and thus same epoch
                        # This assumption needs to be carefully managed if key hashing distributes messages across TPs
                        # For now, let's just get the TP from the first message
                        first_msg = p_msgs[0]
                        tp = DtoTopicPartition(first_msg.topic(), first_msg.partition())
                        if tp in self._offset_trackers:
                            # epoch = self._offset_trackers[tp].epoch # Will add epoch to OffsetTracker
                            # tasks.append(asyncio.create_task(
                            #     self._process_virtual_partition_batch(
                            #         self._consume_topic, p_msgs, epoch # Pass epoch
                            #     )
                            # ))
                            tasks.append(
                                asyncio.create_task(
                                    self._process_virtual_partition_batch(
                                        self._consume_topic, p_msgs
                                    )
                                )
                            )

                if tasks:
                    await asyncio.gather(*tasks)

                # 4. 안전한 오프셋 계산 및 커밋
                if active_tps:
                    for tp, last_offset in active_tps.items():
                        offset_tracker = self._offset_trackers[tp]
                        offset_tracker.advance_high_water_mark()  # Advance HWM after processing

                        safe_offset_to_commit = offset_tracker.last_committed_offset

                        if safe_offset_to_commit >= 0:  # Only commit if valid offset
                            # TODO: Get metadata from MetadataEncoder
                            # commit_metadata = self._metadata_encoder.encode_metadata(offset_tracker.completed_offsets, safe_offset_to_commit)

                            logger.info(
                                f"Committing offset for {tp.topic}-{tp.partition}@{safe_offset_to_commit}"
                            )
                            # For now, without metadata, just commit the offset
                            await asyncio.to_thread(
                                self.consumer.commit,
                                offsets=[
                                    KafkaTopicPartition(
                                        tp.topic,
                                        tp.partition,
                                        safe_offset_to_commit + 1,
                                    )
                                ],  # Kafka commits next expected offset
                                asynchronous=False,
                            )

        except Exception as e:
            logger.error(f"Consumer loop error: {e}", exc_info=True)
        finally:
            await self._cleanup()
            self._shutdown_event.set()

    async def _process_virtual_partition_batch(
        self,
        topic: str,
        messages: list[Message],  # , epoch: int # Will add epoch here
    ) -> None:
        """
        가상 파티션의 메시지 배치를 처리합니다.

        Args:
            topic (str): 메시지가 속한 토픽 이름
            messages (list[Message]): 처리할 메시지 배치
            epoch (int): 현재 파티션 소유권의 세대 번호
        Returns:
            None
        Raises:
            Exception: 배치 처리 중 발생하는 모든 예외
        """
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
                tp = DtoTopicPartition(msg.topic(), msg.partition())
                offset_val = msg.offset()
                assert (
                    offset_val is not None
                ), "Message offset cannot be None for a processed message."

                if tp in self._offset_trackers:
                    offset_tracker = self._offset_trackers[tp]
                    # if epoch == offset_tracker.epoch: # Epoch Fencing check
                    offset_tracker.mark_complete(
                        offset_val
                    )  # Mark complete for specific offset
                    offset_tracker.in_flight_offsets.remove(
                        offset_val
                    )  # Remove from in-flight
                    # else:
                    # logger.warning(f"Discarding zombie message for {tp}@{offset_val} (Epoch mismatch: {epoch} vs {offset_tracker.epoch})")

    def _delivery_report(self, err: KafkaException | None, msg: Message) -> None:
        """
        프로듀서 메시지 전달 보고 콜백

        Args:
            err (KafkaException | None): 전달 오류 정보
            msg (Message): 전달된 메시지 객체
        Returns:
            None
        Raises:
            None
        """
        if err is not None:
            logger.error(f"Delivery failed: {err}")
        else:
            logger.debug(f"Delivered to {msg.topic()} [{msg.partition()}]")

    async def _cleanup(self):
        """
        Kafka 리소스 정리

        Args:
            None
        Returns:
            None
        Raises:
            None
        """
        logger.info("Cleaning up Kafka resources...")
        if self.producer:
            await asyncio.to_thread(self.producer.flush, timeout=5)
        if self.consumer:
            self.consumer.close()
        self._deserialization_thread_pool.shutdown(wait=True)
        logger.info("Kafka resources closed.")

    def _on_assign(self, consumer: Consumer, partitions: list[KafkaTopicPartition]):
        log_message = "Partitions assigned: " + ", ".join(
            [f"{tp.topic}-{tp.partition}@{tp.offset}" for tp in partitions]
        )
        logger.info(log_message)
        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(
                topic=tp_kafka.topic, partition=tp_kafka.partition
            )
            self._offset_trackers[tp_dto] = OffsetTracker(
                topic_partition=tp_dto,
                starting_offset=tp_kafka.offset,
                max_revoke_grace_ms=0,
            )
            self._offset_trackers[tp_dto].increment_epoch()
            logger.info(
                f"Initialized OffsetTracker for {tp_dto.topic}-{tp_dto.partition} with epoch {self._offset_trackers[tp_dto].epoch}"
            )

        # TODO: Hydration logic from prod.md (T9.4)

    def _on_revoke(self, consumer: Consumer, partitions: list[KafkaTopicPartition]):
        """
        파티션 리보크 핸들러로 파티션이 리보크(rebalance)될 때 호출됩니다.

        Args:
            consumer (Consumer): Kafka 컨슈머 인스턴스
            partitions (list[TopicPartition]): 리보크된 파티션 목록
        Returns:
            None
        Raises:
            None
        """
        log_message = "Partitions revoked: " + ", ".join(
            [f"{tp.topic}-{tp.partition}" for tp in partitions]
        )
        logger.warning(log_message)
        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(
                topic=tp_kafka.topic, partition=tp_kafka.partition
            )
            if tp_dto in self._offset_trackers:
                # Perform final graceful commit for revoked partitions
                offset_tracker = self._offset_trackers[tp_dto]
                offset_tracker.advance_high_water_mark()  # Ensure HWM is up-to-date
                safe_offset_to_commit = offset_tracker.last_committed_offset

                if safe_offset_to_commit >= 0:
                    logger.info(
                        f"Final graceful commit for revoked {tp_dto.topic}-{tp_dto.partition}@{safe_offset_to_commit}"
                    )
                    # In a real scenario, this would be a synchronous commit to ensure it goes through
                    # For now, we're just logging and removing the tracker
                    # TODO: Implement actual graceful commit with timeout
                    # self.consumer.commit(offsets=[KafkaTopicPartition(tp.topic, tp.partition, safe_offset_to_commit + 1)], asynchronous=False)

                # Invalidate the epoch for this partition (or simply remove the tracker)
                del self._offset_trackers[tp_dto]
                logger.info(
                    f"Removed OffsetTracker for revoked partition {tp_dto.topic}-{tp_dto.partition}"
                )

        # TODO: Final Graceful Commit logic from prod.md (T9.3)

    async def start(self) -> None:
        """
        Pyrallel-Consumer 시작

        Args:
            None
        Returns:
            None
        Raises:
            Exception: 시작 중 발생하는 모든 예외
        """
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
        """
        Pyrallel-Consumer 종료

        Args:
            None
        Returns:
            None
        Raises:
            None
        """
        if not self._running:
            return
        logger.info("Shutdown signal received.")
        self._running = False
        await self._shutdown_event.wait()
        logger.info("Pyrallel-Consumer stopped gracefully.")
