# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/_process_execution_engine_support.py
# Role: Provides shared imports, fakes, and helpers for process execution engine unit tests.
# Extend here when split process execution engine tests need shared fakes or imports.
import asyncio
import logging
import queue
import threading
import time
from collections import deque
from collections.abc import AsyncGenerator
from multiprocessing.connection import Connection
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import msgpack  # type: ignore[import-untyped]
import pytest
import pytest_asyncio

from pyrallel_consumer.config import ExecutionConfig, ProcessConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    EngineWorkerDiagnostics,
    ExecutionMode,
    RouteBatch,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane import process_engine as process_engine_module
from pyrallel_consumer.execution_plane import (
    process_transport_worker_pipes as worker_pipes_module,
)
from pyrallel_consumer.execution_plane import (
    process_worker_runtime as worker_runtime_module,
)
from pyrallel_consumer.execution_plane.process_codec import (
    batch_completion_from_dict,
    decode_worker_pipe_payload,
    route_batch_from_dict,
)
from pyrallel_consumer.execution_plane.process_engine import (
    ProcessExecutionEngine,
    _completion_event_from_dict,
    _completion_event_to_dict,
    _serialize_batch_payload,
    _work_item_from_dict,
    _work_item_to_dict,
    _worker_loop,
)
from pyrallel_consumer.execution_plane.process_transport import (
    PendingDispatchRecovery,
    RouteIdentity,
    WorkerExecutionIdentity,
    logical_work_identity_from_payload,
    stable_worker_index_for_route,
)
from pyrallel_consumer.execution_plane.process_transport_worker_pipes import (
    WorkerPipesProcessTransport,
)
from tests.unit.execution_plane.test_execution_engine_contract import (
    BaseExecutionEngineContractTest,
)

__all__ = (
    "Any",
    "AsyncGenerator",
    "AsyncMock",
    "BaseExecutionEngineContractTest",
    "CompletionEvent",
    "CompletionStatus",
    "Connection",
    "EngineWorkerDiagnostics",
    "ExecutionConfig",
    "ExecutionMode",
    "Mock",
    "PendingDispatchRecovery",
    "ProcessConfig",
    "ProcessExecutionEngine",
    "RouteBatch",
    "RouteIdentity",
    "TopicPartition",
    "WorkItem",
    "WorkerExecutionIdentity",
    "WorkerPipesProcessTransport",
    "_BrokenPipeSender",
    "_Closable",
    "_CountingAliveWorker",
    "_DeadWorker",
    "_ExplodingSerializer",
    "_FakeProcess",
    "_JoinedWorker",
    "_PipeSender",
    "_RequeueRecordingTransport",
    "_async_worker",
    "_completion_event_from_dict",
    "_completion_event_to_dict",
    "_contract_worker",
    "_serialize_batch_payload",
    "_sync_worker",
    "_work_item_from_dict",
    "_work_item_to_dict",
    "_worker_loop",
    "asyncio",
    "batch_completion_from_dict",
    "cast",
    "decode_worker_pipe_payload",
    "deque",
    "logging",
    "logical_work_identity_from_payload",
    "msgpack",
    "process_engine_module",
    "pytest",
    "pytest_asyncio",
    "queue",
    "route_batch_from_dict",
    "stable_worker_index_for_route",
    "threading",
    "time",
    "worker_pipes_module",
    "worker_runtime_module",
)


class _DeadWorker:
    exitcode = 1

    def is_alive(self) -> bool:
        return False


class _CountingAliveWorker:
    exitcode = None

    def __init__(self) -> None:
        self.is_alive_calls = 0

    def is_alive(self) -> bool:
        self.is_alive_calls += 1
        return True


class _BrokenPipeSender:
    def send_bytes(self, _payload: bytes) -> None:
        raise BrokenPipeError("boom")


async def _async_worker(_item) -> None:
    return None


def _sync_worker(_item) -> None:
    return None


def _contract_worker(item: WorkItem) -> None:
    if item.payload == b"fail":
        raise ValueError("simulated worker failure")


class _PipeSender:
    def __init__(self) -> None:
        self.payloads: list[bytes] = []
        self.closed = False

    def send_bytes(self, payload: bytes) -> None:
        self.payloads.append(payload)

    def close(self) -> None:
        self.closed = True


class _ExplodingSerializer:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __call__(self, _batch: list[WorkItem], _flush_enqueued_at: float) -> bytes:
        return self.payload


class _FakeProcess:
    def __init__(self, target=None, args=()) -> None:
        self.target = target
        self.args = args
        self.pid = 4321
        self.started = False

    def start(self) -> None:
        self.started = True


class _JoinedWorker:
    pid = 9876
    exitcode = 0

    def __init__(self) -> None:
        self.join_calls = 0

    def join(self, timeout: float | None = None) -> None:
        del timeout
        self.join_calls += 1

    def is_alive(self) -> bool:
        return False


class _Closable:
    def __init__(self) -> None:
        self.closed = False
        self.stopped = False

    def close(self) -> None:
        self.closed = True

    def stop(self) -> None:
        self.stopped = True


class _RequeueRecordingTransport:
    def __init__(self) -> None:
        self.requeued_payloads: list[list[dict[str, Any]]] = []

    async def submit_work_item(
        self,
        work_item: WorkItem,
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        del work_item, route_identity, count_in_flight

    def dispatch_payload(
        self,
        payload: dict[str, Any],
        *,
        route_identity: RouteIdentity,
        count_in_flight: bool,
    ) -> None:
        del payload, route_identity, count_in_flight

    def start_worker_task_source(self, idx: int) -> tuple[Any, bool]:
        del idx
        return object(), False

    def handle_registry_event(self, event: dict[str, Any]) -> None:
        del event

    def recover_pending_dispatches(self, idx: int) -> list[PendingDispatchRecovery]:
        del idx
        return []

    def signal_shutdown(self, worker_count: int) -> None:
        del worker_count

    def close(self) -> None:
        return None

    def requeue_payloads(self, payloads: list[dict[str, Any]]) -> None:
        self.requeued_payloads.append(payloads)

    def clear_pending_dispatches(self) -> None:
        return None
