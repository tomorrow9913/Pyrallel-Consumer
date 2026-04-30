from __future__ import annotations

import asyncio
import hashlib
import time
from functools import partial
from typing import Awaitable, Callable

from pyrallel_consumer.dto import WorkItem


def sleep_worker(payload: bytes, sleep_ms: float) -> None:
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def sleep_worker_async(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def sleep_worker_process(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


def cpu_worker(payload: bytes, iterations: int) -> None:
    payload.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload).digest()


async def cpu_worker_async(item: WorkItem, iterations: int) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()
    await asyncio.sleep(0)


def cpu_worker_process(item: WorkItem, iterations: int) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    digest = b""
    for _ in range(iterations):
        digest = hashlib.sha256(digest + payload_bytes).digest()


def io_worker(payload: bytes, sleep_ms: float) -> None:
    payload.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


async def io_worker_async(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    await asyncio.sleep(sleep_ms / 1000.0)


def io_worker_process(item: WorkItem, sleep_ms: float) -> None:
    payload_bytes = item.payload or b""
    payload_bytes.decode("utf-8")
    time.sleep(sleep_ms / 1000.0)


def select_workers(
    *,
    workload: str,
    sleep_ms: float,
    cpu_iterations: int,
    io_sleep_ms: float,
) -> tuple[
    Callable[[bytes], None],
    Callable[[WorkItem], Awaitable[None]],
    Callable[[WorkItem], None],
]:
    if workload == "sleep":
        return (
            partial(sleep_worker, sleep_ms=sleep_ms),
            partial(sleep_worker_async, sleep_ms=sleep_ms),
            partial(sleep_worker_process, sleep_ms=sleep_ms),
        )
    if workload == "cpu":
        return (
            partial(cpu_worker, iterations=cpu_iterations),
            partial(cpu_worker_async, iterations=cpu_iterations),
            partial(cpu_worker_process, iterations=cpu_iterations),
        )
    if workload == "io":
        return (
            partial(io_worker, sleep_ms=io_sleep_ms),
            partial(io_worker_async, sleep_ms=io_sleep_ms),
            partial(io_worker_process, sleep_ms=io_sleep_ms),
        )
    raise ValueError(f"Unknown workload: {workload}")
