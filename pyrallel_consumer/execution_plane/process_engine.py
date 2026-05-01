# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_engine.py
# Role: Coordinates multiprocessing workers, process transports, batching, recovery, and runtime metrics.
# Extend here for process-engine orchestration; split focused helpers when IPC logic grows.
from __future__ import annotations

import asyncio
import inspect
import logging
import logging.handlers
import pickle
import queue
import threading
import time
from collections import deque
from collections.abc import Callable
from multiprocessing import Process, Queue
from typing import Any, Deque, List, Optional

import msgpack  # type: ignore[import-untyped]

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    EngineRuntimeDiagnostics,
    ProcessBatchMetrics,
    ProcessRuntimeDiagnostics,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_batching import (
    BatchAccumulator as _BatchAccumulator,
)
from pyrallel_consumer.execution_plane.process_batching import (
    NoOpBatchAccumulator as _NoOpBatchAccumulator,
)
from pyrallel_consumer.execution_plane.process_codec import SerializedWorkItem
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_from_dict as _completion_event_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_to_dict as _completion_event_to_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    decode_incoming_item as _decode_incoming_item,
)
from pyrallel_consumer.execution_plane.process_codec import (
    decode_incoming_payloads as _decode_incoming_payloads,
)
from pyrallel_consumer.execution_plane.process_codec import (
    normalize_decoded_payloads as _normalize_decoded_payloads,
)
from pyrallel_consumer.execution_plane.process_codec import (
    serialize_batch_payload as _serialize_batch_payload,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_from_dict as _work_item_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    work_item_to_dict as _work_item_to_dict,
)
from pyrallel_consumer.execution_plane.process_registry_support import (
    ProcessRegistrySupport,
)
from pyrallel_consumer.execution_plane.process_transport import (
    ProcessTransport,
    resolve_route_identity,
)
from pyrallel_consumer.execution_plane.process_transport_shared_queue import (
    SharedQueueProcessTransport,
)
from pyrallel_consumer.execution_plane.process_transport_worker_pipes import (
    WorkerPipesProcessTransport,
)
from pyrallel_consumer.execution_plane.process_worker_runtime import (
    _PIPE_SENTINEL,
    _SENTINEL,
    _calculate_backoff,
    _receive_task_payload,
    _worker_loop,
)
from pyrallel_consumer.logger import LogManager

_SHUTDOWN_DRAIN_SLEEP_SECONDS = 0.01
_POST_JOIN_SHUTDOWN_DRAIN_SECONDS = 0.05
_POST_JOIN_SHUTDOWN_STABLE_EMPTY_PASSES = 2
_logger = logging.getLogger(__name__)

__all__ = [
    "ProcessExecutionEngine",
    "_BatchAccumulator",
    "_completion_event_from_dict",
    "_completion_event_to_dict",
    "_decode_incoming_item",
    "_decode_incoming_payloads",
    "_normalize_decoded_payloads",
    "_serialize_batch_payload",
    "_calculate_backoff",
    "_receive_task_payload",
    "_worker_loop",
    "_work_item_from_dict",
    "_work_item_to_dict",
]


