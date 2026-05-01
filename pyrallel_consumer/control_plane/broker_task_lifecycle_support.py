# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_task_lifecycle_support.py
# Role: Starts and stops broker runtime resources and background tasks.
# Extend here for lifecycle wiring; keep polling and business decisions in broker_poller.py.
from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any, Awaitable


class BrokerTaskLifecycleSupport:
    """Group helper operations for broker task lifecycle management."""

    def __init__(
        self,
        *,
        producer_factory: Callable[[dict[str, Any]], Any],
        admin_factory: Callable[[dict[str, Any]], Any],
        consumer_factory: Callable[[dict[str, Any]], Any],
        task_factory: Callable[[Awaitable[Any], str | None], Any],
    ) -> None:
        """Initialize this component.

        Args:
            producer_factory: Factory used to create Kafka producers.
            admin_factory: Factory used to create Kafka admin clients.
            consumer_factory: Factory used to create Kafka consumers.
            task_factory: Factory used to create asyncio tasks.

        """
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
        """Start runtime for broker task lifecycle management.

        Args:
            consume_topic: Kafka topic being consumed.
            producer_conf: Kafka producer configuration.
            admin_conf: Kafka admin client configuration.
            consumer_conf: Kafka consumer configuration.
            on_assign: Kafka assignment callback.
            on_revoke: Kafka revoke callback.
            consumer_loop_coro_factory: Factory for the consumer-loop coroutine.
            completion_monitor_coro_factory: Factory for the completion-monitor coroutine.
            strict_completion_monitor_enabled: Whether to start a strict completion monitor task.

        Returns:
            tuple[Any, Any, Any, Any, Any | None] result produced by this function.

        """
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
        """Stop runtime for broker task lifecycle management.

        Args:
            consumer_task: Background consumer task to stop or await.
            shutdown_event: Event set when shutdown has completed.
            timeout_seconds: Maximum time to wait, in seconds; None waits indefinitely.
            wait_for: Awaitable timeout helper, defaults to asyncio.wait_for.
            gather: Awaitable gather helper, defaults to asyncio.gather.

        """
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
        """Wait for closed in broker task lifecycle management.

        Args:
            shutdown_event: Event set when shutdown has completed.
            raise_if_failed: Callback that raises any stored runtime failure.

        """
        await shutdown_event.wait()
        raise_if_failed()
