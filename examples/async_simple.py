import asyncio
import os
import random
import logging
from pyrallel_consumer.config import (
    KafkaConfig,
    ParallelConsumerConfig,
    ExecutionConfig,
    AsyncConfig,
)
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 1. Define the Async Worker Function
async def async_io_worker(item: WorkItem) -> None:
    """
    Simulates an I/O bound task (e.g., calling an external API, DB query).
    비동기 I/O 작업을 시뮬레이션합니다 (예: 외부 API 호출, DB 쿼리).
    """
    # Simulate I/O latency (random sleep between 100ms and 500ms)
    sleep_time = random.uniform(0.1, 0.5)
    await asyncio.sleep(sleep_time)

    logger.info(
        f"Processed message {item.offset} from partition {item.tp.partition} in {sleep_time:.2f}s"
    )
    # You can access the payload via item.payload (bytes)
    # payload_str = item.payload.decode('utf-8')


# 2. Configuration
def get_config() -> KafkaConfig:
    return KafkaConfig(
        BOOTSTRAP_SERVERS=["localhost:9092"],
        CONSUMER_GROUP="async-example-group",
        parallel_consumer=ParallelConsumerConfig(
            execution=ExecutionConfig(
                mode="async",
                max_in_flight_messages=100,  # Total system limit
                async_config=AsyncConfig(
                    max_concurrent_tasks=50,  # Engine limit
                    task_timeout_ms=5000,
                ),
            )
        ),
    )


# 3. Main Entry Point
async def main():
    config = get_config()
    topic = "example-topic"

    consumer = PyrallelConsumer(config=config, worker=async_io_worker, topic=topic)

    logger.info("Starting Async Consumer...")
    try:
        await consumer.start()

        # Keep the main loop running
        while True:
            await asyncio.sleep(1)
            # Optional: Print metrics periodically
            # metrics = consumer.get_metrics()
            # print(f"Lag: {metrics.partitions[0].true_lag if metrics.partitions else 0}")

    except KeyboardInterrupt:
        logger.info("Stopping consumer...")
    finally:
        await consumer.stop()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
