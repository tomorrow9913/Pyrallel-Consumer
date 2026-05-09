# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/process_worker_supervisor.py
# Role: Supervises dead process workers, recovery payload collection, restart, and requeue.
# Extend here for worker liveness/recovery orchestration; keep registry state rules in process_registry_support.
from __future__ import annotations

import logging
import time
from collections.abc import Callable, MutableSequence
from dataclasses import dataclass
from typing import Any

from pyrallel_consumer.execution_plane.process_codec import SerializedWorkItem


@dataclass(slots=True)
class ProcessWorkerSupervisorContext:
    """Runtime state and callbacks needed to supervise process workers."""

    workers: MutableSequence[Any]
    transport: Any
    max_retries: int
    logger: logging.Logger
    is_shutdown: Callable[[], bool]
    drain_registry_events: Callable[[], None]
    prefetch_completed_events: Callable[[], int]
    get_last_liveness_check: Callable[[], float]
    set_last_liveness_check: Callable[[float], None]
    liveness_interval_seconds: float
    recover_dead_worker_items: Callable[[int], list[SerializedWorkItem]]
    start_worker: Callable[[int], Any]
    requeue_recovered_payloads: Callable[[list[SerializedWorkItem]], None]
    emit_worker_recovery_failure: Callable[..., None]


