from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import partial

from pyrallel_consumer.dto import WorkItem

from .base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata


@dataclass(frozen=True, slots=True)
class SleepOptions:
    """Represent configurable sleep workload options."""

    sleep_ms: float = field(
        default=0.5,
        metadata={
            "workload_option": WorkloadOptionMetadata(
                label="Sleep per message",
                description="Milliseconds to sleep for each message.",
                minimum=0,
                legacy_flags=("--worker-sleep-ms",),
            )
        },
    )


def sleep_worker(payload: bytes, sleep_ms: float) -> None:
    """Handle sleep worker for benchmark workload discovery."""
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def sleep_worker_async(item: WorkItem, sleep_ms: float) -> None:
    """Handle sleep worker async for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def sleep_worker_process(item: WorkItem, sleep_ms: float) -> None:
    """Handle sleep worker process for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


class SleepWorkload(BenchmarkWorkload[SleepOptions]):
    """Handle SleepWorkload for benchmark workload discovery."""

    name = "sleep"
    label = "Sleep"
    description = "Decode payloads and sleep for the configured duration."
    options_type = SleepOptions

    def baseline_worker(self, context: WorkloadContext[SleepOptions]):
        """Handle baseline worker for benchmark workload discovery."""
        return partial(sleep_worker, sleep_ms=context.options.sleep_ms)

    def async_worker(self, context: WorkloadContext[SleepOptions]):
        """Handle async worker for benchmark workload discovery."""
        return partial(sleep_worker_async, sleep_ms=context.options.sleep_ms)

    def process_worker(self, context: WorkloadContext[SleepOptions]):
        """Handle process worker for benchmark workload discovery."""
        return partial(sleep_worker_process, sleep_ms=context.options.sleep_ms)
