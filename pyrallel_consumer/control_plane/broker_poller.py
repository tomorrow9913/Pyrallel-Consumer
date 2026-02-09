# -*- coding: utf-8 -*-
import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, Optional, Set, cast  # Import cast

from confluent_kafka import OFFSET_INVALID, Consumer, KafkaException, Message, Producer
from confluent_kafka import TopicPartition as KafkaTopicPartition
from confluent_kafka.admin import AdminClient

from pyrallel_consumer.execution_plane.base import BaseExecutionEngine  # Added import

# Local imports aligned with project structure and prod.md's intent
from ..config import KafkaConfig  # Adjusted import
from ..dto import CompletionStatus
from ..dto import (
    PartitionMetrics,  # Added PartitionMetrics
    SystemMetrics,  # Added SystemMetrics
    TopicPartition as DtoTopicPartition,  # Added CompletionEvent, CompletionStatus
)
from ..logger import LogManager  # Adjusted import

from .metadata_encoder import MetadataEncoder
from .offset_tracker import OffsetTracker  # Adjusted import
from .work_manager import WorkManager

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
        execution_engine: BaseExecutionEngine,
        work_manager: Optional[WorkManager] = None,  # Make WorkManager injectable
    ) -> None:
        # 해당 변수들은 동작 확인 이후 configurations로 이동 필요.
        self.TIME_OUT_SEC = 1
        self.BATCH_SIZE = 1000
        self.WORKER_POOL_SIZE = 8

        self.ORDERING_MODE: OrderingMode = OrderingMode.KEY_HASH
        self._consume_topic: str = consume_topic

        self._kafka_config = kafka_config
        self._message_processor = message_processor
        self._execution_engine = execution_engine  # Stored in instance variable

        self.producer: Producer | None = None
        self.consumer: Consumer | None = None
        self.admin: AdminClient | None = None

        self._running = False
        self._shutdown_event = asyncio.Event()

        # 오프셋 관리를 OffsetTracker로 위임
        # self._offset_tracker = OffsetTracker() # This needs to be per topic-partition

        # OffsetTracker instances per TopicPartition
        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._metadata_encoder = MetadataEncoder()

        if work_manager:
            self._work_manager = work_manager
        else:
            self._work_manager = WorkManager(execution_engine=self._execution_engine)

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
        # Get virtual queue sizes from WorkManager
        virtual_queue_sizes = self._work_manager.get_virtual_queue_sizes()
        total_queued = 0

        for tp, queues in virtual_queue_sizes.items():
            for key, queue_size in queues.items():
                total_queued += queue_size

        return total_queued

    async def _check_backpressure(self) -> None:
        """백프레셔 로직을 확인하고 consumer.pause/resume을 호출합니다."""
        if self.consumer is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks.")

        # Need to sum in-flight messages from all offset trackers

        total_in_flight = (
            self._work_manager.get_total_in_flight_count()
        )  # Assuming OffsetTracker has in_flight_count property
        total_queued = await self._get_total_queued_messages()
        current_load = total_in_flight + total_queued

        assigned_partitions = self.consumer.assignment()
        if not assigned_partitions:
            logger.debug("No partitions assigned, skipping backpressure check.")
            return

        if not self._is_paused and current_load > self.MAX_IN_FLIGHT_MESSAGES:
            logger.warning(
                "Backpressure activated: current_load (%d) > MAX_IN_FLIGHT_MESSAGES (%d). Pausing consumer.",
                current_load,
                self.MAX_IN_FLIGHT_MESSAGES,
            )
            self.consumer.pause(assigned_partitions)
            self._is_paused = True
        elif self._is_paused and current_load < self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME:
            logger.info(
                "Backpressure released: current_load (%d) < MIN_IN_FLIGHT_MESSAGES_TO_RESUME (%d). Resuming consumer.",
                current_load,
                self.MIN_IN_FLIGHT_MESSAGES_TO_RESUME,
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
        if self.consumer is None:
            raise RuntimeError(
                "Kafka consumer must be initialized before running the loop."
            )

        try:
            while self._running:
                # Add backpressure check here
                await self._check_backpressure()

                # 1. 메시지 소비 및 WorkManager에 제출
                messages = await asyncio.to_thread(
                    self.consumer.consume, num_messages=self.BATCH_SIZE, timeout=0.1
                )

                if messages:
                    for msg in messages:
                        if msg.error():
                            logger.warning(
                                "Consumed message with error: %s", msg.error()
                            )
                            continue

                        if msg.topic() is None or msg.partition() is None:
                            logger.warning(
                                "Received message with None topic or partition. Skipping."
                            )
                            continue

                        tp = DtoTopicPartition(msg.topic(), msg.partition())  # type: ignore

                        offset_val = msg.offset()

                        if offset_val is None:
                            raise ValueError(
                                "Message offset cannot be None for a valid message."
                            )

                        if tp not in self._offset_trackers:
                            logger.warning(
                                "Received message for untracked partition %s. Skipping.",
                                tp,
                            )
                            continue

                        # Submit message to WorkManager
                        offset_tracker = self._offset_trackers[tp]
                        await self._work_manager.submit_message(
                            tp=tp,
                            offset=offset_val,
                            epoch=offset_tracker.get_current_epoch(),
                            key=msg.key(),
                            payload=msg.value(),
                        )
                        offset_tracker.update_last_fetched_offset(offset_val)

                    # Trigger the WorkManager to schedule any queued tasks
                    await self._work_manager.schedule()

                # 2. WorkManager로부터 완료 이벤트 폴링 및 OffsetTracker 업데이트
                completed_events = await self._work_manager.poll_completed_events()
                if completed_events:
                    for event in completed_events:
                        if event.tp in self._offset_trackers:
                            offset_tracker = self._offset_trackers[event.tp]
                            if (
                                event.epoch == offset_tracker.get_current_epoch()
                            ):  # Epoch Fencing
                                offset_tracker.mark_complete(event.offset)
                                if event.status == CompletionStatus.FAILURE:  # Fixed
                                    logger.error(
                                        "Message processing failed for %s@%d: %s",
                                        event.tp,
                                        event.offset,
                                        event.error,
                                    )
                            else:
                                logger.warning(
                                    "Discarding zombie completion event for %s@%d (Epoch mismatch: %d vs %d)",
                                    event.tp,
                                    event.offset,
                                    event.epoch,
                                    offset_tracker.get_current_epoch(),
                                )
                        else:
                            logger.warning(
                                "Received completion event for untracked partition %s. Skipping.",
                                event.tp,
                            )

                # 3. 안전한 오프셋 계산 및 커밋
                commits_to_make = []
                for tp, offset_tracker in self._offset_trackers.items():
                    # 1. Calculate potential HWM without modifying state
                    potential_hwm = offset_tracker.last_committed_offset
                    for offset in offset_tracker.completed_offsets:
                        if offset == potential_hwm + 1:
                            potential_hwm = offset
                        else:
                            break  # Gap found

                    if potential_hwm > offset_tracker.last_committed_offset:
                        # 2. If there's something to commit, prepare the commit data
                        safe_offset_to_commit = potential_hwm
                        commit_metadata = self._metadata_encoder.encode_metadata(
                            offset_tracker.completed_offsets, safe_offset_to_commit
                        )
                        commits_to_make.append(
                            (tp, safe_offset_to_commit, commit_metadata)
                        )

                if commits_to_make:
                    for tp, safe_offset, metadata in commits_to_make:
                        logger.info(
                            "Committing offset for %s-%d@%d with metadata: %s",
                            tp.topic,
                            tp.partition,
                            safe_offset,
                            metadata,
                        )
                        # Commit to Kafka
                        tp_to_commit = KafkaTopicPartition(
                            tp.topic,
                            tp.partition,
                            safe_offset + 1,  # Kafka commits next expected offset
                        )
                        tp_to_commit.metadata = metadata

                        await asyncio.to_thread(
                            self.consumer.commit,
                            offsets=[tp_to_commit],
                            asynchronous=False,
                        )

                        # Now, advance the internal state of the tracker
                        offset_tracker = self._offset_trackers[tp]

                        offset_tracker.advance_high_water_mark()

        except Exception as e:
            logger.error("Consumer loop error: %s", e, exc_info=True)
        finally:
            await self._cleanup()
            self._shutdown_event.set()

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
            logger.error("Delivery failed: %s", err)
        else:
            logger.debug("Delivered to %s [%d]", msg.topic(), msg.partition())

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

        # 1. Fetch committed offsets with metadata
        # consumer.committed() returns KafkaTopicPartition objects with offset and metadata fields populated
        committed_tps_from_kafka = consumer.committed(partitions)
        committed_map = {
            DtoTopicPartition(tp.topic, tp.partition): tp
            for tp in committed_tps_from_kafka
        }

        for tp_kafka in partitions:
            tp_dto = DtoTopicPartition(
                topic=tp_kafka.topic, partition=tp_kafka.partition
            )

            initial_completed_offsets: Set[int] = set()
            committed_offset_and_metadata = committed_map.get(tp_dto)

            # The starting offset for the OffsetTracker should be the next offset the consumer is about to fetch.
            # This comes from tp_kafka.offset from the rebalance assignment.
            # However, if there's a committed offset, we should use that to derive the last_committed_offset.

            # Decode metadata if available
            if committed_offset_and_metadata and committed_offset_and_metadata.metadata:
                metadata = committed_offset_and_metadata.metadata
                initial_completed_offsets = self._metadata_encoder.decode_metadata(
                    metadata
                )
                logger.info(
                    "Hydrating %s-%d from metadata. Decoded offsets: %s",
                    tp_dto.topic,
                    tp_dto.partition,
                    initial_completed_offsets,
                )

            # Initialize OffsetTracker
            # The `starting_offset` for the tracker refers to the first offset the consumer will fetch.
            # OffsetTracker's __init__ uses `starting_offset - 1` for last_committed_offset and last_fetched_offset by default.
            # We will correct this below based on actual committed offset.
            self._offset_trackers[tp_dto] = OffsetTracker(
                topic_partition=tp_dto,
                starting_offset=tp_kafka.offset,  # This is the next fetch offset from the rebalance protocol
                max_revoke_grace_ms=0,
                initial_completed_offsets=initial_completed_offsets,
            )

            # Adjust last_committed_offset and last_fetched_offset based on actual committed offset from Kafka.
            if (
                committed_offset_and_metadata
                and committed_offset_and_metadata.offset is not None
                and committed_offset_and_metadata.offset != OFFSET_INVALID
            ):
                # The committed_offset_and_metadata.offset is the *next* offset to fetch (i.e., last committed + 1).
                # So, the actual last committed offset is one less.
                self._offset_trackers[tp_dto].last_committed_offset = (
                    committed_offset_and_metadata.offset - 1
                )
                self._offset_trackers[tp_dto].last_fetched_offset = (
                    committed_offset_and_metadata.offset - 1
                )
            else:
                # If no committed offset (e.g., brand new consumer group or partition),
                # then the effective last committed offset is one less than the first offset to be fetched.
                self._offset_trackers[tp_dto].last_committed_offset = (
                    tp_kafka.offset - 1
                )
                self._offset_trackers[tp_dto].last_fetched_offset = tp_kafka.offset - 1
            # self._offset_trackers[tp_dto].last_committed_offset = tp_kafka.offset - 1
            # self._offset_trackers[tp_dto].last_fetched_offset = tp_kafka.offset - 1
            # print(f"--- BrokerPoller._on_assign: After assignment, last_committed_offset for {tp_dto} is {self._offset_trackers[tp_dto].last_committed_offset} ---")

            self._offset_trackers[tp_dto].increment_epoch()
            logger.info(
                "Initialized OffsetTracker for %s-%d with epoch %d",
                tp_dto.topic,
                tp_dto.partition,
                self._offset_trackers[tp_dto].epoch,
            )

    def _on_revoke(self, consumer: Consumer, partitions: list[KafkaTopicPartition]):
        """
        파티션 리보크 핸들러로 파티션이 리보크(rebalance)될 때 호출됩니다.

        Args:
            consumer (Consumer): Kafka 컨슈머 인스턴스
            partitions (list[KafkaTopicPartition]): 리보크된 파티션 목록
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
                    commit_metadata = self._metadata_encoder.encode_metadata(
                        offset_tracker.completed_offsets, safe_offset_to_commit
                    )
                    logger.info(
                        "Final graceful commit for revoked %s-%d@%d with metadata: %s",
                        tp_dto.topic,
                        tp_dto.partition,
                        safe_offset_to_commit,
                        commit_metadata,
                    )
                    # In a real scenario, this would be a synchronous commit to ensure it goes through
                    # For now, we're just logging and removing the tracker
                    # TODO: Implement actual graceful commit with timeout
                    if self.consumer:  # Ensure consumer is not None before committing
                        tp_to_commit = KafkaTopicPartition(
                            tp_dto.topic,
                            tp_dto.partition,
                            safe_offset_to_commit + 1,
                        )
                        tp_to_commit.metadata = commit_metadata

                        self.consumer.commit(
                            offsets=[tp_to_commit],
                            asynchronous=False,
                        )

                # Invalidate the epoch for this partition (or simply remove the tracker)
                del self._offset_trackers[tp_dto]
                logger.info(
                    "Removed OffsetTracker for revoked partition %s-%d",
                    tp_dto.topic,
                    tp_dto.partition,
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

            logger.info("Kafka Consumer subscribed to: %s", self._consume_topic)
            logger.info("Pyrallel-Consumer started successfully.")

        except Exception as e:
            logger.error("Failed to start Pyrallel-Consumer: %s", e, exc_info=True)
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

    def get_metrics(self) -> SystemMetrics:
        """
        시스템의 현재 상태에 대한 메트릭을 수집하여 반환합니다.

        Returns:
            SystemMetrics: 시스템 전체 및 파티션별 메트릭 정보
        """
        partition_metrics_list = []

        virtual_queue_sizes = self._work_manager.get_virtual_queue_sizes()

        for tp, tracker in self._offset_trackers.items():
            true_lag = max(
                0, tracker.last_fetched_offset - tracker.last_committed_offset
            )

            gaps = tracker.get_gaps()
            gap_count = len(gaps)

            blocking_offset: Optional[int] = None
            blocking_duration: Optional[float] = None

            if gaps:
                blocking_offset = gaps[0].start
                durations = tracker.get_blocking_offset_durations()
                blocking_duration = durations.get(blocking_offset)

            tp_queue_sizes = virtual_queue_sizes.get(tp, {})
            queued_count = sum(tp_queue_sizes.values())

            partition_metrics_list.append(
                PartitionMetrics(
                    tp=tp,
                    true_lag=true_lag,
                    gap_count=gap_count,
                    blocking_offset=blocking_offset,
                    blocking_duration_sec=blocking_duration,
                    queued_count=queued_count,
                )
            )

        return SystemMetrics(
            total_in_flight=self._work_manager.get_total_in_flight_count(),
            is_paused=self._is_paused,
            partitions=partition_metrics_list,
        )
