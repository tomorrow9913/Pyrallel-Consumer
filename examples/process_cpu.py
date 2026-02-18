#!/usr/bin/env python3
"""Basic process (multiprocessing) example using PyrallelConsumer.

Demonstrates:
- PyrallelConsumer facade with sync worker (CPU-bound)
- Micro-batching configuration
- Graceful shutdown on SIGINT/SIGTERM

Usage:
    # Produce test messages first:
    uv run python benchmarks/producer.py --num-messages 5000 --num-keys 100 --topic demo

    # Run this example:
    uv run python examples/process_cpu.py
"""

import asyncio
import hashlib
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


def my_cpu_worker(item: WorkItem) -> None:
    """Example sync worker for CPU-bound tasks.

    This runs in a separate process. Must be picklable (top-level function).
    Replace with your actual CPU-heavy logic (e.g., image processing, ML inference).
    """
    payload = item.payload
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    # Simulate CPU-bound work
    for _ in range(100):
        hashlib.sha256(payload.encode()).hexdigest()


async def main() -> None:
    config = KafkaConfig()
    config.parallel_consumer.execution.mode = "process"
    config.parallel_consumer.execution.max_in_flight = 500
    config.parallel_consumer.execution.process_config.process_count = 4
    config.parallel_consumer.execution.process_config.batch_size = 32
    config.parallel_consumer.execution.process_config.max_batch_wait_ms = 10

    consumer = PyrallelConsumer(
        config=config,
        worker=my_cpu_worker,
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
    logger.info("Consumer started (process mode). Press Ctrl+C to stop.")

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
