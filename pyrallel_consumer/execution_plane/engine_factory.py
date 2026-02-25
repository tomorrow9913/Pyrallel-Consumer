from collections.abc import Callable  # Add Callable import
from typing import Any  # Add Any import

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import ExecutionMode, WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


def create_execution_engine(
    config: ExecutionConfig, worker_fn: Callable[[WorkItem], Any]
) -> BaseExecutionEngine:
    """
    실행 구성에 따라 적절한 실행 엔진을 생성합니다.

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

    if mode == ExecutionMode.ASYNC:
        return AsyncExecutionEngine(config=config, worker_fn=worker_fn)
    if mode == ExecutionMode.PROCESS:
        return ProcessExecutionEngine(config=config, worker_fn=worker_fn)
    raise ValueError(f"Unknown execution mode: {mode}")
