from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.mark.asyncio
async def test_start_runtime_skips_completion_monitor_when_disabled() -> None:
    from pyrallel_consumer.control_plane.broker_task_lifecycle_support import (
        BrokerTaskLifecycleSupport,
    )

    created_tasks: list[tuple[str | None, object]] = []

    def task_factory(coro, name=None):
        coro.close()
        task = MagicMock()
        created_tasks.append((name, task))
        return task

    support = BrokerTaskLifecycleSupport(
        producer_factory=lambda _conf: MagicMock(),
        admin_factory=lambda _conf: MagicMock(),
        consumer_factory=lambda _conf: MagicMock(),
        task_factory=task_factory,
    )

    producer, admin, consumer, consumer_task, completion_task = support.start_runtime(
        consume_topic="test-topic",
        producer_conf={"bootstrap.servers": "broker:9092"},
        admin_conf={"bootstrap.servers": "broker:9092"},
        consumer_conf={"group.id": "test-group"},
        on_assign=lambda *_args, **_kwargs: None,
        on_revoke=lambda *_args, **_kwargs: None,
        consumer_loop_coro_factory=AsyncMock(),
        completion_monitor_coro_factory=AsyncMock(),
        strict_completion_monitor_enabled=False,
    )

    assert producer is not None
    assert admin is not None
    assert consumer is not None
    assert consumer_task is created_tasks[0][1]
    assert completion_task is None
    consumer.subscribe.assert_called_once()


@pytest.mark.asyncio
async def test_stop_runtime_cancels_consumer_task_after_timeout() -> None:
    from pyrallel_consumer.control_plane.broker_task_lifecycle_support import (
        BrokerTaskLifecycleSupport,
    )

    consumer_task = MagicMock()
    consumer_task.cancel = MagicMock()
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    def producer_factory(_conf):
        return MagicMock()

    def admin_factory(_conf):
        return MagicMock()

    def consumer_factory(_conf):
        return MagicMock()

    def task_factory(_coro, name=None):
        del name
        return MagicMock()

    support = BrokerTaskLifecycleSupport(
        producer_factory=producer_factory,
        admin_factory=admin_factory,
        consumer_factory=consumer_factory,
        task_factory=task_factory,
    )

    async def wait_for_side_effect(_task, timeout):
        del timeout
        raise asyncio.TimeoutError

    gather = AsyncMock()
    await support.stop_runtime(
        consumer_task=consumer_task,
        shutdown_event=shutdown_event,
        timeout_seconds=5.0,
        wait_for=wait_for_side_effect,
        gather=gather,
    )

    consumer_task.cancel.assert_called_once_with()
    gather.assert_awaited_once_with(consumer_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_wait_closed_reraises_error_after_shutdown() -> None:
    from pyrallel_consumer.control_plane.broker_task_lifecycle_support import (
        BrokerTaskLifecycleSupport,
    )

    def producer_factory(_conf):
        return MagicMock()

    def admin_factory(_conf):
        return MagicMock()

    def consumer_factory(_conf):
        return MagicMock()

    def task_factory(_coro, name=None):
        del name
        return MagicMock()

    support = BrokerTaskLifecycleSupport(
        producer_factory=producer_factory,
        admin_factory=admin_factory,
        consumer_factory=consumer_factory,
        task_factory=task_factory,
    )
    shutdown_event = asyncio.Event()
    shutdown_event.set()

    with pytest.raises(RuntimeError, match="closed-boom"):
        await support.wait_closed(
            shutdown_event=shutdown_event,
            raise_if_failed=lambda: (_ for _ in ()).throw(RuntimeError("closed-boom")),
        )
