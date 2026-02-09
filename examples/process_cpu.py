import asyncio
import time
import logging
import math
from pyrallel_consumer.config import (
    KafkaConfig,
    ParallelConsumerConfig,
    ExecutionConfig,
    ProcessConfig,
)
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# 1. Define the CPU Bound Worker Function
# MUST be a top-level function to be picklable for multiprocessing
def cpu_heavy_worker(item: WorkItem) -> None:
    """
    Simulates a CPU bound task (e.g., heavy calculation, image processing).
    CPU 집약적인 작업을 시뮬레이션합니다 (예: 복잡한 계산, 이미지 처리).
    """
    start = time.time()

    # Simulate heavy calculation: Verify large prime number
    # This blocks the CPU core
    number = 1000003
    is_prime = True
    if number > 1:
        for i in range(2, int(math.sqrt(number)) + 1):
            if (number % i) == 0:
                is_prime = False
                break

    # Simulate some busy wait to make it noticeable
    # In real world, this would be your actual heavy logic
    end = time.time()
    while end - start < 0.1:  # Burn CPU for 100ms
        end = time.time()

    print(
        f"[Process {os.getpid()}] Processed offset {item.offset} in {end - start:.4f}s"
    )


import os  # Need import inside if used, but top level is better. Imported above.


# 2. Configuration
def get_config() -> KafkaConfig:
    return KafkaConfig(
        BOOTSTRAP_SERVERS=["localhost:9092"],
        CONSUMER_GROUP="process-example-group",
        parallel_consumer=ParallelConsumerConfig(
            execution=ExecutionConfig(
                mode="process",
                max_in_flight_messages=50,
                process_config=ProcessConfig(
                    process_count=4,  # Use 4 worker processes
                    queue_size=100,
                ),
            )
        ),
    )


# 3. Main Entry Point
async def main():
    config = get_config()
    topic = "example-topic"

    consumer = PyrallelConsumer(config=config, worker=cpu_heavy_worker, topic=topic)

    logger.info("Starting Process Consumer...")
    try:
        await consumer.start()

        while True:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        logger.info("Stopping consumer...")
    finally:
        await consumer.stop()


if __name__ == "__main__":
    # Multiprocessing safety
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
