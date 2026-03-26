from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Awaitable


class BrokerTaskLifecycleSupport:
    def __init__(
        self,
        *,
        producer_factory: Callable[[dict[str, Any]], Any],
        admin_factory: Callable[[dict[str, Any]], Any],
        consumer_factory: Callable[[dict[str, Any]], Any],
        task_factory: Callable[[Awaitable[Any], str | None], Any],
    ) -> None:
        self._producer_factory = producer_factory
        self._admin_factory = admin_factory
        self._consumer_factory = consumer_factory
        self._task_factory = task_factory

    def start_runtime(
        self,
        *,
        consume_topic: str,
        producer_conf: dict[str, Any],
        admin_conf: dict[str, Any],
        consumer_conf: dict[str, Any],
        on_assign: Callable[..., None],
        on_revoke: Callable[..., None],
        consumer_loop_coro_factory: Callable[[], Awaitable[Any]],
        completion_monitor_coro_factory: Callable[[], Awaitable[Any]],
        strict_completion_monitor_enabled: bool,
    ) -> tuple[Any, Any, Any, Any, Any | None]:
        producer = self._producer_factory(producer_conf)
        admin = self._admin_factory(admin_conf)
        consumer = self._consumer_factory(consumer_conf)
        consumer.subscribe(
            [consume_topic],
            on_assign=on_assign,
            on_revoke=on_revoke,
        )
        completion_monitor_task = None
        if strict_completion_monitor_enabled:
            completion_monitor_task = self._task_factory(
                completion_monitor_coro_factory(),
                None,
            )
        consumer_task = self._task_factory(
            consumer_loop_coro_factory(), "broker-poller-loop"
        )
        return producer, admin, consumer, consumer_task, completion_monitor_task

    async def stop_runtime(
        self,
        *,
        consumer_task: Any,
        shutdown_event: asyncio.Event,
        timeout_seconds: float,
        wait_for: Callable[[Any, float], Awaitable[Any]] | None = None,
        gather: Callable[..., Awaitable[Any]] | None = None,
    ) -> None:
        if wait_for is None:
            wait_for = asyncio.wait_for
        if gather is None:
            gather = asyncio.gather
        try:
            await wait_for(consumer_task, timeout_seconds)
        except asyncio.TimeoutError:
            consumer_task.cancel()
            await gather(consumer_task, return_exceptions=True)
        await shutdown_event.wait()

    async def wait_closed(
        self,
        *,
        shutdown_event: asyncio.Event,
        raise_if_failed: Callable[[], None],
    ) -> None:
        await shutdown_event.wait()
        raise_if_failed()
