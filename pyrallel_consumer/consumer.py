from typing import Any, Awaitable, Callable, Union

from pyrallel_consumer.config import KafkaConfig, ParallelConsumerConfig
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

        parallel_config = getattr(config, "parallel_consumer", None)
        if parallel_config is None:
            parallel_config = ParallelConsumerConfig()
            setattr(self.config, "parallel_consumer", parallel_config)

        execution_config = parallel_config.execution

        # 1. Create Execution Engine
        self._execution_engine = create_execution_engine(execution_config, worker)

        # 2. Create Work Manager
        self._work_manager = WorkManager(
            execution_engine=self._execution_engine,
            max_in_flight_messages=execution_config.max_in_flight_messages,
        )

        # 3. Create Broker Poller (The main loop)
        self._poller = BrokerPoller(
            consume_topic=topic,
            kafka_config=config,
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
