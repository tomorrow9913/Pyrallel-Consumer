from typing import Any, Literal

import pytest

from pyrallel_consumer.config import KafkaConfig
from pyrallel_consumer.dto import ExecutionMode, OrderingMode


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("ordering_mode", "expected_worker_name"),
    [
        (OrderingMode.KEY_HASH, "_ProcessKeyHashWorker"),
        (OrderingMode.PARTITION, "_ProcessPartitionWorker"),
    ],
)
async def test_run_ordering_test_uses_sync_process_worker_by_default(
    monkeypatch: pytest.MonkeyPatch,
    ordering_mode: OrderingMode,
    expected_worker_name: str,
) -> None:
    from tests.e2e import test_ordering as module

    captured: dict[str, Any] = {}

    class _FakeManager:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb) -> Literal[False]:
            return False

        def list(self):
            return []

    class _FakeEngine:
        def __init__(self, config, worker_fn):
            captured["config_mode"] = config.mode
            captured["worker_fn"] = worker_fn

        async def shutdown(self) -> None:
            return None

    class _FakeWorkManager:
        def __init__(self, *args, **kwargs) -> None:
            return None

    class _FakePoller:
        ORDERING_MODE = None

        def __init__(self, *args, **kwargs) -> None:
            return None

        async def start(self) -> None:
            return None

        async def stop(self) -> None:
            return None

    class _FakeProcess:
        async def wait(self) -> None:
            return None

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _FakeProcess()

    async def _fake_wait_for_shared_results(
        *, shared_results, expected_count: int, timeout_seconds: float
    ) -> None:
        if ordering_mode == OrderingMode.PARTITION:
            shared_results.append((0, 0))
        else:
            shared_results.append(("key-0", 0))

    monkeypatch.setattr(module, "Manager", _FakeManager)
    monkeypatch.setattr(module, "ProcessExecutionEngine", _FakeEngine)
    monkeypatch.setattr(module, "WorkManager", _FakeWorkManager)
    monkeypatch.setattr(module, "BrokerPoller", _FakePoller)
    monkeypatch.setattr(
        module.asyncio,
        "create_subprocess_exec",
        _fake_create_subprocess_exec,
    )
    monkeypatch.setattr(
        module,
        "_wait_for_shared_results",
        _fake_wait_for_shared_results,
    )

    result_tracker, _ = await module.run_ordering_test(
        kafka_config=KafkaConfig(),
        ordering_mode=ordering_mode,
        execution_mode=ExecutionMode.PROCESS,
        num_messages=1,
        num_keys=1,
    )

    assert captured["config_mode"] == ExecutionMode.PROCESS
    assert captured["worker_fn"].__class__.__name__ == expected_worker_name
    assert result_tracker.get_processed_count() == 1