class ProcessExecutionEngine(BaseExecutionEngine):
    """프로세스 기반 실행 엔진의 구현입니다.

    Args:
        config (ExecutionConfig): 실행 엔진 설정.
        worker_fn (Callable[[WorkItem], Any]): 사용자 정의 워커 함수.

    """

    def __init__(self, config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]):
        """Initialize this component.

        Args:
            config: Configuration object used to initialize this component.
            worker_fn: User worker callable invoked for each work item.

        Raises:
            TypeError: If initialization fails.
            RuntimeError: If initialization fails.

        """
        if inspect.iscoroutinefunction(worker_fn) or inspect.iscoroutinefunction(
            getattr(worker_fn, "__call__", None)
        ):
            raise TypeError(
                "Process execution mode requires a synchronous picklable worker"
            )
        if getattr(config.process_config, "require_picklable_worker", False):
            try:
                pickle.dumps(worker_fn)
            except Exception as exc:
                raise TypeError(
                    "Process execution mode requires a synchronous picklable worker"
                ) from exc

        self._config = config
        self._worker_fn = worker_fn
        self._transport_mode = config.process_config.transport_mode
        self._validate_transport_config()
        self._task_queue: Optional[Queue[Optional[WorkItem]]] = None
        self._batch_accumulator: _BatchAccumulator | _NoOpBatchAccumulator
        if self._transport_mode == "shared_queue":
            self._task_queue = Queue(maxsize=config.process_config.queue_size)
        self._completion_queue: Queue[Any] = Queue()
        self._registry_event_queue: Queue[Any] = Queue()
        self._prefetched_completion_events: Deque[CompletionEvent] = deque()
        self._in_flight_registry: dict[
            tuple[int, str, int, int], SerializedWorkItem
        ] = {}
        self._workers: List[Process] = []
        self._worker_pid_by_index: dict[int, Optional[int]] = {}
        self._in_flight_count: int = 0
        self._in_flight_lock = threading.Lock()
        self._registry_state_lock = threading.RLock()

        self._logger = logging.getLogger(__name__)
        self._is_shutdown: bool = False
        self._initialize_runtime_timing_state()
        self._last_worker_liveness_check = 0.0
        self._worker_liveness_check_interval_seconds = 0.05
        self._worker_slot_wait_liveness_lock = threading.RLock()
        self._worker_pipe_senders: list[Any] = []

        self._log_queue: Queue[logging.LogRecord] = Queue(
            maxsize=config.process_config.queue_size
        )
        main_handlers = tuple(logging.getLogger().handlers)
        self._log_listener = LogManager.create_queue_listener(
            self._log_queue, main_handlers
        )
        self._log_listener.start()

        if self._transport_mode == "shared_queue":
            if self._task_queue is None:
                raise RuntimeError("shared_queue transport requires a task queue")
            self._batch_accumulator = _BatchAccumulator(
                task_queue=self._task_queue,
                batch_size=config.process_config.batch_size,
                max_batch_wait_ms=config.process_config.max_batch_wait_ms,
                flush_policy=config.process_config.flush_policy,
                demand_flush_min_residence_ms=(
                    config.process_config.demand_flush_min_residence_ms
                ),
            )
            if self._task_queue is None:
                raise RuntimeError("shared_queue transport requires a task queue")
            self._transport: ProcessTransport = SharedQueueProcessTransport(
                task_queue=self._task_queue,
                get_batch_accumulator=lambda: self._batch_accumulator,
                work_item_from_dict=_work_item_from_dict,
                increment_in_flight=self._increment_in_flight_count,
                sentinel=_SENTINEL,
            )
        else:
            self._batch_accumulator = _NoOpBatchAccumulator()
            worker_pipe_transport = WorkerPipesProcessTransport(
                process_count=config.process_config.process_count,
                queue_size=config.process_config.queue_size,
                max_payload_bytes=config.process_config.msgpack_max_bytes,
                serialize_work_item=_work_item_to_dict,
                serialize_batch_payload=_serialize_batch_payload,
                work_item_from_dict=_work_item_from_dict,
                get_worker_pipe_senders=lambda: self._worker_pipe_senders,
                increment_in_flight=self._increment_in_flight_count,
                pipe_sentinel=_PIPE_SENTINEL,
                slot_wait_liveness_check=self._signal_worker_pipe_slot_wait,
            )
            self._transport = worker_pipe_transport

        self._start_workers()

    def _validate_transport_config(self) -> None:
        """Validate transport config for multiprocessing execution.

        Raises:
            ValueError: If the provided configuration or state is invalid.

        """
        process_config = self._config.process_config
        if self._transport_mode != "worker_pipes":
            return
        if process_config.batch_size != 1:
            raise ValueError(
                "worker_pipes transport only supports batch_size=1 in the first slice"
            )
        if process_config.max_batch_wait_ms != 0:
            raise ValueError(
                "worker_pipes transport rejects timer batching; set max_batch_wait_ms=0"
            )
        if process_config.flush_policy != "size_or_timer":
            raise ValueError(
                "worker_pipes transport rejects flush_policy=%s in the first slice"
                % process_config.flush_policy
            )
        if process_config.demand_flush_min_residence_ms != 0:
            raise ValueError(
                "worker_pipes transport rejects demand_flush_min_residence_ms>0"
            )
        if process_config.max_tasks_per_child != 0:
            raise ValueError(
                "worker_pipes transport does not support max_tasks_per_child yet"
            )
        if process_config.recycle_jitter_ms != 0:
            raise ValueError(
                "worker_pipes transport does not support recycle_jitter_ms yet"
            )

    def _start_worker(self, idx: int) -> Process:
        """Start worker for multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            Process result produced by this function.

        """
        (
            task_source,
            close_parent_after_start,
        ) = self._transport.start_worker_task_source(idx)
        worker = Process(
            target=_worker_loop,
            args=(
                task_source,
                self._completion_queue,
                self._registry_event_queue,
                self._worker_fn,
                idx,
                self._config,
                self._log_queue,
            ),
        )
        worker.start()
        if close_parent_after_start:
            close = getattr(task_source, "close", None)
            if callable(close):
                close()
        self._worker_pid_by_index[idx] = worker.pid
        self._logger.debug("Started ProcessWorker[%d] (PID: %d)", idx, worker.pid)
        return worker

    def _start_workers(self):
        """워커 프로세스 풀을 시작합니다."""
        for i in range(self._config.process_config.process_count):
            worker = self._start_worker(i)
            self._workers.append(worker)

    def _join_worker_with_escalation(self, worker: Process) -> None:
        """Handle join worker with escalation within multiprocessing execution.

        Args:
            worker: Worker process being managed.

        """
        timeout_sec = self._config.process_config.worker_join_timeout_ms / 1000.0
        worker.join(timeout=timeout_sec)
        if not worker.is_alive():
            return

        self._logger.warning(
            "ProcessWorker[%s] did not shut down gracefully after %.3fs. Terminating.",
            worker.pid,
            timeout_sec,
        )
        worker.terminate()
        worker.join(timeout=timeout_sec)
        if not worker.is_alive():
            return

        kill = getattr(worker, "kill", None)
        if callable(kill):
            self._logger.warning(
                "ProcessWorker[%s] still alive after terminate(). Killing.",
                worker.pid,
            )
            kill()
            worker.join(timeout=timeout_sec)

    def _emit_completion_event(self, completion_event: CompletionEvent) -> None:
        """Handle emit completion event within multiprocessing execution.

        Args:
            completion_event: Completion event to emit.

        """
        packed = msgpack.packb(
            _completion_event_to_dict(completion_event),
            use_bin_type=True,
        )
        self._completion_queue.put(packed)  # type: ignore[arg-type]

    def _emit_worker_recovery_failure(
        self,
        idx: int,
        payload: SerializedWorkItem,
        *,
        error: str,
        attempt: int,
        timeout_failure: bool = False,
    ) -> None:
        """Handle emit worker recovery failure within multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.
            payload: Serialized or decoded payload handled by this function.
            error: Error reason to attach to the completion or DLQ record.
            attempt: Current retry attempt number.
            timeout_failure: Timeout failure value used by this function.

        """
        try:
            completion_event = CompletionEvent(
                id=payload.get("id", ""),
                tp=TopicPartition(
                    payload.get("topic", ""),
                    payload.get("partition", 0),
                ),
                offset=payload.get("offset", -1),
                epoch=payload.get("epoch", 0),
                status=CompletionStatus.FAILURE,
                error=error,
                attempt=attempt,
            )
            self._emit_completion_event(completion_event)
        except Exception as push_exc:
            if timeout_failure:
                self._logger.error(
                    "Failed to emit timeout failure for worker %d item offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    push_exc,
                )
            else:
                self._logger.error(
                    "Failed to emit failure for worker %d item offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    push_exc,
                )

    def _get_registry_state_lock(self) -> Any:
        """Return registry state lock for multiprocessing execution.

        Returns:
            Any result produced by this function.

        """
        lock = getattr(self, "_registry_state_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._registry_state_lock = lock
        return lock

    def _recover_dead_worker_items(self, idx: int) -> list[SerializedWorkItem]:
        """Recover dead worker items for multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            list[SerializedWorkItem] result produced by this function.

        """
        with self._get_registry_state_lock():
            return ProcessRegistrySupport.recover_dead_worker_items(
                worker_index=idx,
                in_flight_registry=self._in_flight_registry,
                max_retries=self._config.max_retries,
                emit_worker_recovery_failure=self._emit_worker_recovery_failure,
            )

    def _emit_worker_restart_failures(
        self,
        idx: int,
        payloads: list[SerializedWorkItem],
        restart_exc: Exception,
    ) -> None:
        """Handle emit worker restart failures within multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.
            payloads: Serialized payloads handled by this function.
            restart_exc: Exception raised while attempting to restart a worker.

        """
        for payload in payloads:
            self._emit_worker_recovery_failure(
                idx,
                payload,
                error=f"worker_restart_failed: {restart_exc}",
                attempt=self._config.max_retries,
            )

    def _drain_registry_event_queue(self) -> int:
        """Drain registry event queue for multiprocessing execution.

        Returns:
            Computed integer value.

        """
        with self._get_registry_state_lock():
            return ProcessRegistrySupport.drain_registry_event_queue(
                registry_event_queue=getattr(self, "_registry_event_queue", None),
                apply_event=self._apply_registry_event,
            )

    def _ensure_workers_alive(self, *, force: bool = False) -> None:
        """Handle ensure workers alive within multiprocessing execution.

        Args:
            force: Whether to force the operation regardless of cadence checks.

        """
        self._drain_visible_worker_events()
        if not self._should_run_worker_liveness_scan(force=force):
            return

        for idx, exitcode in self._collect_dead_worker_recovery_candidates():
            to_requeue = self._collect_recoverable_worker_payloads(idx)
            self._logger.error(
                "ProcessWorker[%d] died (exitcode=%s). Restarting worker.",
                idx,
                exitcode,
            )
            if self._restart_dead_worker(idx, exitcode, to_requeue):
                self._publish_recovered_worker_payloads(idx, to_requeue)

    def _drain_visible_worker_events(self) -> None:
        """Drain visible worker events for multiprocessing execution."""
        self._drain_registry_events()
        self._prefetch_completed_events_from_queue()

    def _should_run_worker_liveness_scan(self, *, force: bool) -> bool:
        """Return whether run worker liveness scan should run in multiprocessing execution.

        Args:
            force: Whether to force the operation regardless of cadence checks.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        """
        liveness_interval = getattr(
            self,
            "_worker_liveness_check_interval_seconds",
            0.0,
        )
        if liveness_interval > 0 and not force:
            now = time.monotonic()
            last_check = getattr(self, "_last_worker_liveness_check", 0.0)
            if now - last_check < liveness_interval:
                return False
            self._last_worker_liveness_check = now
            return True
        if force:
            self._last_worker_liveness_check = time.monotonic()
        return True

    def _collect_dead_worker_recovery_candidates(self) -> list[tuple[int, Any]]:
        """Handle collect dead worker recovery candidates within multiprocessing execution.

        Returns:
            list[tuple[int, Any]] result produced by this function.

        """
        candidates: list[tuple[int, Any]] = []
        for idx, worker in enumerate(self._workers):
            if not worker.is_alive():
                candidates.append((idx, worker.exitcode))
        return candidates

    def _collect_recoverable_worker_payloads(
        self,
        idx: int,
    ) -> list[SerializedWorkItem]:
        """Handle collect recoverable worker payloads within multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            list[SerializedWorkItem] result produced by this function.

        """
        to_requeue: list[SerializedWorkItem] = []
        try:
            to_requeue.extend(self._recover_dead_worker_items(idx))
        except Exception as recovery_exc:
            self._logger.error(
                "Failed to recover in-flight work from worker %d: %s",
                idx,
                recovery_exc,
            )
        try:
            to_requeue.extend(self._recover_pending_pipe_dispatches(idx))
        except Exception as recovery_exc:
            self._logger.error(
                "Failed to recover pending dispatches from worker %d: %s",
                idx,
                recovery_exc,
            )
        return to_requeue

    def _restart_dead_worker(
        self,
        idx: int,
        exitcode: Any,
        recovered_payloads: list[SerializedWorkItem],
    ) -> bool:
        """Handle restart dead worker within multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.
            exitcode: Process exit code observed for the worker.
            recovered_payloads: Recovered payloads value used by this function.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        """
        try:
            new_worker = self._start_worker(idx)
        except Exception as restart_exc:
            self._logger.error(
                "Failed to restart worker %d after exitcode=%s: %s",
                idx,
                exitcode,
                restart_exc,
            )
            self._emit_worker_restart_failures(idx, recovered_payloads, restart_exc)
            return False
        self._workers[idx] = new_worker
        return True

    def _publish_recovered_worker_payloads(
        self,
        idx: int,
        payloads: list[SerializedWorkItem],
    ) -> None:
        """Publish recovered worker payloads for multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.
            payloads: Serialized payloads handled by this function.

        """
        if not payloads:
            return
        requeued_offsets: list[Any] = []
        for payload in payloads:
            try:
                self._requeue_recovered_payloads([payload])
                requeued_offsets.append(payload.get("offset"))
            except Exception as requeue_exc:
                self._logger.error(
                    "Failed to requeue recovered work from worker %d offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    requeue_exc,
                )
                self._emit_worker_recovery_failure(
                    idx,
                    payload,
                    error="worker_requeue_failed: %s" % requeue_exc,
                    attempt=self._config.max_retries,
                )
        if requeued_offsets:
            self._logger.warning(
                "Requeued %d lost work item(s) offsets=%s from dead worker %d",
                len(requeued_offsets),
                requeued_offsets,
                idx,
            )

    def _recover_pending_pipe_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        """Recover pending pipe dispatches for multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            list[SerializedWorkItem] result produced by this function.

        """
        transport = getattr(self, "_transport", None)
        if transport is None:
            return []
        capabilities = getattr(transport, "capabilities", None)
        if capabilities is None or not capabilities.pending_dispatch_recovery:
            return []
        recovered_dispatches = transport.recover_pending_dispatches(idx)
        if recovered_dispatches:
            identities = [entry.identity for entry in recovered_dispatches]
            self._logger.warning(
                "Recovered %d pending worker-pipe dispatch(es) identities=%s",
                len(recovered_dispatches),
                identities,
            )
        return self._filter_recoverable_pending_pipe_dispatches(
            idx, [entry.payload for entry in recovered_dispatches]
        )

    def _signal_worker_pipe_slot_wait(self) -> None:
        """Handle signal worker pipe slot wait within multiprocessing execution."""
        lock = getattr(self, "_worker_slot_wait_liveness_lock", None)
        if lock is None:
            lock = threading.RLock()
            self._worker_slot_wait_liveness_lock = lock
        if not lock.acquire(blocking=False):
            return
        try:
            self._ensure_workers_alive(force=True)
        finally:
            lock.release()

    def _filter_recoverable_pending_pipe_dispatches(
        self,
        idx: int,
        payloads: list[SerializedWorkItem],
    ) -> list[SerializedWorkItem]:
        """Handle filter recoverable pending pipe dispatches within multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.
            payloads: Serialized payloads handled by this function.

        Returns:
            list[SerializedWorkItem] result produced by this function.

        """
        recoverable: list[SerializedWorkItem] = []
        max_retries = self._config.max_retries
        for payload in payloads:
            attempts = payload.get("requeue_attempts", 0)
            if attempts >= max_retries:
                self._emit_worker_recovery_failure(
                    idx,
                    payload,
                    error="worker_died_max_retries",
                    attempt=attempts,
                )
                continue
            recovered_payload = dict(payload)
            recovered_payload["requeue_attempts"] = attempts + 1
            recoverable.append(recovered_payload)
        return recoverable

    def _requeue_recovered_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        """Requeue recovered payloads for multiprocessing execution.

        Args:
            payloads: Serialized payloads handled by this function.

        """
        if not payloads:
            return
        self._transport.requeue_payloads(payloads)

    def _apply_registry_event(self, event: dict[str, Any]) -> None:
        """Handle apply registry event within multiprocessing execution.

        Args:
            event: Completion or registry event being processed.

        """
        self._initialize_runtime_timing_state()
        transport = getattr(self, "_transport", None)
        if transport is not None:
            transport.handle_registry_event(event)
        ProcessRegistrySupport.apply_registry_event(
            event=event,
            in_flight_registry=self._in_flight_registry,
            record_main_to_worker_ipc=self._record_main_to_worker_ipc,
            record_worker_exec=self._record_worker_exec,
        )

    def _drain_registry_events(self) -> None:
        """Drain registry events for multiprocessing execution."""
        self._drain_registry_event_queue()

    def _prefetch_completed_events_from_queue(self) -> int:
        """Build prefetch completed events from queue.

        Returns:
            Computed integer value.

        """
        with self._get_registry_state_lock():
            completion_queue = getattr(self, "_completion_queue", None)
            prefetched_events = getattr(self, "_prefetched_completion_events", None)
            if completion_queue is None or prefetched_events is None:
                return 0
            prefetched = 0
            while True:
                try:
                    raw_event = completion_queue.get_nowait()
                except queue.Empty:
                    return prefetched
                event = self._decode_completion_queue_item(raw_event)
                self._prefetch_completion_event(event)
                prefetched += 1

    def _prefetch_completion_event(self, event: CompletionEvent) -> None:
        """Handle prefetch completion event within multiprocessing execution.

        Args:
            event: Completion or registry event being processed.

        """
        self._prefetched_completion_events.append(event)
        self._discard_registry_entry_for_completion(event)

    def _discard_registry_entry_for_completion(self, event: CompletionEvent) -> None:
        """Handle discard registry entry for completion within multiprocessing execution.

        Args:
            event: Completion or registry event being processed.

        """
        with self._get_registry_state_lock():
            in_flight_registry = getattr(self, "_in_flight_registry", None)
            if in_flight_registry is None:
                return
            ProcessRegistrySupport.discard_completion_from_registry(
                in_flight_registry=in_flight_registry,
                event=event,
            )

    def _drain_shutdown_ipc_once(self) -> tuple[int, int]:
        """Drain shutdown ipc once for multiprocessing execution.

        Returns:
            tuple[int, int] result produced by this function.

        """
        drained_registry = self._drain_registry_event_queue()
        drained_completion = 0

        while True:
            try:
                raw_event = self._completion_queue.get_nowait()
            except queue.Empty:
                break
            drained_completion += 1
            self._prefetch_completion_event(
                self._decode_completion_queue_item(raw_event)
            )

        return drained_registry, drained_completion

    async def _drain_shutdown_ipc_until_stable_empty(
        self,
        *,
        max_seconds: float,
        stable_empty_passes: int,
    ) -> tuple[int, int, int]:
        """Drain shutdown ipc until stable empty for multiprocessing execution.

        Args:
            max_seconds: Maximum time to spend in the operation, in seconds.
            stable_empty_passes: Number of consecutive empty drain passes required before stopping.

        Returns:
            tuple[int, int, int] result produced by this function.

        """
        deadline = time.monotonic() + max(0.0, max_seconds)
        hard_deadline = deadline + (
            _SHUTDOWN_DRAIN_SLEEP_SECONDS * max(1, stable_empty_passes + 2)
        )
        total_registry_drained = 0
        total_completion_drained = 0
        total_passes = 0
        empty_passes = 0

        while True:
            drained_registry, drained_completion = self._drain_shutdown_ipc_once()
            total_registry_drained += drained_registry
            total_completion_drained += drained_completion
            total_passes += 1

            if drained_registry == 0 and drained_completion == 0:
                empty_passes += 1
            else:
                empty_passes = 0

            if empty_passes >= stable_empty_passes:
                break
            now = time.monotonic()
            remaining_seconds = deadline - now
            # The shutdown safety boundary is stable-empty observation, not
            # merely the original time budget.  A multiprocessing.Queue feeder
            # can make the first late IPC event visible just after the budget,
            # so still perform non-zero post-deadline waits until the queue has
            # been observed empty for the configured consecutive passes.  Keep
            # that post-deadline grace bounded so shutdown cannot hang forever
            # if a buggy or hostile queue source never reaches stable-empty.
            if remaining_seconds > 0:
                sleep_seconds = min(_SHUTDOWN_DRAIN_SLEEP_SECONDS, remaining_seconds)
            else:
                remaining_grace_seconds = hard_deadline - now
                if remaining_grace_seconds <= 0:
                    break
                sleep_seconds = min(
                    _SHUTDOWN_DRAIN_SLEEP_SECONDS,
                    remaining_grace_seconds,
                )
            await asyncio.sleep(sleep_seconds)

        return total_registry_drained, total_completion_drained, total_passes

    def get_min_inflight_offset(self, tp: TopicPartition) -> Optional[int]:
        """Expose the deprecated process-private in-flight offset hook.

        Commit safety is now computed from WorkManager's submitted-work ledger.
        This method only surfaces process-private recovery state for diagnostics
        and compatibility callers that still expect the method to exist.

        Args:
            tp: Topic-partition affected by the operation.

        Returns:
            Computed integer value, or None when no value is available.

        """
        self._drain_registry_events()
        return ProcessRegistrySupport.get_min_inflight_offset(
            in_flight_registry=self._in_flight_registry,
            tp=tp,
        )

    def get_runtime_metrics(self) -> Optional[EngineRuntimeDiagnostics]:
        """Return runtime metrics for multiprocessing execution.

        Returns:
            Engine runtime diagnostics, or None when the engine has no metrics.

        """
        self._drain_registry_events()
        base_metrics = self._batch_accumulator.snapshot()
        transport_mode = self._get_transport_mode()
        support_state = "bounded" if transport_mode == "worker_pipes" else "full"
        timer_flush_supported = transport_mode != "worker_pipes"
        demand_flush_supported = transport_mode != "worker_pipes"
        recycle_supported = transport_mode != "worker_pipes"
        self._initialize_runtime_timing_state()
        with self._runtime_timing_lock:
            main_to_worker_avg = (
                self._main_to_worker_ipc_sum_seconds / self._main_to_worker_ipc_samples
                if self._main_to_worker_ipc_samples > 0
                else 0.0
            )
            worker_exec_avg = (
                self._worker_exec_sum_seconds / self._worker_exec_samples
                if self._worker_exec_samples > 0
                else 0.0
            )
            worker_to_main_avg = (
                self._worker_to_main_ipc_sum_seconds / self._worker_to_main_ipc_samples
                if self._worker_to_main_ipc_samples > 0
                else 0.0
            )
            return EngineRuntimeDiagnostics(
                engine_type="process",
                process=ProcessRuntimeDiagnostics(
                    batch_metrics=ProcessBatchMetrics(
                        size_flush_count=base_metrics.size_flush_count,
                        timer_flush_count=base_metrics.timer_flush_count,
                        close_flush_count=base_metrics.close_flush_count,
                        total_flushed_items=base_metrics.total_flushed_items,
                        last_flush_size=base_metrics.last_flush_size,
                        last_flush_wait_seconds=base_metrics.last_flush_wait_seconds,
                        buffered_items=base_metrics.buffered_items,
                        buffered_age_seconds=base_metrics.buffered_age_seconds,
                        demand_flush_count=base_metrics.demand_flush_count,
                        last_main_to_worker_ipc_seconds=self._last_main_to_worker_ipc_seconds,
                        avg_main_to_worker_ipc_seconds=main_to_worker_avg,
                        last_worker_exec_seconds=self._last_worker_exec_seconds,
                        avg_worker_exec_seconds=worker_exec_avg,
                        last_worker_to_main_ipc_seconds=self._last_worker_to_main_ipc_seconds,
                        avg_worker_to_main_ipc_seconds=worker_to_main_avg,
                        transport_mode=transport_mode,
                        support_state=support_state,
                        timer_flush_supported=timer_flush_supported,
                        demand_flush_supported=demand_flush_supported,
                        recycle_supported=recycle_supported,
                    )
                ),
            )

    async def submit(self, work_item: WorkItem) -> None:
        """제출된 작업 항목을 태스크 큐에 넣습니다.

        Args:
            work_item: Work item being scheduled or processed.

        """
        self._drain_registry_events()
        if self._get_transport_mode() == "worker_pipes":
            await asyncio.to_thread(self._ensure_workers_alive, force=True)
        else:
            self._ensure_workers_alive()
        await self._transport.submit_work_item(
            work_item,
            route_identity=resolve_route_identity(work_item),
            count_in_flight=True,
        )

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """완료 큐에서 완료 이벤트를 가져와 리스트로 반환합니다.

        Args:
            batch_limit: Maximum number of completion events to return.

        Returns:
            List[CompletionEvent] result produced by this function.

        """
        if not getattr(self, "_is_shutdown", False):
            await asyncio.to_thread(self._ensure_workers_alive)
            self._drain_registry_events()

        completed_events: List[CompletionEvent] = []
        while (
            len(completed_events) < batch_limit and self._prefetched_completion_events
        ):
            completed_events.append(self._prefetched_completion_events.popleft())
            with self._in_flight_lock:
                self._in_flight_count -= 1
        if getattr(self, "_is_shutdown", False):
            return completed_events
        while len(completed_events) < batch_limit:
            try:
                raw_event = self._completion_queue.get_nowait()
                event = self._decode_completion_queue_item(raw_event)
                self._discard_registry_entry_for_completion(event)
                completed_events.append(event)
                with self._in_flight_lock:
                    self._in_flight_count -= 1
            except queue.Empty:
                break
            except Exception as e:
                _logger.error(
                    "Error getting item from completion queue: %r", e, exc_info=True
                )
                break
        return completed_events

    async def wait_for_completion(
        self, timeout_seconds: Optional[float] = None
    ) -> bool:
        """Wait for for completion in multiprocessing execution.

        Args:
            timeout_seconds: Maximum time to wait, in seconds; None waits indefinitely.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        """
        if getattr(self, "_is_shutdown", False):
            return bool(self._prefetched_completion_events)

        await asyncio.to_thread(self._ensure_workers_alive)
        self._drain_registry_events()

        if self._prefetched_completion_events:
            return True

        try:
            raw_event = self._completion_queue.get_nowait()
        except queue.Empty:
            raw_event = None

        if raw_event is not None:
            self._prefetch_completion_event(
                self._decode_completion_queue_item(raw_event)
            )
            return True

        if timeout_seconds is not None and timeout_seconds <= 0:
            return False

        try:
            raw_event = await asyncio.to_thread(
                self._completion_queue.get,
                True,
                timeout_seconds,
            )
        except queue.Empty:
            return False

        self._prefetch_completion_event(self._decode_completion_queue_item(raw_event))
        return True

    def _initialize_runtime_timing_state(self) -> None:
        """Handle initialize runtime timing state within multiprocessing execution."""
        if hasattr(self, "_runtime_timing_lock"):
            return
        self._runtime_timing_lock = threading.Lock()
        self._main_to_worker_ipc_samples = 0
        self._main_to_worker_ipc_sum_seconds = 0.0
        self._last_main_to_worker_ipc_seconds = 0.0
        self._worker_exec_samples = 0
        self._worker_exec_sum_seconds = 0.0
        self._last_worker_exec_seconds = 0.0
        self._worker_to_main_ipc_samples = 0
        self._worker_to_main_ipc_sum_seconds = 0.0
        self._last_worker_to_main_ipc_seconds = 0.0

    def _record_main_to_worker_ipc(self, duration_seconds: Any) -> None:
        """Convert record main to worker ipc.

        Args:
            duration_seconds: Observed duration in seconds.

        """
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_main_to_worker_ipc_samples",
            sum_attr="_main_to_worker_ipc_sum_seconds",
            last_attr="_last_main_to_worker_ipc_seconds",
        )

    def _record_worker_exec(self, duration_seconds: Any) -> None:
        """Record worker exec for multiprocessing execution.

        Args:
            duration_seconds: Observed duration in seconds.

        """
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_worker_exec_samples",
            sum_attr="_worker_exec_sum_seconds",
            last_attr="_last_worker_exec_seconds",
        )

    def _record_worker_to_main_ipc(self, duration_seconds: Any) -> None:
        """Convert record worker to main ipc.

        Args:
            duration_seconds: Observed duration in seconds.

        """
        self._record_runtime_timing(
            duration_seconds,
            sample_attr="_worker_to_main_ipc_samples",
            sum_attr="_worker_to_main_ipc_sum_seconds",
            last_attr="_last_worker_to_main_ipc_seconds",
        )

    def _record_runtime_timing(
        self,
        duration_seconds: Any,
        *,
        sample_attr: str,
        sum_attr: str,
        last_attr: str,
    ) -> None:
        """Record runtime timing for multiprocessing execution.

        Args:
            duration_seconds: Observed duration in seconds.
            sample_attr: Sample attr value used by this function.
            sum_attr: Sum attr value used by this function.
            last_attr: Last attr value used by this function.

        """
        if not isinstance(duration_seconds, (int, float)):
            return
        self._initialize_runtime_timing_state()
        duration = max(0.0, float(duration_seconds))
        with self._runtime_timing_lock:
            setattr(self, sample_attr, getattr(self, sample_attr) + 1)
            setattr(self, sum_attr, getattr(self, sum_attr) + duration)
            setattr(self, last_attr, duration)

    def _decode_completion_queue_item(self, raw_event: Any) -> CompletionEvent:
        """Decode completion queue item for multiprocessing execution.

        Args:
            raw_event: Raw event value used by this function.

        Returns:
            Completion event produced by the operation.

        """
        if isinstance(raw_event, (bytes, bytearray)):
            payload = msgpack.unpackb(raw_event, raw=False)
            completion_enqueued_at = payload.get("completion_enqueued_at")
            if isinstance(completion_enqueued_at, (int, float)):
                self._record_worker_to_main_ipc(
                    time.monotonic() - float(completion_enqueued_at)
                )
            return _completion_event_from_dict(payload)
        return raw_event

    def get_in_flight_count(self) -> int:
        """현재 처리 중인 작업 항목의 수를 반환합니다.

        Returns:
            Computed integer value.

        """
        with self._in_flight_lock:
            return self._in_flight_count

    def _dispatch_payload_to_transport(
        self,
        payload: SerializedWorkItem,
        count_in_flight: bool = False,
    ) -> None:
        """Convert dispatch payload to transport.

        Args:
            payload: Serialized or decoded payload handled by this function.
            count_in_flight: Whether dispatch should increment in-flight accounting.

        """
        work_item = _work_item_from_dict(payload)
        self._transport.dispatch_payload(
            payload,
            route_identity=resolve_route_identity(work_item),
            count_in_flight=count_in_flight,
        )

    def _get_transport_mode(self) -> str:
        """Return transport mode for multiprocessing execution.

        Returns:
            Computed string value.

        """
        return getattr(self, "_transport_mode", "shared_queue")

    async def shutdown(self) -> None:
        """실행 엔진을 정상적으로 종료합니다. 모든 워커 프로세스에 종료 시그널을 보내고 대기합니다.

        이 메서드는 멱등(idempotent)하며, 여러 번 호출해도 안전합니다.
        """
        if self._is_shutdown:
            _logger.debug(
                "ProcessExecutionEngine.shutdown() called but already shut down. Skipping."
            )
            return
        self._is_shutdown = True

        self._drain_registry_events()
        prefetched_count = len(self._prefetched_completion_events)
        in_flight_registry_size = len(self._in_flight_registry)
        worker_count = len(self._workers)
        _logger.debug(
            "Initiating ProcessExecutionEngine shutdown. prefetched_completion_events=%d in_flight_registry=%d worker_count=%d",
            prefetched_count,
            in_flight_registry_size,
            worker_count,
        )
        self._batch_accumulator.close()
        self._transport.signal_shutdown(len(self._workers))

        shutdown_drain_deadline = time.monotonic() + 1.0
        total_registry_drained = 0
        total_completion_drained = 0
        while time.monotonic() < shutdown_drain_deadline:
            drained_registry, drained_completion = self._drain_shutdown_ipc_once()
            total_registry_drained += drained_registry
            total_completion_drained += drained_completion
            if (
                drained_registry == 0
                and drained_completion == 0
                and not self._in_flight_registry
            ):
                break
            await asyncio.sleep(_SHUTDOWN_DRAIN_SLEEP_SECONDS)
        _logger.debug(
            "ProcessExecutionEngine shutdown pre-join drain: registry_events=%d completion_events=%d residual_in_flight_registry=%d",
            total_registry_drained,
            total_completion_drained,
            len(self._in_flight_registry),
        )

        # Wait for all workers to finish
        for worker in self._workers:
            self._join_worker_with_escalation(worker)

        (
            post_join_registry_drained,
            post_join_completion_drained,
            post_join_drain_passes,
        ) = await self._drain_shutdown_ipc_until_stable_empty(
            max_seconds=_POST_JOIN_SHUTDOWN_DRAIN_SECONDS,
            stable_empty_passes=_POST_JOIN_SHUTDOWN_STABLE_EMPTY_PASSES,
        )
        _logger.debug(
            "ProcessExecutionEngine shutdown post-join drain: registry_events=%d completion_events=%d passes=%d residual_in_flight_registry=%d",
            post_join_registry_drained,
            post_join_completion_drained,
            post_join_drain_passes,
            len(self._in_flight_registry),
        )
        if self._in_flight_registry:
            registry_summary = []
            for (worker_idx, topic, partition, offset), payload in sorted(
                self._in_flight_registry.items(),
                key=lambda item: item[0],
            ):
                registry_summary.append(
                    "%d(pid=%s):%s-%d@%d id=%s epoch=%s timed_out=%s attempts=%s"
                    % (
                        worker_idx,
                        self._worker_pid_by_index.get(worker_idx),
                        topic,
                        partition,
                        offset,
                        payload.get("id", ""),
                        payload.get("epoch", 0),
                        payload.get("timed_out", False),
                        payload.get("requeue_attempts", 0),
                    )
                )
            _logger.warning(
                "Residual in-flight registry after shutdown drain: %s",
                "; ".join(registry_summary),
            )

        self._in_flight_registry.clear()
        self._transport.clear_pending_dispatches()
        with self._in_flight_lock:
            self._in_flight_count = len(self._prefetched_completion_events)

        _logger.debug("ProcessExecutionEngine shutdown complete.")
        self._log_listener.stop()
        for queue_obj in (
            self._task_queue,
            self._completion_queue,
            self._registry_event_queue,
        ):
            if queue_obj is None:
                continue
            close = getattr(queue_obj, "close", None)
            if callable(close):
                close()
        self._transport.close()

    def _increment_in_flight_count(self) -> None:
        """Increment in flight count for multiprocessing execution."""
        with self._in_flight_lock:
            self._in_flight_count += 1
