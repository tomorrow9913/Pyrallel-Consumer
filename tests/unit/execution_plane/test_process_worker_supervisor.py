# -*- coding: utf-8 -*-
# File: tests/unit/execution_plane/test_process_worker_supervisor.py
# Role: Verifies process worker supervisor liveness, restart, recovery, and requeue orchestration.
# Extend here for dead-worker supervision behavior split from ProcessExecutionEngine.
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import Mock

from pyrallel_consumer.execution_plane.process_worker_supervisor import (
    ProcessWorkerSupervisor,
    ProcessWorkerSupervisorContext,
)


class _DeadWorker:
    exitcode = 17

    def is_alive(self) -> bool:
        return False


class _AliveWorker:
    exitcode = None

    def __init__(self) -> None:
        self.is_alive_calls = 0

    def is_alive(self) -> bool:
        self.is_alive_calls += 1
        return True


def _context(**overrides: Any) -> ProcessWorkerSupervisorContext:
    workers = overrides.pop("workers", [])
    state = {
        "last_liveness_check": overrides.pop("last_liveness_check", 0.0),
        "shutdown": overrides.pop("shutdown", False),
    }
    return ProcessWorkerSupervisorContext(
        workers=workers,
        transport=overrides.pop("transport", None),
        max_retries=overrides.pop("max_retries", 3),
        logger=overrides.pop("logger", logging.getLogger(__name__)),
        is_shutdown=overrides.pop("is_shutdown", lambda: state["shutdown"]),
        drain_registry_events=overrides.pop("drain_registry_events", lambda: None),
        prefetch_completed_events=overrides.pop(
            "prefetch_completed_events",
            lambda: 0,
        ),
        get_last_liveness_check=overrides.pop(
            "get_last_liveness_check",
            lambda: state["last_liveness_check"],
        ),
        set_last_liveness_check=overrides.pop(
            "set_last_liveness_check",
            lambda value: state.__setitem__("last_liveness_check", value),
        ),
        liveness_interval_seconds=overrides.pop("liveness_interval_seconds", 0.0),
        recover_dead_worker_items=overrides.pop(
            "recover_dead_worker_items",
            lambda _idx: [],
        ),
        start_worker=overrides.pop("start_worker", lambda _idx: Mock()),
        requeue_recovered_payloads=overrides.pop(
            "requeue_recovered_payloads",
            lambda _payloads: None,
        ),
        emit_worker_recovery_failure=overrides.pop(
            "emit_worker_recovery_failure",
            lambda *_args, **_kwargs: None,
        ),
    )


def test_ensure_workers_alive_restarts_before_requeueing_recovered_payload() -> None:
    # Given: a dead worker has one recoverable payload waiting for restart.
    supervisor = ProcessWorkerSupervisor()
    order: list[str] = []
    replacement_worker = _AliveWorker()
    recovered_payload = {"offset": 42, "requeue_attempts": 1}

    def start_worker(_idx: int) -> _AliveWorker:
        order.append("restart")
        return replacement_worker

    def requeue_recovered_payloads(_payloads: list[dict[str, int]]) -> None:
        order.append("requeue")

    context = _context(
        workers=[_DeadWorker()],
        recover_dead_worker_items=lambda _idx: [recovered_payload],
        start_worker=start_worker,
        requeue_recovered_payloads=requeue_recovered_payloads,
    )

    # When: the supervisor runs a liveness scan.
    supervisor.ensure_workers_alive(context)

    # Then: the worker is replaced before recovered work is requeued.
    assert order == ["restart", "requeue"]
    assert context.workers == [replacement_worker]


def test_ensure_workers_alive_emits_recovery_failures_when_restart_fails() -> None:
    # Given: recovered payloads exist but worker restart raises an error.
    supervisor = ProcessWorkerSupervisor()
    emitted: list[tuple[int, str, int]] = []
    recovered_payload = {"offset": 43, "requeue_attempts": 1}

    def emit_failure(idx: int, payload: dict[str, Any], **kwargs: Any) -> None:
        emitted.append((idx, kwargs["error"], kwargs["attempt"]))
        assert payload is recovered_payload

    def start_worker(_idx: int) -> None:
        raise RuntimeError("spawn failed")

    context = _context(
        workers=[_DeadWorker()],
        recover_dead_worker_items=lambda _idx: [recovered_payload],
        start_worker=start_worker,
        emit_worker_recovery_failure=emit_failure,
    )

    # When: the supervisor cannot restart the dead worker.
    supervisor.ensure_workers_alive(context)

    # Then: recovered payloads become terminal restart-failure completions.
    assert emitted == [(0, "worker_restart_failed: spawn failed", 3)]


def test_liveness_scan_throttle_still_drains_visible_events() -> None:
    # Given: liveness scan is throttled but visible queues may contain events.
    supervisor = ProcessWorkerSupervisor()
    worker = _AliveWorker()
    drained: list[str] = []

    def drain_registry_events() -> None:
        drained.append("registry")

    def prefetch_completed_events() -> int:
        drained.append("completion")
        return 0

    context = _context(
        workers=[worker],
        liveness_interval_seconds=10.0,
        last_liveness_check=1_000_000_000.0,
        drain_registry_events=drain_registry_events,
        prefetch_completed_events=prefetch_completed_events,
    )

    # When: the liveness cadence says it is too soon to scan workers.
    supervisor.ensure_workers_alive(
        context,
        force=False,
    )

    # Then: visible events are drained but worker liveness is not checked.
    assert drained == ["registry", "completion"]
    assert worker.is_alive_calls == 0


def test_filter_recoverable_pending_pipe_dispatches_caps_retry_budget() -> None:
    # Given: one pending dispatch is already at the retry limit.
    supervisor = ProcessWorkerSupervisor()
    emitted: list[tuple[int, int]] = []
    maxed_payload = {"offset": 44, "requeue_attempts": 3}
    retryable_payload = {"offset": 45, "requeue_attempts": 2}
    context = _context(
        max_retries=3,
        emit_worker_recovery_failure=lambda idx, payload, **_kwargs: emitted.append(
            (idx, payload["offset"])
        ),
    )

    # When: pending dispatches are filtered for recovery.
    recoverable = supervisor.filter_recoverable_pending_pipe_dispatches(
        context,
        0,
        [maxed_payload, retryable_payload],
    )

    # Then: maxed payload emits a failure and retryable payload is incremented.
    assert recoverable == [{"offset": 45, "requeue_attempts": 3}]
    assert emitted == [(0, 44)]
