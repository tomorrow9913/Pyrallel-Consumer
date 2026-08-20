# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_lifecycle_support.py
# Role: Verifies lifecycle orchestration outside BrokerPoller.
# Extend here when start, stop, wait-closed, or cleanup wiring moves.

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from pyrallel_consumer.control_plane.broker_lifecycle_support import (
    BrokerLifecycleSupport,
)


def _make_support(
    *,
    running: bool = False,
    shutdown_event: asyncio.Event | None = None,
    consumer_task=None,
    completion_monitor_task=None,
    task_lifecycle_support=None,
    shutdown_policy: str = "force",
    fatal_error: Exception | None = None,
):
    """Create BrokerLifecycleSupport with mutable lifecycle state."""
    if shutdown_event is None:
        shutdown_event = asyncio.Event()
    if task_lifecycle_support is None:
        task_lifecycle_support = MagicMock()
    state = {
        "running": running,
        "shutdown_event": shutdown_event,
        "consumer_task": consumer_task,
        "completion_monitor_task": completion_monitor_task,
        "event_loop": None,
        "fatal_error": fatal_error,
        "producer": None,
        "admin": None,
        "consumer": None,
        "defer_cleanup": False,
        "message_cache_size_bytes": 10,
    }
    kafka_config = SimpleNamespace(
        get_producer_config=MagicMock(return_value={"bootstrap.servers": "broker"}),
        get_admin_config=MagicMock(return_value={"bootstrap.servers": "broker"}),
        get_consumer_config=MagicMock(return_value={"group.id": "test"}),
        parallel_consumer=SimpleNamespace(
            strict_completion_monitor_enabled=True,
            commit_coordinator=SimpleNamespace(stop_drain_timeout_ms=50),
        ),
    )
    message_cache = {"cached": object()}
    pending_dlq_events = {"pending": object()}
    drain_shutdown_work = AsyncMock(return_value=True)
    drain_commit_coordinator = AsyncMock(return_value=True)
    consumer_operation_guard = MagicMock()
    consumer_operation_guard.run_off_event_loop = AsyncMock()

    def raise_if_failed() -> None:
        error = state["fatal_error"]
        if error is not None:
            state["fatal_error"] = None
            raise error

    support = BrokerLifecycleSupport(
        stop_lock=asyncio.Lock(),
        get_running=lambda: state["running"],
        set_running=lambda value: state.__setitem__("running", value),
        get_shutdown_event=lambda: state["shutdown_event"],
        set_shutdown_event=lambda value: state.__setitem__("shutdown_event", value),
        get_consumer_task=lambda: state["consumer_task"],
        set_consumer_task=lambda value: state.__setitem__("consumer_task", value),
        get_completion_monitor_task=lambda: state["completion_monitor_task"],
        set_completion_monitor_task=(
            lambda value: state.__setitem__("completion_monitor_task", value)
        ),
        set_event_loop=lambda value: state.__setitem__("event_loop", value),
        set_fatal_error=lambda value: state.__setitem__("fatal_error", value),
        get_kafka_config=lambda: kafka_config,
        get_consume_topic=lambda: "test-topic",
        set_runtime_clients=lambda producer, admin, consumer: state.update(
            {"producer": producer, "admin": admin, "consumer": consumer}
        ),
        get_producer=lambda: state["producer"],
        get_consumer=lambda: state["consumer"],
        get_pending_dlq_events=lambda: pending_dlq_events,
        get_message_cache=lambda: message_cache,
        set_message_cache_size_bytes=(
            lambda value: state.__setitem__("message_cache_size_bytes", value)
        ),
        get_task_lifecycle_support=lambda: task_lifecycle_support,
        get_shutdown_policy=lambda: shutdown_policy,
        get_shutdown_drain_timeout_seconds=lambda: 0.25,
        get_consumer_task_stop_timeout_seconds=lambda: 0.1,
        set_defer_consumer_cleanup_for_stop=(
            lambda value: state.__setitem__("defer_cleanup", value)
        ),
        raise_if_failed=raise_if_failed,
        drain_shutdown_work=drain_shutdown_work,
        drain_commit_coordinator_for_shutdown=drain_commit_coordinator,
        consumer_operation_guard=consumer_operation_guard,
        on_assign=lambda *_args, **_kwargs: None,
        on_revoke=lambda *_args, **_kwargs: None,
        run_consumer=AsyncMock(),
        run_completion_monitor=AsyncMock(),
        logger=MagicMock(),
    )
    return support, {
        "state": state,
        "kafka_config": kafka_config,
        "message_cache": message_cache,
        "pending_dlq_events": pending_dlq_events,
        "task_lifecycle_support": task_lifecycle_support,
        "drain_shutdown_work": drain_shutdown_work,
        "drain_commit_coordinator": drain_commit_coordinator,
        "consumer_operation_guard": consumer_operation_guard,
    }


