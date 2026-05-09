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
import uuid
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
    EngineWorkerDiagnostics,
    ExecutionControlEvent,
    ProcessBatchMetrics,
    ProcessRuntimeDiagnostics,
    RouteBatch,
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
from pyrallel_consumer.execution_plane.process_codec import (
    BATCH_COMPLETION_KIND,
    SerializedWorkItem,
    _decode_msgpack_payload,
)
from pyrallel_consumer.execution_plane.process_codec import (
    batch_completion_from_dict as _batch_completion_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_from_dict as _completion_event_from_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    completion_event_to_dict as _completion_event_to_dict,
)
from pyrallel_consumer.execution_plane.process_codec import (
    decode_batch_completion_payload as _decode_batch_completion_payload,
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
from pyrallel_consumer.execution_plane.process_completion_buffer import (
    ProcessCompletionBuffer,
)
from pyrallel_consumer.execution_plane.process_registry_support import (
    ProcessRegistrySupport,
)
from pyrallel_consumer.execution_plane.process_shutdown_support import (
    ProcessShutdownContext,
    ProcessShutdownSupport,
)
from pyrallel_consumer.execution_plane.process_transport import (
    ProcessTransport,
    resolve_route_identity,
    stable_worker_index_for_route,
)
from pyrallel_consumer.execution_plane.process_transport_worker_pipes import (
    WorkerPipesProcessTransport,
)
from pyrallel_consumer.execution_plane.process_worker_runtime import (
    _PIPE_SENTINEL,
    _calculate_backoff,
    _receive_task_payload,
    _worker_loop,
)
from pyrallel_consumer.execution_plane.process_worker_supervisor import (
    ProcessWorkerSupervisor,
    ProcessWorkerSupervisorContext,
)
from pyrallel_consumer.execution_plane.worker_spec import WorkerSpec
from pyrallel_consumer.logger import LogManager
from pyrallel_consumer.worker import BatchWorkerContractError

_DEFAULT_MSGPACK_MAX_BYTES = 1_000_000
_MAX_SEEN_COMPLETION_IDENTITIES = 100_000
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

    def __init__(
        self,
        config: ExecutionConfig,
        worker_fn: Callable[[WorkItem], Any] | WorkerSpec,
    ):
        """Initialize this component.

        Args:
            config: Configuration object used to initialize this component.
            worker_fn: User worker callable invoked for each work item.

        Raises:
            TypeError: If initialization fails.
            RuntimeError: If initialization fails.

        """
        worker_callable = (
            worker_fn.callable if isinstance(worker_fn, WorkerSpec) else worker_fn
        )
        if inspect.iscoroutinefunction(worker_callable) or inspect.iscoroutinefunction(
            getattr(worker_callable, "__call__", None)
        ):
            raise TypeError(
                "Process execution mode requires a synchronous picklable worker"
            )
        if getattr(config.process_config, "require_picklable_worker", False):
            try:
                pickle.dumps(worker_callable)
            except Exception as exc:
                raise TypeError(
                    "Process execution mode requires a synchronous picklable worker"
                ) from exc

        self._config = config
        self._worker_fn = (
            worker_fn
            if isinstance(worker_fn, WorkerSpec) and worker_fn.kind == "batch"
            else worker_callable
        )
        self._validate_transport_config()
        self._task_queue: Optional[Queue[Optional[WorkItem]]] = None
        self._batch_accumulator: _BatchAccumulator | _NoOpBatchAccumulator
        self._completion_queue: Queue[Any] = Queue()
        self._registry_event_queue: Queue[Any] = Queue()
        self._process_control_events: Deque[ExecutionControlEvent] = deque()
        self._prefetched_completion_events: Deque[CompletionEvent] = deque()
        self._seen_completion_identities: set[tuple[str, str, int, int, int]] = set()
        self._seen_completion_identity_order: Deque[
            tuple[str, str, int, int, int]
        ] = deque()
        self._completion_buffer = self._build_completion_buffer()
        self._shutdown_support = ProcessShutdownSupport(_logger)
        self._worker_supervisor = ProcessWorkerSupervisor()
        self._in_flight_registry: dict[
            tuple[int, str, int, int], SerializedWorkItem
        ] = {}
        self._process_batch_manifests: dict[str, dict[str, Any]] = {}
        self._active_process_batch_ids: set[str] = set()
        self._completed_process_batch_payloads: dict[
            tuple[str, str, int, int, int], tuple[int, SerializedWorkItem]
        ] = {}
        self._stale_process_batch_completion_count: int = 0
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
        self._transport: ProcessTransport = worker_pipe_transport

        self._start_workers()

    @property
    def supports_ordered_route_batch(self) -> bool:
        """Return whether process mode can dispatch ordered route batches safely."""
        return True

    def _validate_transport_config(self) -> None:
        """Validate transport config for multiprocessing execution.

        Raises:
            ValueError: If the provided configuration or state is invalid.

        """
        process_config = self._config.process_config
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
        self._get_shutdown_support().join_worker_with_escalation(
            worker,
            timeout_seconds=(
                self._config.process_config.worker_join_timeout_ms / 1000.0
            ),
        )

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
        self._get_worker_supervisor().emit_worker_restart_failures(
            self._build_worker_supervisor_context(),
            idx,
            payloads,
            restart_exc,
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
        if getattr(self, "_is_shutdown", False):
            return
        self._get_worker_supervisor().ensure_workers_alive(
            self._build_worker_supervisor_context(),
            force=force,
        )

    def _drain_visible_worker_events(self) -> None:
        """Drain visible worker events for multiprocessing execution."""
        self._get_worker_supervisor().drain_visible_worker_events(
            self._build_worker_supervisor_context()
        )

    def _should_run_worker_liveness_scan(self, *, force: bool) -> bool:
        """Return whether run worker liveness scan should run in multiprocessing execution.

        Args:
            force: Whether to force the operation regardless of cadence checks.

        Returns:
            True when the operation succeeds or the condition is met; otherwise False.

        """
        return self._get_worker_supervisor().should_run_liveness_scan(
            self._build_worker_supervisor_context(),
            force=force,
            monotonic=time.monotonic,
        )

    def _collect_dead_worker_recovery_candidates(self) -> list[tuple[int, Any]]:
        """Handle collect dead worker recovery candidates within multiprocessing execution.

        Returns:
            list[tuple[int, Any]] result produced by this function.

        """
        return self._get_worker_supervisor().collect_dead_worker_recovery_candidates(
            self._build_worker_supervisor_context()
        )

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
        return self._get_worker_supervisor().collect_recoverable_worker_payloads(
            self._build_worker_supervisor_context(),
            idx,
        )

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
        return self._get_worker_supervisor().restart_dead_worker(
            self._build_worker_supervisor_context(),
            idx,
            exitcode,
            recovered_payloads,
        )

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
        self._get_worker_supervisor().publish_recovered_worker_payloads(
            self._build_worker_supervisor_context(),
            idx,
            payloads,
        )

    def _recover_pending_pipe_dispatches(self, idx: int) -> list[SerializedWorkItem]:
        """Recover pending pipe dispatches for multiprocessing execution.

        Args:
            idx: Worker index being inspected or restarted.

        Returns:
            list[SerializedWorkItem] result produced by this function.

        """
        return self._get_worker_supervisor().recover_pending_pipe_dispatches(
            self._build_worker_supervisor_context(),
            idx,
        )

    def _signal_worker_pipe_slot_wait(self) -> None:
        """Handle signal worker pipe slot wait within multiprocessing execution."""
        if getattr(self, "_is_shutdown", False):
            return
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
        return self._get_worker_supervisor().filter_recoverable_pending_pipe_dispatches(
            self._build_worker_supervisor_context(),
            idx,
            payloads,
        )

    def _requeue_recovered_payloads(self, payloads: list[SerializedWorkItem]) -> None:
        """Requeue recovered payloads for multiprocessing execution.

        Args:
            payloads: Serialized payloads handled by this function.

        """
        if not payloads:
            return
        self._transport.requeue_payloads(payloads)

    def _get_process_batch_manifests(self) -> dict[str, dict[str, Any]]:
        """Return parent-owned process batch manifests."""
        manifests = getattr(self, "_process_batch_manifests", None)
        if manifests is None:
            manifests = {}
            self._process_batch_manifests = manifests
        return manifests

    def _get_process_control_events(self) -> Deque[ExecutionControlEvent]:
        """Return buffered parent-side process control events."""
        control_events = getattr(self, "_process_control_events", None)
        if control_events is None:
            control_events = deque()
            self._process_control_events = control_events
        return control_events

    def _get_active_process_batch_ids(self) -> set[str]:
        """Return parent-owned active process batch identifiers."""
        batch_ids = getattr(self, "_active_process_batch_ids", None)
        if batch_ids is None:
            batch_ids = set()
            self._active_process_batch_ids = batch_ids
        return batch_ids

    def _record_process_batch_manifest(
        self,
        *,
        batch_id: str,
        worker_index: int,
        items: list[WorkItem],
    ) -> None:
        """Record parent-owned membership for a process route-batch attempt."""
        timeout_seconds = max(
            0.001,
            float(getattr(self._config.process_config, "task_timeout_ms", 30000))
            / 1000.0,
        )
        self._get_process_batch_manifests()[batch_id] = {
            "batch_id": batch_id,
            "worker_index": worker_index,
            "items": [_work_item_to_dict(item) for item in items],
            "start_ack_deadline_at": time.monotonic() + timeout_seconds,
        }
        self._get_active_process_batch_ids().add(batch_id)

    def _recover_expired_batch_start_acks(self, *, now: float) -> int:
        """Recover route batches whose child batch_start ack did not arrive in time."""
        recovered = 0
        manifests = self._get_process_batch_manifests()
        for batch_id, manifest in list(manifests.items()):
            deadline = manifest.get("start_ack_deadline_at")
            if not isinstance(deadline, (int, float)) or now <= float(deadline):
                continue
            payloads = [
                dict(payload)
                for payload in manifest.get("items", [])
                if isinstance(payload, dict)
            ]
            manifests.pop(batch_id, None)
            self._get_active_process_batch_ids().discard(str(batch_id))
            if payloads:
                self._requeue_recovered_payloads(payloads)
            recovered += 1
        return recovered

    def _quarantine_stale_batch_completion(self, batch_id: str) -> bool:
        """Return True after recording a stale process batch completion."""
        active_batch_ids = getattr(self, "_active_process_batch_ids", None)
        if active_batch_ids is None:
            return False
        if batch_id in active_batch_ids:
            return False
        self._stale_process_batch_completion_count = (
            getattr(self, "_stale_process_batch_completion_count", 0) + 1
        )
        self._logger.warning(
            "Quarantined stale process batch completion batch_id=%s",
            batch_id,
        )
        return True

    def _retire_active_process_batch_id(self, batch_id: str) -> None:
        """Retire an active process batch identifier after accepted completion decode."""
        active_batch_ids = getattr(self, "_active_process_batch_ids", None)
        if active_batch_ids is not None:
            active_batch_ids.discard(batch_id)

    def _completion_payload_identity(
        self,
        payload: SerializedWorkItem,
    ) -> tuple[str, str, int, int, int]:
        """Return the logical completion identity for a serialized payload."""
        return (
            str(payload.get("id")),
            str(payload.get("topic")),
            int(payload.get("partition", 0)),
            int(payload.get("offset", -1)),
            int(payload.get("epoch", 0)),
        )

    def _completion_event_identity(
        self,
        event: CompletionEvent,
    ) -> tuple[str, str, int, int, int]:
        """Return the logical completion identity for a completion event."""
        return (
            event.id,
            event.tp.topic,
            event.tp.partition,
            event.offset,
            event.epoch,
        )

    def _get_completed_process_batch_payloads(
        self,
    ) -> dict[tuple[str, str, int, int, int], tuple[int, SerializedWorkItem]]:
        """Return payload tombstones for batch done events drained before completion."""
        completed_payloads = getattr(self, "_completed_process_batch_payloads", None)
        if completed_payloads is None:
            completed_payloads = {}
            self._completed_process_batch_payloads = completed_payloads
        return completed_payloads

    def _capture_completed_batch_payload(self, event: dict[str, Any]) -> None:
        """Preserve batch payload ownership before child done events pop registry."""
        if event.get("kind") != "done":
            return
        if not self._get_active_process_batch_ids():
            return
        key = event.get("key")
        if not isinstance(key, tuple) or not key:
            return
        payload = self._in_flight_registry.get(key)
        if payload is None:
            return
        self._get_completed_process_batch_payloads()[
            self._completion_payload_identity(payload)
        ] = (int(key[0]), dict(payload))

    def _discard_completed_batch_payload_for_completion(
        self,
        event: CompletionEvent,
    ) -> None:
        """Discard a preserved batch payload after its completion becomes visible."""
        self._get_completed_process_batch_payloads().pop(
            self._completion_event_identity(event),
            None,
        )

    def _apply_batch_start_event(self, event: dict[str, Any]) -> bool:
        """Seed per-item recovery registry from parent manifest on batch_start ack."""
        if event.get("kind") != "batch_start":
            return False
        batch_id = event.get("batch_id")
        worker_index = event.get("worker_index")
        if batch_id is None or worker_index is None:
            return True
        manifest = self._get_process_batch_manifests().pop(str(batch_id), None)
        if manifest is None:
            return True
        manifest_worker_index = int(manifest.get("worker_index", worker_index))
        for payload in manifest.get("items", []):
            if not isinstance(payload, dict):
                continue
            key = (
                manifest_worker_index,
                str(payload["topic"]),
                int(payload["partition"]),
                int(payload["offset"]),
            )
            self._in_flight_registry[key] = dict(payload)
        return True

    def _apply_control_event(self, event: dict[str, Any]) -> bool:
        """Buffer child-originated fatal control events for the broker loop."""
        if event.get("kind") != "control":
            return False
        if event.get("control_kind") != "fatal":
            return True
        error_reason = str(event.get("error", "process_control_error"))
        if event.get("error_code") == "invalid_batch_worker_result":
            error: Exception = BatchWorkerContractError(error_reason)
        else:
            error = RuntimeError(error_reason)
        self._get_process_control_events().append(
            ExecutionControlEvent(kind="fatal", error=error)
        )
        return True

    def _apply_registry_event(self, event: dict[str, Any]) -> None:
        """Handle apply registry event within multiprocessing execution.

        Args:
            event: Completion or registry event being processed.

        """
        self._initialize_runtime_timing_state()
        if self._apply_control_event(event):
            return
        transport = getattr(self, "_transport", None)
        if transport is not None:
            transport.handle_registry_event(event)
        if self._apply_batch_start_event(event):
            return
        if self._recover_not_started_payloads(event):
            return
        self._capture_completed_batch_payload(event)
        ProcessRegistrySupport.apply_registry_event(
            event=event,
            in_flight_registry=self._in_flight_registry,
            record_main_to_worker_ipc=self._record_main_to_worker_ipc,
            record_worker_exec=self._record_worker_exec,
        )

    def _recover_not_started_payloads(self, event: dict[str, Any]) -> bool:
        """Requeue ordered route-batch tail payloads that a live worker skipped."""
        if event.get("kind") != "not_started":
            return False
        if event.get("_route_batch_pending_not_started") is False:
            return True
        payloads = event.get("payloads")
        if not isinstance(payloads, list):
            return True
        recovered_payloads = [
            dict(payload) for payload in payloads if isinstance(payload, dict)
        ]
        if not recovered_payloads:
            return True
        for index, payload in enumerate(recovered_payloads):
            try:
                self._requeue_recovered_payloads([payload])
            except Exception as requeue_exc:
                self._logger.error(
                    "Failed to requeue not_started route-batch tail payloads batch_id=%s: %s",
                    event.get("batch_id"),
                    requeue_exc,
                )
                for failed_payload in recovered_payloads[index:]:
                    self._emit_worker_recovery_failure(
                        -1,
                        failed_payload,
                        error="not_started_requeue_failed: %s" % requeue_exc,
                        attempt=self._config.max_retries,
                    )
                break
        return True

    def _pop_registry_payload_for_completion(
        self, event: CompletionEvent
    ) -> tuple[int, SerializedWorkItem] | None:
        """Remove and return registry ownership matching a completion event."""
        registry = getattr(self, "_in_flight_registry", None)
        if registry is None:
            return None
        for key, payload in list(registry.items()):
            if (
                str(payload.get("id")) == event.id
                and str(payload.get("topic")) == event.tp.topic
                and int(payload.get("partition")) == event.tp.partition
                and int(payload.get("offset")) == event.offset
                and int(payload.get("epoch")) == event.epoch
            ):
                registry.pop(key, None)
                return int(key[0]), dict(payload)
        return self._get_completed_process_batch_payloads().pop(
            self._completion_event_identity(event),
            None,
        )

    def _redispatch_retryable_batch_failures(
        self,
        *,
        batch_id: str,
        route_identity: tuple[Any, ...],
        results: list[CompletionEvent],
    ) -> list[CompletionEvent]:
        """Redispatch retryable failed batch subset and return visible completions."""
        visible_events: list[CompletionEvent] = []
        retry_items: list[WorkItem] = []
        retry_worker_index: int | None = None
        for event in results:
            if event.status != CompletionStatus.FAILURE:
                self._discard_completed_batch_payload_for_completion(event)
                visible_events.append(event)
                continue
            registry_entry = self._pop_registry_payload_for_completion(event)
            if registry_entry is None:
                visible_events.append(event)
                continue
            worker_index, payload = registry_entry
            attempts = int(payload.get("requeue_attempts", 0))
            if attempts >= self._config.max_retries:
                visible_events.append(
                    CompletionEvent(
                        id=event.id,
                        tp=event.tp,
                        offset=event.offset,
                        epoch=event.epoch,
                        status=event.status,
                        error=event.error,
                        attempt=self._config.max_retries,
                    )
                )
                continue
            payload["requeue_attempts"] = attempts + 1
            retry_items.append(_work_item_from_dict(payload))
            retry_worker_index = worker_index
        if not retry_items:
            return visible_events
        retry_route_identity = resolve_route_identity(retry_items[0])
        retry_batch = RouteBatch(
            batch_id=uuid.uuid4().hex,
            route_identity=route_identity,
            worker_index=retry_worker_index,
            items=retry_items,
        )
        self._record_process_batch_manifest(
            batch_id=retry_batch.batch_id,
            worker_index=stable_worker_index_for_route(
                retry_route_identity,
                self._config.process_config.process_count,
            ),
            items=retry_items,
        )
        try:
            dispatch_route_batch = getattr(self._transport, "dispatch_route_batch")
            dispatch_route_batch(
                retry_batch,
                route_identity=retry_route_identity,
                count_in_flight=False,
            )
        except Exception:
            self._get_process_batch_manifests().pop(retry_batch.batch_id, None)
            raise
        return visible_events

    def _drain_registry_events(self) -> None:
        """Drain registry events for multiprocessing execution."""
        self._drain_registry_event_queue()

    def _build_completion_buffer(self) -> ProcessCompletionBuffer:
        """Build the parent-side completion buffer around current engine state."""
        return ProcessCompletionBuffer(
            completion_queue=getattr(self, "_completion_queue", None),
            prefetched_events=self._prefetched_completion_events,
            seen_identities=self._seen_completion_identities,
            seen_identity_order=self._seen_completion_identity_order,
            decode_queue_item_events=self._decode_completion_queue_item_events,
            discard_registry_entry_for_completion=(
                self._discard_registry_entry_for_completion
            ),
            max_seen_identities=_MAX_SEEN_COMPLETION_IDENTITIES,
        )

    def _get_completion_buffer(self) -> ProcessCompletionBuffer:
        """Return a completion buffer matching the current test/runtime state."""
        prefetched_events = getattr(self, "_prefetched_completion_events", None)
        if prefetched_events is None:
            prefetched_events = deque()
            self._prefetched_completion_events = prefetched_events
        seen_identities = getattr(self, "_seen_completion_identities", None)
        if seen_identities is None:
            seen_identities = set()
            self._seen_completion_identities = seen_identities
        seen_identity_order = getattr(self, "_seen_completion_identity_order", None)
        if seen_identity_order is None:
            seen_identity_order = deque()
            self._seen_completion_identity_order = seen_identity_order

        completion_queue = getattr(self, "_completion_queue", None)
        buffer = getattr(self, "_completion_buffer", None)
        if not isinstance(buffer, ProcessCompletionBuffer) or not buffer.uses(
            completion_queue=completion_queue,
            prefetched_events=prefetched_events,
            seen_identities=seen_identities,
            seen_identity_order=seen_identity_order,
        ):
            buffer = self._build_completion_buffer()
            self._completion_buffer = buffer
        return buffer

    def _get_shutdown_support(self) -> ProcessShutdownSupport:
        """Return the shutdown support object, rebuilding it for __new__ tests."""
        support = getattr(self, "_shutdown_support", None)
        if not isinstance(support, ProcessShutdownSupport):
            support = ProcessShutdownSupport(_logger)
            self._shutdown_support = support
        return support

    def _get_worker_supervisor(self) -> ProcessWorkerSupervisor:
        """Return the worker supervisor, rebuilding it for __new__ tests."""
        supervisor = getattr(self, "_worker_supervisor", None)
        if not isinstance(supervisor, ProcessWorkerSupervisor):
            supervisor = ProcessWorkerSupervisor()
            self._worker_supervisor = supervisor
        return supervisor

    def _build_worker_supervisor_context(self) -> ProcessWorkerSupervisorContext:
        """Build a worker supervisor context over current engine state."""
        return ProcessWorkerSupervisorContext(
            workers=getattr(self, "_workers", []),
            transport=getattr(self, "_transport", None),
            max_retries=getattr(getattr(self, "_config", None), "max_retries", 0),
            logger=getattr(self, "_logger", _logger),
            is_shutdown=lambda: getattr(self, "_is_shutdown", False),
            drain_registry_events=self._drain_registry_events,
            prefetch_completed_events=self._prefetch_completed_events_from_queue,
            get_last_liveness_check=lambda: getattr(
                self,
                "_last_worker_liveness_check",
                0.0,
            ),
            set_last_liveness_check=self._set_last_worker_liveness_check,
            liveness_interval_seconds=getattr(
                self,
                "_worker_liveness_check_interval_seconds",
                0.0,
            ),
            recover_dead_worker_items=self._recover_dead_worker_items,
            start_worker=self._start_worker,
            requeue_recovered_payloads=self._requeue_recovered_payloads,
            emit_worker_recovery_failure=self._emit_worker_recovery_failure,
        )

    def _prefetch_completed_events_from_queue(self) -> int:
        """Build prefetch completed events from queue.

        Returns:
            Computed integer value.

        """
        with self._get_registry_state_lock():
            return self._get_completion_buffer().drain_queue()

    def _prefetch_completion_queue_item(self, raw_event: Any) -> bool:
        """Decode one completion queue item and prefetch any visible events."""
        return self._get_completion_buffer().prefetch_queue_item(raw_event)

    def _prefetch_completion_event(self, event: CompletionEvent) -> bool:
        """Handle prefetch completion event within multiprocessing execution.

        Args:
            event: Completion or registry event being processed.

        """
        return self._get_completion_buffer().prefetch_event(event)

    def _is_duplicate_completion_event(self, event: CompletionEvent) -> bool:
        """Return True when this item completion was already surfaced."""
        return self._get_completion_buffer().is_duplicate_event(event)

    @staticmethod
    def _is_synthetic_failure_completion_event(event: CompletionEvent) -> bool:
        """Return whether a failure has no stable work identity to dedupe by."""
        return ProcessCompletionBuffer.is_synthetic_failure_event(event)

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
        drained_completion = self._get_completion_buffer().drain_queue()

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
        return await self._get_shutdown_support().drain_until_stable_empty(
            drain_once=self._drain_shutdown_ipc_once,
            max_seconds=max_seconds,
            stable_empty_passes=stable_empty_passes,
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
        )

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
        batch_accumulator = getattr(self, "_batch_accumulator", _NoOpBatchAccumulator())
        base_metrics = batch_accumulator.snapshot()
        worker_diagnostics = self._snapshot_worker_diagnostics()
        transport_mode = self._get_transport_mode()
        support_state = "bounded"
        timer_flush_supported = False
        demand_flush_supported = False
        recycle_supported = False
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
            items_per_input_ipc = (
                self._input_ipc_item_count / self._input_ipc_count
                if self._input_ipc_count > 0
                else None
            )
            items_per_completion_ipc = (
                self._completion_ipc_item_count / self._completion_ipc_count
                if self._completion_ipc_count > 0
                else None
            )
            route_batch_size_avg = (
                self._route_batch_item_count / self._route_batch_count
                if self._route_batch_count > 0
                else None
            )
            route_batch_size_max = (
                self._route_batch_size_max if self._route_batch_count > 0 else None
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
                        items_per_input_ipc=items_per_input_ipc,
                        items_per_completion_ipc=items_per_completion_ipc,
                        route_batch_count=self._route_batch_count,
                        route_batch_item_count=self._route_batch_item_count,
                        route_batch_size_avg=route_batch_size_avg,
                        route_batch_size_max=route_batch_size_max,
                        completion_item_payload_count=(
                            self._completion_item_payload_count
                        ),
                        completion_batch_payload_count=(
                            self._completion_batch_payload_count
                        ),
                    )
                ),
                workers=worker_diagnostics,
            )

    def _snapshot_pending_worker_loads(self) -> list[int]:
        """Return pending per-worker transport loads when transport exists."""
        transport = getattr(self, "_transport", None)
        snapshot_loads = getattr(transport, "snapshot_pending_worker_loads", None)
        if not callable(snapshot_loads):
            return []
        worker_loads = snapshot_loads()
        if not isinstance(worker_loads, list):
            return []
        return [load if isinstance(load, int) else 0 for load in worker_loads]

    def _snapshot_worker_diagnostics(self) -> EngineWorkerDiagnostics:
        """Return process worker capacity diagnostics without reading private callers."""
        worker_count = self._config.process_config.process_count
        pending_loads = self._snapshot_pending_worker_loads()
        pending_by_worker = [0 for _ in range(worker_count)]
        for worker_idx, load in enumerate(pending_loads[:worker_count]):
            pending_by_worker[worker_idx] = load

        executing_by_worker = [0 for _ in range(worker_count)]
        with self._registry_state_lock:
            for key in self._in_flight_registry:
                worker_idx = key[0]
                if isinstance(worker_idx, int) and 0 <= worker_idx < worker_count:
                    executing_by_worker[worker_idx] += 1

        executing = sum(1 for load in executing_by_worker if load > 0)
        admitted = sum(
            1
            for worker_idx, load in enumerate(pending_by_worker)
            if load > 0 and executing_by_worker[worker_idx] == 0
        )
        top_k_loads = sorted(
            (
                executing_by_worker[worker_idx] + pending_by_worker[worker_idx]
                for worker_idx in range(worker_count)
                if executing_by_worker[worker_idx] + pending_by_worker[worker_idx] > 0
            ),
            reverse=True,
        )[:10]
        return EngineWorkerDiagnostics(
            total=worker_count,
            executing=executing,
            admitted=admitted,
            top_k_loads=top_k_loads,
        )

    async def submit(self, work_item: WorkItem) -> None:
        """제출된 작업 항목을 태스크 큐에 넣습니다.

        Args:
            work_item: Work item being scheduled or processed.

        """
        if getattr(self, "_is_shutdown", False):
            raise RuntimeError("ProcessExecutionEngine is shutting down")
        self._drain_registry_events()
        self._recover_expired_batch_start_acks(now=time.monotonic())
        await asyncio.to_thread(self._ensure_workers_alive, force=True)
        await self._transport.submit_work_item(
            work_item,
            route_identity=resolve_route_identity(work_item),
            count_in_flight=True,
        )
        self._record_input_ipc(1)

    async def submit_batch(self, work_items: list[WorkItem]) -> None:
        """Submit a route-local work batch through the worker-pipe transport."""
        if getattr(self, "_is_shutdown", False):
            raise RuntimeError("ProcessExecutionEngine is shutting down")
        if not work_items:
            await super().submit_batch(work_items)
            return

        route_identity = resolve_route_identity(work_items[0])
        if any(
            resolve_route_identity(item) != route_identity for item in work_items[1:]
        ):
            await super().submit_batch(work_items)
            return

        self._drain_registry_events()
        self._recover_expired_batch_start_acks(now=time.monotonic())
        await asyncio.to_thread(self._ensure_workers_alive, force=True)
        worker_index = stable_worker_index_for_route(
            route_identity,
            self._config.process_config.process_count,
        )
        route_batch = RouteBatch(
            batch_id=uuid.uuid4().hex,
            route_identity=(
                route_identity.topic,
                route_identity.partition,
                route_identity.key,
            ),
            worker_index=worker_index,
            items=work_items,
        )
        dispatch_route_batch = getattr(self._transport, "dispatch_route_batch")
        self._record_process_batch_manifest(
            batch_id=route_batch.batch_id,
            worker_index=worker_index,
            items=work_items,
        )
        try:
            await asyncio.to_thread(
                dispatch_route_batch,
                route_batch,
                route_identity=route_identity,
                count_in_flight=True,
            )
        except Exception:
            self._process_batch_manifests.pop(route_batch.batch_id, None)
            raise
        self._record_input_ipc(len(work_items), route_batch=True)

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

        completion_buffer = self._get_completion_buffer()
        completed_events: List[CompletionEvent] = completion_buffer.poll_prefetched(
            batch_limit=batch_limit,
            decrement_in_flight=self._decrement_in_flight_count,
        )
        if getattr(self, "_is_shutdown", False):
            return completed_events
        completed_events.extend(
            completion_buffer.poll_available(
                batch_limit=batch_limit - len(completed_events),
                decrement_in_flight=self._decrement_in_flight_count,
                logger=_logger,
            )
        )
        return completed_events

    async def poll_control_events(
        self, batch_limit: int = 1000
    ) -> List[ExecutionControlEvent]:
        """Poll parent-side process control events from worker registry IPC."""
        if not getattr(self, "_is_shutdown", False) and hasattr(
            self, "_registry_event_queue"
        ):
            self._drain_registry_events()
        control_events = self._get_process_control_events()
        drained: List[ExecutionControlEvent] = []
        while len(drained) < batch_limit and control_events:
            drained.append(control_events.popleft())
        return drained

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
            return self._get_completion_buffer().has_prefetched_events

        await asyncio.to_thread(self._ensure_workers_alive)
        self._drain_registry_events()

        completion_buffer = self._get_completion_buffer()
        if completion_buffer.has_prefetched_events:
            return True

        deadline = (
            None
            if timeout_seconds is None
            else time.monotonic() + max(timeout_seconds, 0)
        )
        while True:
            try:
                raw_event = self._completion_queue.get_nowait()
            except queue.Empty:
                if completion_buffer.has_prefetched_events:
                    return True
                if deadline is None:
                    remaining_timeout = None
                else:
                    remaining_timeout = deadline - time.monotonic()
                    if remaining_timeout <= 0:
                        return False
                try:
                    raw_event = await asyncio.to_thread(
                        self._completion_queue.get,
                        True,
                        remaining_timeout,
                    )
                except queue.Empty:
                    return completion_buffer.has_prefetched_events

            if completion_buffer.prefetch_queue_item(raw_event):
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
        self._input_ipc_count = 0
        self._input_ipc_item_count = 0
        self._completion_ipc_count = 0
        self._completion_ipc_item_count = 0
        self._route_batch_count = 0
        self._route_batch_item_count = 0
        self._route_batch_size_max = 0
        self._completion_item_payload_count = 0
        self._completion_batch_payload_count = 0

    def _record_input_ipc(self, item_count: int, *, route_batch: bool = False) -> None:
        """Record parent-to-worker IPC item counts."""
        if item_count <= 0:
            return
        self._initialize_runtime_timing_state()
        with self._runtime_timing_lock:
            self._input_ipc_count += 1
            self._input_ipc_item_count += item_count
            if route_batch:
                self._route_batch_count += 1
                self._route_batch_item_count += item_count
                self._route_batch_size_max = max(self._route_batch_size_max, item_count)

    def _record_completion_ipc(
        self,
        item_count: int,
        *,
        batch_payload: bool = False,
    ) -> None:
        """Record worker-to-parent completion IPC item counts."""
        if item_count <= 0:
            return
        self._initialize_runtime_timing_state()
        with self._runtime_timing_lock:
            self._completion_ipc_count += 1
            self._completion_ipc_item_count += item_count
            if batch_payload:
                self._completion_batch_payload_count += 1
            else:
                self._completion_item_payload_count += 1

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
        events = self._decode_completion_queue_item_events(raw_event)
        if len(events) != 1:
            raise ValueError("completion_queue_item_expanded_to_multiple_events")
        return events[0]

    def _decode_completion_queue_item_events(
        self, raw_event: Any
    ) -> list[CompletionEvent]:
        """Decode one completion queue item into item-level completion events."""
        if isinstance(raw_event, (bytes, bytearray)):
            msgpack_max_bytes = self._completion_msgpack_max_bytes()
            payload = _decode_msgpack_payload(
                raw_event,
                msgpack_max_bytes,
            )
            if not isinstance(payload, dict):
                raise ValueError("invalid_completion_payload_type")
            if payload.get("kind") == BATCH_COMPLETION_KIND:
                decoded_payload = _decode_batch_completion_payload(
                    payload,
                    max_bytes=msgpack_max_bytes,
                )
                timing = decoded_payload.get("timing", {})
                completion_enqueued_at = timing.get("completion_enqueued_at")
                if isinstance(completion_enqueued_at, (int, float)):
                    self._record_worker_to_main_ipc(
                        time.monotonic() - float(completion_enqueued_at)
                    )
                batch_completion = _batch_completion_from_dict(
                    decoded_payload["completion"]
                )
                batch_id = str(batch_completion.batch_id)
                if self._quarantine_stale_batch_completion(batch_id):
                    self._record_completion_ipc(0, batch_payload=True)
                    return []
                results = self._redispatch_retryable_batch_failures(
                    batch_id=batch_id,
                    route_identity=batch_completion.route_identity,
                    results=list(batch_completion.results),
                )
                self._retire_active_process_batch_id(batch_id)
                self._record_completion_ipc(len(results), batch_payload=True)
                return results
            completion_enqueued_at = payload.get("completion_enqueued_at")
            if isinstance(completion_enqueued_at, (int, float)):
                self._record_worker_to_main_ipc(
                    time.monotonic() - float(completion_enqueued_at)
                )
            self._record_completion_ipc(1, batch_payload=False)
            return [_completion_event_from_dict(payload)]
        return [raw_event]

    def _completion_msgpack_max_bytes(self) -> int:
        """Return completion decode msgpack byte limit without config construction."""
        config = getattr(self, "_config", None)
        process_config = getattr(config, "process_config", None)
        msgpack_max_bytes = getattr(process_config, "msgpack_max_bytes", None)
        if isinstance(msgpack_max_bytes, int) and msgpack_max_bytes > 0:
            return msgpack_max_bytes
        return _DEFAULT_MSGPACK_MAX_BYTES

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
        return "worker_pipes"

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

        await self._get_shutdown_support().shutdown(
            ProcessShutdownContext(
                workers=self._workers,
                batch_accumulator=self._batch_accumulator,
                transport=self._transport,
                task_queue=self._task_queue,
                completion_queue=self._completion_queue,
                registry_event_queue=self._registry_event_queue,
                log_listener=self._log_listener,
                prefetched_completion_events=self._prefetched_completion_events,
                in_flight_registry=self._in_flight_registry,
                worker_pid_by_index=self._worker_pid_by_index,
                drain_registry_events=self._drain_registry_events,
                drain_shutdown_ipc_once=self._drain_shutdown_ipc_once,
                join_worker=self._join_worker_with_escalation,
                set_in_flight_count=self._set_in_flight_count,
            ),
            monotonic=time.monotonic,
            sleep=asyncio.sleep,
        )

    def _increment_in_flight_count(self) -> None:
        """Increment in flight count for multiprocessing execution."""
        with self._in_flight_lock:
            self._in_flight_count += 1

    def _decrement_in_flight_count(self) -> None:
        """Decrement in-flight count after surfacing one completion event."""
        with self._in_flight_lock:
            self._in_flight_count -= 1

    def _set_in_flight_count(self, value: int) -> None:
        """Set in-flight count while holding the engine accounting lock."""
        with self._in_flight_lock:
            self._in_flight_count = value

    def _set_last_worker_liveness_check(self, value: float) -> None:
        """Set the last worker liveness scan timestamp."""
        self._last_worker_liveness_check = value
