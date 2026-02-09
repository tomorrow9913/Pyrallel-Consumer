import pytest
from pydantic_core._pydantic_core import ValidationError

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import WorkItem  # Import WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.engine_factory import create_execution_engine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine


# Dummy worker function for testing
async def dummy_worker(work_item: WorkItem):
    pass


def test_create_async_execution_engine():
    config = ExecutionConfig(mode="async")
    engine = create_execution_engine(config, dummy_worker)  # Pass dummy_worker
    assert isinstance(engine, AsyncExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_process_execution_engine():
    config = ExecutionConfig(mode="process")
    engine = create_execution_engine(config, dummy_worker)  # Pass dummy_worker
    assert isinstance(engine, ProcessExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_execution_engine_unknown_mode_config_validation():
    with pytest.raises(ValidationError, match="Input should be 'async' or 'process'"):
        ExecutionConfig(mode="unknown")  # Assert Validation during config creation
