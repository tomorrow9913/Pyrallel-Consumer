# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/engine_factory.py
# Role: Validates worker functions and chooses the concrete execution engine for a config.
# Extend here when adding execution modes or mode-specific worker validation.
import inspect
import pickle
from collections.abc import Callable
from typing import Any

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import ExecutionMode, WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


def _is_async_worker(worker_fn: Callable[[WorkItem], Any]) -> bool:
    """Return whether async worker holds for engine factory.

    Args:
        worker_fn: User worker callable invoked for each work item.

    Returns:
        True when the operation succeeds or the condition is met; otherwise False.

    """
    if inspect.iscoroutinefunction(worker_fn):
        return True

    call = getattr(worker_fn, "__call__", None)
    return inspect.iscoroutinefunction(call)


def _ensure_worker_matches_mode(
    config: ExecutionConfig,
    worker_fn: Callable[[WorkItem], Any],
) -> None:
    """Handle ensure worker matches mode within engine factory.

    Args:
        config: Execution or controller configuration used by this component.
        worker_fn: User worker callable invoked for each work item.

    Raises:
        TypeError: If the worker callable is incompatible with the selected execution mode.

    """
    mode = config.mode
    if isinstance(mode, str):
        mode = ExecutionMode(mode)

    if mode == ExecutionMode.ASYNC:
        if not _is_async_worker(worker_fn):
            raise TypeError("Async execution mode requires an async worker function")
        return

    if mode == ExecutionMode.PROCESS:
        if _is_async_worker(worker_fn):
            raise TypeError(
                "Process execution mode requires a synchronous worker function"
            )
        if getattr(config.process_config, "require_picklable_worker", False):
            try:
                pickle.dumps(worker_fn)
            except Exception as exc:
                raise TypeError(
                    "Process execution mode requires a picklable worker"
                ) from exc


def create_execution_engine(
    config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]
) -> BaseExecutionEngine:
    """실행 구성에 따라 적절한 실행 엔진을 생성합니다.

    Args:
        config (ExecutionConfig): 실행 구성
        worker_fn (Callable[[WorkItem], Any]): 사용자 정의 비동기 또는 동기 워커 함수.

    Returns:
        BaseExecutionEngine: 생성된 실행 엔진 인스턴스
    Raises:
        ValueError: 알 수 없는 실행 모드가 지정된 경우 발생

    """
    mode = config.mode
    if isinstance(mode, str):
        mode = ExecutionMode(mode)

    _ensure_worker_matches_mode(config, worker_fn)

    if mode == ExecutionMode.ASYNC:
        return AsyncExecutionEngine(config=config, worker_fn=worker_fn)
    if mode == ExecutionMode.PROCESS:
        return ProcessExecutionEngine(config=config, worker_fn=worker_fn)
    raise ValueError(f"Unknown execution mode: {mode}")
