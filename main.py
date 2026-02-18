#!/usr/bin/env python3
"""Pyrallel Consumer — Quick Start.

This is a living document that demonstrates the simplest possible usage
of PyrallelConsumer. For more examples, see the examples/ directory.

Usage:
    uv run python main.py
"""

import asyncio
import json
import logging

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.consumer import PyrallelConsumer
from pyrallel_consumer.dto import WorkItem

logging.basicConfig(level=logging.INFO)


async def worker(item: WorkItem) -> None:
    payload = item.payload
    if isinstance(payload, bytes):
        payload = json.loads(payload.decode("utf-8"))
    print("offset=%d key=%s payload=%s" % (item.offset, item.key, payload))


async def main() -> None:
    config = KafkaConfig()
    consumer = PyrallelConsumer(config=config, worker=worker, topic="demo")
    await consumer.start()
    try:
        await asyncio.sleep(30)
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
