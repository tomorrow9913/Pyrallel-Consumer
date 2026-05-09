# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_shutdown_support.py
# Role: Coordinates process-engine shutdown drains, worker joins, and resource cleanup.
# Extend here for parent-side shutdown mechanics; keep submit/recovery orchestration in process_engine.
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass
from typing import Any, Deque

from pyrallel_consumer.dto import CompletionEvent
from pyrallel_consumer.execution_plane.process_codec import SerializedWorkItem

_SHUTDOWN_DRAIN_SLEEP_SECONDS = 0.01
_POST_JOIN_SHUTDOWN_DRAIN_SECONDS = 0.05
_POST_JOIN_SHUTDOWN_STABLE_EMPTY_PASSES = 2

InFlightRegistryKey = tuple[int, str, int, int]
InFlightRegistry = MutableMapping[InFlightRegistryKey, SerializedWorkItem]
DrainOnce = Callable[[], tuple[int, int]]
Sleep = Callable[[float], Awaitable[None]]


@dataclass(slots=True)
class ProcessShutdownContext:
    """Runtime state needed to perform a process-engine shutdown."""

    workers: Sequence[Any]
    batch_accumulator: Any
    transport: Any
    task_queue: Any
    completion_queue: Any
    registry_event_queue: Any
    log_listener: Any
    prefetched_completion_events: Deque[CompletionEvent]
    in_flight_registry: InFlightRegistry
    worker_pid_by_index: Mapping[int, Any]
    drain_registry_events: Callable[[], None]
    drain_shutdown_ipc_once: DrainOnce
    join_worker: Callable[[Any], None]
    set_in_flight_count: Callable[[int], None]


