from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from functools import partial

from pyrallel_consumer.dto import WorkItem

from .base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata


@dataclass(frozen=True, slots=True)
class IoOptions:
    """Represent configurable I/O workload options."""

    sleep_ms: float = field(
        default=0.5,
        metadata={
            "workload_option": WorkloadOptionMetadata(
                label="I/O sleep per message",
                description="Milliseconds to await for each message.",
                minimum=0,
                legacy_flags=("--worker-io-sleep-ms",),
            )
        },
    )


def io_worker(payload: bytes, sleep_ms: float) -> None:
    """Handle io worker for benchmark workload discovery."""
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def io_worker_async(item: WorkItem, sleep_ms: float) -> None:
    """Handle io worker async for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def io_worker_process(item: WorkItem, sleep_ms: float) -> None:
    """Handle io worker process for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


class IoWorkload(BenchmarkWorkload[IoOptions]):
    """Handle IoWorkload for benchmark workload discovery."""

    name = "io"
    label = "I/O"
    description = "Decode payloads and simulate I/O latency with sleep."
    options_type = IoOptions

    def baseline_worker(self, context: WorkloadContext[IoOptions]):
        """Handle baseline worker for benchmark workload discovery."""
        return partial(io_worker, sleep_ms=context.options.sleep_ms)

    def async_worker(self, context: WorkloadContext[IoOptions]):
        """Handle async worker for benchmark workload discovery."""
        return partial(io_worker_async, sleep_ms=context.options.sleep_ms)

    def process_worker(self, context: WorkloadContext[IoOptions]):
        """Handle process worker for benchmark workload discovery."""
        return partial(io_worker_process, sleep_ms=context.options.sleep_ms)
