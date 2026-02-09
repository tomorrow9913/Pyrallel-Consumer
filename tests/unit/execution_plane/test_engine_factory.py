import pytest
from pydantic_core._pydantic_core import (
    ValidationError,  # Use the exact type from traceback
)

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.engine_factory import create_execution_engine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


def test_create_async_execution_engine():
    config = ExecutionConfig(mode="async")
    engine = create_execution_engine(config)
    assert isinstance(engine, AsyncExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_process_execution_engine():
    config = ExecutionConfig(mode="process")
    engine = create_execution_engine(config)
    assert isinstance(engine, ProcessExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_execution_engine_unknown_mode_config_validation():
    with pytest.raises(ValidationError, match="Input should be 'async' or 'process'"):
        ExecutionConfig(mode="unknown")  # Assert Validation during config creation
