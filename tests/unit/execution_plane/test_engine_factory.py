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


def sync_dummy_worker(work_item: WorkItem):
    return None


def test_create_async_execution_engine():
    config = ExecutionConfig(mode="async")
    engine = create_execution_engine(config, dummy_worker)  # Pass dummy_worker
    assert isinstance(engine, AsyncExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_process_execution_engine(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(mode="process")
    engine = create_execution_engine(config, sync_dummy_worker)
    assert isinstance(engine, ProcessExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_async_execution_engine_rejects_sync_worker():
    config = ExecutionConfig(mode="async")

    with pytest.raises(TypeError, match="async worker"):
        create_execution_engine(config, sync_dummy_worker)


def test_create_process_execution_engine_rejects_async_worker():
    config = ExecutionConfig(mode="process")

    with pytest.raises(TypeError, match="synchronous worker"):
        create_execution_engine(config, dummy_worker)


def test_create_process_execution_engine_rejects_non_picklable_worker():
    config = ExecutionConfig(mode="process")

    def nested_worker(work_item: WorkItem):
        return work_item

    with pytest.raises(TypeError, match="picklable worker"):
        create_execution_engine(config, nested_worker)


def test_create_execution_engine_unknown_mode_config_validation():
    with pytest.raises(ValidationError, match="not a valid ExecutionMode"):
        ExecutionConfig(mode="unknown")  # Assert Validation during config creation
