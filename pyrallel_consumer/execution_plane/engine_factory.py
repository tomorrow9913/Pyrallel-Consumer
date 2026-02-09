from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


def create_execution_engine(config: ExecutionConfig) -> BaseExecutionEngine:
    """
    실행 구성에 따라 적절한 실행 엔진을 생성합니다.

    Args:
        config (ExecutionConfig): 실행 구성

    Returns:
        BaseExecutionEngine: 생성된 실행 엔진 인스턴스
    Raises:
        ValueError: 알 수 없는 실행 모드가 지정된 경우 발생
    """
    if config.mode == "async":
        return AsyncExecutionEngine()
    elif config.mode == "process":
        return ProcessExecutionEngine()
    else:
        raise ValueError(f"Unknown execution mode: {config.mode}")