@pytest.mark.asyncio
async def test_lifecycle_support_starts_runtime_clients_and_tasks() -> None:
    # Given: task lifecycle support can create runtime clients and tasks.
    task_lifecycle_support = MagicMock()
    producer = MagicMock()
    admin = MagicMock()
    consumer = MagicMock()
    consumer_task = MagicMock()
    completion_monitor_task = MagicMock()
    task_lifecycle_support.start_runtime.return_value = (
        producer,
        admin,
        consumer,
        consumer_task,
        completion_monitor_task,
    )
    support, doubles = _make_support(task_lifecycle_support=task_lifecycle_support)

    # When: lifecycle start is invoked.
    await support.start()

    # Then: clients, task handles, and running state are installed together.
    assert doubles["state"]["running"] is True
    assert doubles["state"]["producer"] is producer
    assert doubles["state"]["admin"] is admin
    assert doubles["state"]["consumer"] is consumer
    assert doubles["state"]["consumer_task"] is consumer_task
    assert doubles["state"]["completion_monitor_task"] is completion_monitor_task
    task_lifecycle_support.start_runtime.assert_called_once()


@pytest.mark.asyncio
async def test_lifecycle_support_graceful_stop_drains_then_cleans_up() -> None:
    # Given: a running poller has a consumer task and graceful shutdown policy.
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    task_lifecycle_support = MagicMock()
    task_lifecycle_support.stop_runtime = AsyncMock()
    cleanup = AsyncMock()
    support, doubles = _make_support(
        running=True,
        shutdown_event=shutdown_event,
        consumer_task=MagicMock(),
        task_lifecycle_support=task_lifecycle_support,
        shutdown_policy="graceful",
    )

    # When: lifecycle stop is invoked.
    await support.stop(cleanup=cleanup)

    # Then: the consumer task is stopped, graceful drain runs, and cleanup is deferred until after drain.
    assert doubles["state"]["running"] is False
    assert doubles["state"]["consumer_task"] is None
    assert doubles["state"]["defer_cleanup"] is False
    task_lifecycle_support.stop_runtime.assert_awaited_once()
    doubles["drain_shutdown_work"].assert_awaited_once_with(timeout_seconds=0.25)
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_lifecycle_support_cleanup_drains_closes_and_clears_ledgers() -> None:
    # Given: producer, consumer, DLQ cache, and pending DLQ ledgers exist.
    producer = MagicMock()
    consumer = MagicMock()
    support, doubles = _make_support()
    doubles["state"]["producer"] = producer
    doubles["state"]["consumer"] = consumer

    # When: lifecycle cleanup runs.
    await support.cleanup_runtime()

    # Then: coordinator drain precedes client close and shutdown ledgers are cleared.
    doubles["drain_commit_coordinator"].assert_awaited_once()
    producer.flush.assert_called_once_with(timeout=5)
    doubles["consumer_operation_guard"].run_off_event_loop.assert_awaited_once_with(
        consumer.close
    )
    assert doubles["message_cache"] == {}
    assert doubles["pending_dlq_events"] == {}
    assert doubles["state"]["message_cache_size_bytes"] == 0


@pytest.mark.asyncio
async def test_lifecycle_support_wait_closed_reraises_terminal_error() -> None:
    # Given: shutdown is already complete but a fatal error is stored.
    shutdown_event = asyncio.Event()
    shutdown_event.set()
    support, _ = _make_support(
        running=False,
        shutdown_event=shutdown_event,
        consumer_task=None,
        fatal_error=RuntimeError("closed-boom"),
    )

    # When/Then: wait_closed surfaces the stored terminal error.
    with pytest.raises(RuntimeError, match="closed-boom"):
        await support.wait_closed()
