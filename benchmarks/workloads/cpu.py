from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from functools import partial

from pyrallel_consumer.dto import WorkItem

from .base import BenchmarkWorkload, WorkloadContext, WorkloadOptionMetadata


@dataclass(frozen=True, slots=True)
class CpuOptions:
    """Represent configurable CPU workload options."""

    iterations: int = field(
        default=1000,
        metadata={
            "workload_option": WorkloadOptionMetadata(
                label="CPU iterations",
                description="SHA-256 hashing iterations per message.",
                minimum=0,
                legacy_flags=("--worker-cpu-iterations",),
            )
        },
    )


def cpu_worker(payload: bytes, iterations: int) -> None:
    """Handle cpu worker for benchmark workload discovery."""
    payload.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload).digest()


async def cpu_worker_async(item: WorkItem, iterations: int) -> None:
    """Handle cpu worker async for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()
    await asyncio.sleep(0)


def cpu_worker_process(item: WorkItem, iterations: int) -> None:
    """Handle cpu worker process for benchmark workload discovery."""
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()


class CpuWorkload(BenchmarkWorkload[CpuOptions]):
    """Handle CpuWorkload for benchmark workload discovery."""

    name = "cpu"
    label = "CPU"
    description = "Decode payloads and perform SHA-256 hashing iterations."
    options_type = CpuOptions

    def baseline_worker(self, context: WorkloadContext[CpuOptions]):
        """Handle baseline worker for benchmark workload discovery."""
        return partial(cpu_worker, iterations=context.options.iterations)

    def async_worker(self, context: WorkloadContext[CpuOptions]):
        """Handle async worker for benchmark workload discovery."""
        return partial(cpu_worker_async, iterations=context.options.iterations)

    def process_worker(self, context: WorkloadContext[CpuOptions]):
        """Handle process worker for benchmark workload discovery."""
        return partial(cpu_worker_process, iterations=context.options.iterations)
