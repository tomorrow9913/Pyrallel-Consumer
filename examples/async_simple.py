#!/usr/bin/env python3
"""Basic async example using PyrallelConsumer.

Demonstrates:
- PyrallelConsumer facade with async worker
- Graceful shutdown on SIGINT/SIGTERM
- Periodic metrics reporting

Usage:
    # Produce test messages first:
    uv run python benchmarks/producer.py --num-messages 1000 --num-keys 50 --topic demo

    # Run this example:
    uv run python examples/async_simple.py
"""

import asyncio
import logging
import signal

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)


async def my_async_worker(item: WorkItem) -> None:
    """Example async worker that processes a Kafka message.

    Replace this with your actual business logic (e.g., HTTP call, DB write).
    """
    logger.info(
        "Processing message: topic=%s partition=%d offset=%d key=%s",
        item.tp.topic,
        item.tp.partition,
        item.offset,
        item.key,
    )
    # Simulate async I/O work
    await asyncio.sleep(0.01)


async def main() -> None:
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = "async"
    config.parallel_consumer.execution.max_in_flight = 200

    consumer = PyrallelConsumer(
        config=config,
        worker=my_async_worker,
        topic="demo",
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def _signal_handler() -> None:
        logger.info("Shutdown signal received")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await consumer.start()
    logger.info("Consumer started. Press Ctrl+C to stop.")

    try:
        while not stop_event.is_set():
            await asyncio.sleep(5.0)
            metrics = consumer.get_metrics()
            logger.info(
                "Metrics: in_flight=%d paused=%s partitions=%d",
                metrics.total_in_flight,
                metrics.is_paused,
                len(metrics.partitions),
            )
    finally:
        logger.info("Stopping consumer...")
        await consumer.stop()
        logger.info("Consumer stopped.")


if __name__ == "__main__":
    asyncio.run(main())
