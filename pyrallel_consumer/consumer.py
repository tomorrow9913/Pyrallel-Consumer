import asyncio
from typing import Any, Awaitable, Callable, Optional, Union

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import SystemMetrics, WorkItem
from pyrallel_consumer.execution_plane.engine_factory import create_execution_engine


class PyrallelConsumer:
    """
    High-level facade for Pyrallel Consumer.
    Simplifies the initialization and management of the parallel consumption components.

    Attributes:
        config (KafkaConfig): Configuration for Kafka and Execution Engine.
        worker (Callable): User-defined worker function.
    """

    def __init__(
        self,
        config: KafkaConfig,
        worker: Union[Callable[[WorkItem], Awaitable[Any]], Callable[[WorkItem], Any]],
        topic: str,
    ):
        """
        Initialize the Pyrallel Consumer.

        Args:
            config (KafkaConfig): Configuration object.
            worker (Callable): The worker function to execute for each message.
                               For 'async' mode, this must be an async function.
                               For 'process' mode, this must be a picklable function.
            topic (str): The Kafka topic to subscribe to.
        """
        self.config = config
        self._topic = topic

        # 1. Create Execution Engine
        self._execution_engine = create_execution_engine(
            config.parallel_consumer.execution, worker
        )

        # 2. Create Work Manager
        # Using the control plane max_in_flight limit
        self._work_manager = WorkManager(
            execution_engine=self._execution_engine,
            max_in_flight_messages=config.parallel_consumer.execution.max_in_flight_messages,
        )

        # 3. Create Broker Poller (The main loop)
        # MessageProcessor in BrokerPoller is conceptually different from the Worker.
        # BrokerPoller uses MessageProcessor for raw handling, but in our architecture,
        # it submits to WorkManager. So we might pass a dummy or None if allowed,
        # but BrokerPoller currently requires it.
        # However, looking at BrokerPoller implementation, it submits to WorkManager directly.
        # The `message_processor` arg in BrokerPoller seems legacy or unused in the new flow?
        # Let's check BrokerPoller again. It stores it but doesn't seem to use it in `_run_consumer`.
        # It calls `self._work_manager.submit_message`.
        # We will pass a no-op dummy for now to satisfy the type signature.

        async def dummy_processor(topic: str, batch: list[dict[str, Any]]) -> None:
            pass

        self._poller = BrokerPoller(
            consume_topic=topic,
            kafka_config=config,
            message_processor=dummy_processor,
            execution_engine=self._execution_engine,
            work_manager=self._work_manager,
        )

    async def start(self) -> None:
        """
        Start the consumer.
        This method starts the BrokerPoller loop and the Execution Engine (if needed).
        """
        await self._poller.start()

    async def stop(self) -> None:
        """
        Stop the consumer gracefully.
        """
        await self._poller.stop()
        await self._execution_engine.shutdown()

    def get_metrics(self) -> SystemMetrics:
        """
        Get current system metrics.
        """
        return self._poller.get_metrics()
