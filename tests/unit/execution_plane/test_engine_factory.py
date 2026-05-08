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
    # Given: inputs for `create async execution engine` are prepared.
    config = ExecutionConfig(mode="async")
    # When: the execution engine factory code path is exercised.
    engine = create_execution_engine(config, dummy_worker)  # Pass dummy_worker
    # Then: the expected `create async execution engine` behavior is asserted.
    assert isinstance(engine, AsyncExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_process_execution_engine(monkeypatch: pytest.MonkeyPatch):
    # Given: inputs for `create process execution engine` are prepared.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(mode="process")
    # When: the execution engine factory code path is exercised.
    engine = create_execution_engine(config, sync_dummy_worker)
    # Then: the expected `create process execution engine` behavior is asserted.
    assert isinstance(engine, ProcessExecutionEngine)
    assert isinstance(engine, BaseExecutionEngine)


def test_create_async_execution_engine_rejects_sync_worker():
    # Given: inputs for `create async execution engine rejects sync wo...` are prepared.
    config = ExecutionConfig(mode="async")

    # When: the execution engine factory code path is exercised.
    # Then: the expected `create async execution engine rejects sync wo...` behavior is asserted.
    with pytest.raises(TypeError, match="async worker"):
        create_execution_engine(config, sync_dummy_worker)


def test_create_process_execution_engine_rejects_async_worker():
    # Given: inputs for `create process execution engine rejects async...` are prepared.
    config = ExecutionConfig(mode="process")

    # When: the execution engine factory code path is exercised.
    # Then: the expected `create process execution engine rejects async...` behavior is asserted.
    with pytest.raises(TypeError, match="synchronous worker"):
        create_execution_engine(config, dummy_worker)


def test_create_process_execution_engine_rejects_non_picklable_worker():
    # Given: inputs for `create process execution engine rejects non p...` are prepared.
    config = ExecutionConfig(mode="process")

    def nested_worker(work_item: WorkItem):
        return work_item

    # When: the execution engine factory code path is exercised.
    # Then: the expected `create process execution engine rejects non p...` behavior is asserted.
    with pytest.raises(TypeError, match="picklable worker"):
        create_execution_engine(config, nested_worker)


def test_create_execution_engine_unknown_mode_config_validation():
    # Given: inputs for `create execution engine unknown mode config v...` are prepared.
    # When: the execution engine factory code path is exercised.
    # Then: the expected `create execution engine unknown mode config v...` behavior is asserted.
    with pytest.raises(ValidationError, match="not a valid ExecutionMode"):
        ExecutionConfig(mode="unknown")  # Assert Validation during config creation
