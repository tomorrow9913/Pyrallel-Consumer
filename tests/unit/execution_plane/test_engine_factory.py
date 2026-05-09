import pytest
from pydantic_core._pydantic_core import ValidationError

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import OrderingMode, WorkItem  # Import WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.engine_factory import create_execution_engine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine
from pyrallel_consumer.execution_plane.worker_spec import (
    BatchWorkerRuntimeSpec,
    WorkerSpec,
)


# Dummy worker function for testing
async def dummy_worker(work_item: WorkItem):
    pass


def sync_dummy_worker(work_item: WorkItem):
    return None


async def async_batch_worker(work_items: list[WorkItem]):
    return None


def sync_batch_worker(work_items: list[WorkItem]):
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


def test_create_async_execution_engine_accepts_batch_worker_spec():
    # Given: an async execution config and a batch WorkerSpec with key-hash route policy.
    config = ExecutionConfig(mode="async")
    runtime = BatchWorkerRuntimeSpec.from_config(
        ordering_mode=OrderingMode.KEY_HASH,
        batch_worker_config=object(),
        max_retries=config.max_retries,
    )
    worker_spec = WorkerSpec.batch(async_batch_worker, runtime)

    # When: the execution engine is created through the WorkerSpec boundary.
    engine = create_execution_engine(config, worker_spec)

    # Then: async mode accepts an async batch worker and stores the underlying callable.
    assert isinstance(engine, AsyncExecutionEngine)
    assert engine._worker_fn is async_batch_worker


def test_create_process_execution_engine_accepts_picklable_batch_worker_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: a process execution config and a picklable sync batch WorkerSpec.
    monkeypatch.setattr(ProcessExecutionEngine, "_start_workers", lambda self: None)
    config = ExecutionConfig(mode="process")
    runtime = BatchWorkerRuntimeSpec.from_config(
        ordering_mode=OrderingMode.UNORDERED,
        batch_worker_config=object(),
        max_retries=config.max_retries,
    )
    worker_spec = WorkerSpec.batch(sync_batch_worker, runtime)

    # When: the execution engine is created through the WorkerSpec boundary.
    engine = create_execution_engine(config, worker_spec)

    # Then: process mode preserves the batch WorkerSpec for child batch invocation.
    assert isinstance(engine, ProcessExecutionEngine)
    assert engine._worker_fn is worker_spec


def test_create_execution_engine_rejects_sync_batch_worker_in_async_mode() -> None:
    # Given: an async config receives a sync batch WorkerSpec.
    config = ExecutionConfig(mode="async")
    runtime = BatchWorkerRuntimeSpec.from_config(
        ordering_mode=OrderingMode.KEY_HASH,
        batch_worker_config=object(),
        max_retries=config.max_retries,
    )
    worker_spec = WorkerSpec.batch(sync_batch_worker, runtime)

    # When/Then: async mode rejects the sync batch worker.
    with pytest.raises(TypeError, match="async batch worker"):
        create_execution_engine(config, worker_spec)


def test_batch_worker_runtime_spec_derives_and_validates_route_policy() -> None:
    # Given: ordering modes are converted into runtime specs.
    key_hash_spec = BatchWorkerRuntimeSpec.from_config(
        ordering_mode=OrderingMode.KEY_HASH,
        batch_worker_config=object(),
        max_retries=3,
    )
    unordered_spec = BatchWorkerRuntimeSpec.from_config(
        ordering_mode=OrderingMode.UNORDERED,
        batch_worker_config=object(),
        max_retries=3,
    )

    # When/Then: route policy is derived and direct mismatches are rejected.
    assert key_hash_spec.route_policy == "key_hash"
    assert unordered_spec.route_policy == "unordered"
    with pytest.raises(ValueError, match="route_policy"):
        BatchWorkerRuntimeSpec(
            ordering_mode=OrderingMode.KEY_HASH,
            batch_worker_config=object(),
            route_policy="unordered",
            max_retries=3,
        )