class ProcessWorkerSupervisor:
    """Dead-worker liveness and recovery orchestration for process execution."""

    def ensure_workers_alive(
        self,
        context: ProcessWorkerSupervisorContext,
        *,
        force: bool = False,
    ) -> None:
        """Drain visible worker events and recover any dead workers."""
        if context.is_shutdown():
            return
        self.drain_visible_worker_events(context)
        if not self.should_run_liveness_scan(context, force=force):
            return

        for idx, exitcode in self.collect_dead_worker_recovery_candidates(context):
            to_requeue = self.collect_recoverable_worker_payloads(context, idx)
            context.logger.error(
                "ProcessWorker[%d] died (exitcode=%s). Restarting worker.",
                idx,
                exitcode,
            )
            if self.restart_dead_worker(context, idx, exitcode, to_requeue):
                self.publish_recovered_worker_payloads(context, idx, to_requeue)

    @staticmethod
    def drain_visible_worker_events(context: ProcessWorkerSupervisorContext) -> None:
        """Drain registry and completion events visible to the parent process."""
        context.drain_registry_events()
        context.prefetch_completed_events()

    @staticmethod
    def should_run_liveness_scan(
        context: ProcessWorkerSupervisorContext,
        *,
        force: bool,
        monotonic: Callable[[], float] | None = None,
    ) -> bool:
        """Return whether liveness scan should run for this call."""
        monotonic = monotonic or time.monotonic
        liveness_interval = context.liveness_interval_seconds
        if liveness_interval > 0 and not force:
            now = monotonic()
            last_check = context.get_last_liveness_check()
            if now - last_check < liveness_interval:
                return False
            context.set_last_liveness_check(now)
            return True
        if force:
            context.set_last_liveness_check(monotonic())
        return True

    @staticmethod
    def collect_dead_worker_recovery_candidates(
        context: ProcessWorkerSupervisorContext,
    ) -> list[tuple[int, Any]]:
        """Return worker indexes and exit codes for workers no longer alive."""
        candidates: list[tuple[int, Any]] = []
        for idx, worker in enumerate(context.workers):
            if not worker.is_alive():
                candidates.append((idx, worker.exitcode))
        return candidates

    def collect_recoverable_worker_payloads(
        self,
        context: ProcessWorkerSupervisorContext,
        idx: int,
    ) -> list[SerializedWorkItem]:
        """Collect in-flight and pending dispatch payloads recoverable for a worker."""
        to_requeue: list[SerializedWorkItem] = []
        try:
            to_requeue.extend(context.recover_dead_worker_items(idx))
        except Exception as recovery_exc:
            context.logger.error(
                "Failed to recover in-flight work from worker %d: %s",
                idx,
                recovery_exc,
            )
        try:
            to_requeue.extend(self.recover_pending_pipe_dispatches(context, idx))
        except Exception as recovery_exc:
            context.logger.error(
                "Failed to recover pending dispatches from worker %d: %s",
                idx,
                recovery_exc,
            )
        return to_requeue

    def restart_dead_worker(
        self,
        context: ProcessWorkerSupervisorContext,
        idx: int,
        exitcode: Any,
        recovered_payloads: list[SerializedWorkItem],
    ) -> bool:
        """Restart a dead worker, emitting terminal failures if restart fails."""
        try:
            new_worker = context.start_worker(idx)
        except Exception as restart_exc:
            context.logger.error(
                "Failed to restart worker %d after exitcode=%s: %s",
                idx,
                exitcode,
                restart_exc,
            )
            self.emit_worker_restart_failures(
                context,
                idx,
                recovered_payloads,
                restart_exc,
            )
            return False
        context.workers[idx] = new_worker
        return True

    def publish_recovered_worker_payloads(
        self,
        context: ProcessWorkerSupervisorContext,
        idx: int,
        payloads: list[SerializedWorkItem],
    ) -> None:
        """Requeue recovered payloads and emit failures for requeue errors."""
        if not payloads:
            return
        requeued_offsets: list[Any] = []
        for payload in payloads:
            try:
                context.requeue_recovered_payloads([payload])
                requeued_offsets.append(payload.get("offset"))
            except Exception as requeue_exc:
                context.logger.error(
                    "Failed to requeue recovered work from worker %d offset=%s: %s",
                    idx,
                    payload.get("offset"),
                    requeue_exc,
                )
                context.emit_worker_recovery_failure(
                    idx,
                    payload,
                    error="worker_requeue_failed: %s" % requeue_exc,
                    attempt=context.max_retries,
                )
        if requeued_offsets:
            context.logger.warning(
                "Requeued %d lost work item(s) offsets=%s from dead worker %d",
                len(requeued_offsets),
                requeued_offsets,
                idx,
            )

    def recover_pending_pipe_dispatches(
        self,
        context: ProcessWorkerSupervisorContext,
        idx: int,
    ) -> list[SerializedWorkItem]:
        """Recover pending transport dispatches for a dead worker."""
        transport = context.transport
        if transport is None:
            return []
        capabilities = getattr(transport, "capabilities", None)
        if capabilities is None or not capabilities.pending_dispatch_recovery:
            return []
        recovered_dispatches = transport.recover_pending_dispatches(idx)
        if recovered_dispatches:
            identities = [entry.identity for entry in recovered_dispatches]
            context.logger.warning(
                "Recovered %d pending worker-pipe dispatch(es) identities=%s",
                len(recovered_dispatches),
                identities,
            )
        return self.filter_recoverable_pending_pipe_dispatches(
            context,
            idx,
            [entry.payload for entry in recovered_dispatches],
        )

    @staticmethod
    def filter_recoverable_pending_pipe_dispatches(
        context: ProcessWorkerSupervisorContext,
        idx: int,
        payloads: list[SerializedWorkItem],
    ) -> list[SerializedWorkItem]:
        """Filter pending dispatch payloads by retry budget."""
        recoverable: list[SerializedWorkItem] = []
        for payload in payloads:
            attempts = payload.get("requeue_attempts", 0)
            if attempts >= context.max_retries:
                context.emit_worker_recovery_failure(
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

    @staticmethod
    def emit_worker_restart_failures(
        context: ProcessWorkerSupervisorContext,
        idx: int,
        payloads: list[SerializedWorkItem],
        restart_exc: Exception,
    ) -> None:
        """Emit terminal failures for payloads recovered before restart failed."""
        for payload in payloads:
            context.emit_worker_recovery_failure(
                idx,
                payload,
                error=f"worker_restart_failed: {restart_exc}",
                attempt=context.max_retries,
            )