class ProcessShutdownSupport:
    """Shutdown helper for process execution engine resources."""

    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger

    async def shutdown(
        self,
        context: ProcessShutdownContext,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        """Run the shutdown sequence once the engine has been marked shutting down."""
        context.drain_registry_events()
        self._log_shutdown_start(context)
        context.batch_accumulator.close()
        context.transport.signal_shutdown(len(context.workers))

        (
            total_registry_drained,
            total_completion_drained,
        ) = await self._drain_before_join(
            context,
            monotonic=monotonic,
            sleep=sleep,
        )
        self._logger.debug(
            "ProcessExecutionEngine shutdown pre-join drain: registry_events=%d completion_events=%d residual_in_flight_registry=%d",
            total_registry_drained,
            total_completion_drained,
            len(context.in_flight_registry),
        )

        for worker in context.workers:
            context.join_worker(worker)

        (
            post_join_registry_drained,
            post_join_completion_drained,
            post_join_drain_passes,
        ) = await self.drain_until_stable_empty(
            drain_once=context.drain_shutdown_ipc_once,
            max_seconds=_POST_JOIN_SHUTDOWN_DRAIN_SECONDS,
            stable_empty_passes=_POST_JOIN_SHUTDOWN_STABLE_EMPTY_PASSES,
            monotonic=monotonic,
            sleep=sleep,
        )
        self._logger.debug(
            "ProcessExecutionEngine shutdown post-join drain: registry_events=%d completion_events=%d passes=%d residual_in_flight_registry=%d",
            post_join_registry_drained,
            post_join_completion_drained,
            post_join_drain_passes,
            len(context.in_flight_registry),
        )
        self._warn_residual_registry(context)

        context.in_flight_registry.clear()
        context.transport.clear_pending_dispatches()
        context.set_in_flight_count(len(context.prefetched_completion_events))

        self._logger.debug("ProcessExecutionEngine shutdown complete.")
        context.log_listener.stop()
        self.close_queues(
            context.task_queue,
            context.completion_queue,
            context.registry_event_queue,
        )
        context.transport.close()

    def join_worker_with_escalation(
        self,
        worker: Any,
        *,
        timeout_seconds: float,
    ) -> None:
        """Join a worker and escalate from terminate to kill when needed."""
        worker.join(timeout=timeout_seconds)
        if not worker.is_alive():
            return

        self._logger.warning(
            "ProcessWorker[%s] did not shut down gracefully after %.3fs. Terminating.",
            worker.pid,
            timeout_seconds,
        )
        worker.terminate()
        worker.join(timeout=timeout_seconds)
        if not worker.is_alive():
            return

        kill = getattr(worker, "kill", None)
        if callable(kill):
            self._logger.warning(
                "ProcessWorker[%s] still alive after terminate(). Killing.",
                worker.pid,
            )
            kill()
            worker.join(timeout=timeout_seconds)

    async def drain_until_stable_empty(
        self,
        *,
        drain_once: DrainOnce,
        max_seconds: float,
        stable_empty_passes: int,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> tuple[int, int, int]:
        """Drain IPC until consecutive empty passes prove shutdown stability."""
        deadline = monotonic() + max(0.0, max_seconds)
        hard_deadline = deadline + (
            _SHUTDOWN_DRAIN_SLEEP_SECONDS * max(1, stable_empty_passes + 2)
        )
        total_registry_drained = 0
        total_completion_drained = 0
        total_passes = 0
        empty_passes = 0

        while True:
            drained_registry, drained_completion = drain_once()
            total_registry_drained += drained_registry
            total_completion_drained += drained_completion
            total_passes += 1

            if drained_registry == 0 and drained_completion == 0:
                empty_passes += 1
            else:
                empty_passes = 0

            if empty_passes >= stable_empty_passes:
                break
            now = monotonic()
            remaining_seconds = deadline - now
            if remaining_seconds > 0:
                sleep_seconds = min(
                    _SHUTDOWN_DRAIN_SLEEP_SECONDS,
                    remaining_seconds,
                )
            else:
                remaining_grace_seconds = hard_deadline - now
                if remaining_grace_seconds <= 0:
                    break
                sleep_seconds = min(
                    _SHUTDOWN_DRAIN_SLEEP_SECONDS,
                    remaining_grace_seconds,
                )
            await sleep(sleep_seconds)

        return total_registry_drained, total_completion_drained, total_passes

    @staticmethod
    def close_queues(*queue_objects: Any) -> None:
        """Close queue-like objects when they expose a close method."""
        for queue_obj in queue_objects:
            if queue_obj is None:
                continue
            close = getattr(queue_obj, "close", None)
            if callable(close):
                close()

    async def _drain_before_join(
        self,
        context: ProcessShutdownContext,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> tuple[int, int]:
        shutdown_drain_deadline = monotonic() + 1.0
        total_registry_drained = 0
        total_completion_drained = 0
        while monotonic() < shutdown_drain_deadline:
            drained_registry, drained_completion = context.drain_shutdown_ipc_once()
            total_registry_drained += drained_registry
            total_completion_drained += drained_completion
            if (
                drained_registry == 0
                and drained_completion == 0
                and not context.in_flight_registry
            ):
                break
            await sleep(_SHUTDOWN_DRAIN_SLEEP_SECONDS)
        return total_registry_drained, total_completion_drained

    def _log_shutdown_start(self, context: ProcessShutdownContext) -> None:
        self._logger.debug(
            "Initiating ProcessExecutionEngine shutdown. prefetched_completion_events=%d in_flight_registry=%d worker_count=%d",
            len(context.prefetched_completion_events),
            len(context.in_flight_registry),
            len(context.workers),
        )

    def _warn_residual_registry(self, context: ProcessShutdownContext) -> None:
        if not context.in_flight_registry:
            return
        registry_summary = []
        for (worker_idx, topic, partition, offset), payload in sorted(
            context.in_flight_registry.items(),
            key=lambda item: item[0],
        ):
            registry_summary.append(
                "%d(pid=%s):%s-%d@%d id=%s epoch=%s timed_out=%s attempts=%s"
                % (
                    worker_idx,
                    context.worker_pid_by_index.get(worker_idx),
                    topic,
                    partition,
                    offset,
                    payload.get("id", ""),
                    payload.get("epoch", 0),
                    payload.get("timed_out", False),
                    payload.get("requeue_attempts", 0),
                )
            )
        self._logger.warning(
            "Residual in-flight registry after shutdown drain: %s",
            "; ".join(registry_summary),
        )
