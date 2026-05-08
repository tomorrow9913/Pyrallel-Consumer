# -*- coding: utf-8 -*-
# File: pyrallel_consumer/control_plane/broker_lifecycle_support.py
# Role: Coordinates BrokerPoller start, stop, wait-closed, and cleanup orchestration.
# Extend here for lifecycle entrypoint wiring; keep consumer-loop polling in broker_poller.py.
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any, cast


class BrokerLifecycleSupport:
    """Coordinate BrokerPoller lifecycle entrypoints without owning polling logic."""

    def __init__(
        self,
        *,
        stop_lock: asyncio.Lock,
        get_running: Callable[[], bool],
        set_running: Callable[[bool], None],
        get_shutdown_event: Callable[[], asyncio.Event],
        set_shutdown_event: Callable[[asyncio.Event], None],
        get_consumer_task: Callable[[], Any | None],
        set_consumer_task: Callable[[Any | None], None],
        get_completion_monitor_task: Callable[[], Any | None],
        set_completion_monitor_task: Callable[[Any | None], None],
        set_event_loop: Callable[[asyncio.AbstractEventLoop | None], None],
        set_fatal_error: Callable[[Exception | None], None],
        get_kafka_config: Callable[[], Any],
        get_consume_topic: Callable[[], str],
        set_runtime_clients: Callable[[Any, Any, Any], None],
        get_producer: Callable[[], Any | None],
        get_consumer: Callable[[], Any | None],
        get_pending_dlq_events: Callable[[], Any],
        get_message_cache: Callable[[], Any],
        set_message_cache_size_bytes: Callable[[int], None],
        get_task_lifecycle_support: Callable[[], Any],
        get_shutdown_policy: Callable[[], str],
        get_shutdown_drain_timeout_seconds: Callable[[], float],
        get_consumer_task_stop_timeout_seconds: Callable[[], float],
        set_defer_consumer_cleanup_for_stop: Callable[[bool], None],
        raise_if_failed: Callable[[], None],
        drain_shutdown_work: Callable[..., Awaitable[bool]],
        drain_commit_coordinator_for_shutdown: Callable[[float], Awaitable[bool]],
        consumer_operation_guard: Any,
        on_assign: Callable[..., None],
        on_revoke: Callable[..., None],
        run_consumer: Callable[[], Awaitable[Any]],
        run_completion_monitor: Callable[[], Awaitable[Any]],
        logger: Any,
    ) -> None:
        """Initialize lifecycle orchestration support."""
        self._stop_lock = stop_lock
        self._get_running = get_running
        self._set_running = set_running
        self._get_shutdown_event = get_shutdown_event
        self._set_shutdown_event = set_shutdown_event
        self._get_consumer_task = get_consumer_task
        self._set_consumer_task = set_consumer_task
        self._get_completion_monitor_task = get_completion_monitor_task
        self._set_completion_monitor_task = set_completion_monitor_task
        self._set_event_loop = set_event_loop
        self._set_fatal_error = set_fatal_error
        self._get_kafka_config = get_kafka_config
        self._get_consume_topic = get_consume_topic
        self._set_runtime_clients = set_runtime_clients
        self._get_producer = get_producer
        self._get_consumer = get_consumer
        self._get_pending_dlq_events = get_pending_dlq_events
        self._get_message_cache = get_message_cache
        self._set_message_cache_size_bytes = set_message_cache_size_bytes
        self._get_task_lifecycle_support = get_task_lifecycle_support
        self._get_shutdown_policy = get_shutdown_policy
        self._get_shutdown_drain_timeout_seconds = get_shutdown_drain_timeout_seconds
        self._get_consumer_task_stop_timeout_seconds = (
            get_consumer_task_stop_timeout_seconds
        )
        self._set_defer_consumer_cleanup_for_stop = set_defer_consumer_cleanup_for_stop
        self._raise_if_failed = raise_if_failed
        self._drain_shutdown_work = drain_shutdown_work
        self._drain_commit_coordinator_for_shutdown = (
            drain_commit_coordinator_for_shutdown
        )
        self._consumer_operation_guard = consumer_operation_guard
        self._on_assign = on_assign
        self._on_revoke = on_revoke
        self._run_consumer = run_consumer
        self._run_completion_monitor = run_completion_monitor
        self._logger = logger

    async def start(self) -> None:
        """Start Kafka runtime clients and background lifecycle tasks."""
        try:
            if self._get_running():
                return
            self._set_event_loop(asyncio.get_running_loop())
            self._set_shutdown_event(asyncio.Event())
            self._set_fatal_error(None)
            kafka_config = self._get_kafka_config()
            producer_conf = cast(
                dict[str, str | int | float | bool],
                kafka_config.get_producer_config(),
            )
            admin_conf = cast(
                dict[str, str | int | float | bool],
                kafka_config.get_admin_config(),
            )
            consumer_conf = cast(
                dict[str, str | int | float | bool | None],
                kafka_config.get_consumer_config(),
            )
            (
                producer,
                admin,
                consumer,
                consumer_task,
                completion_monitor_task,
            ) = self._get_task_lifecycle_support().start_runtime(
                consume_topic=self._get_consume_topic(),
                producer_conf=producer_conf,
                admin_conf=admin_conf,
                consumer_conf=consumer_conf,
                on_assign=self._on_assign,
                on_revoke=self._on_revoke,
                consumer_loop_coro_factory=self._run_consumer,
                completion_monitor_coro_factory=self._run_completion_monitor,
                strict_completion_monitor_enabled=getattr(
                    kafka_config.parallel_consumer,
                    "strict_completion_monitor_enabled",
                    True,
                ),
            )
            self._set_runtime_clients(producer, admin, consumer)
            self._set_consumer_task(consumer_task)
            self._set_completion_monitor_task(completion_monitor_task)
            self._set_running(True)
            self._logger.debug(
                "Kafka consumer subscribed to %s", self._get_consume_topic()
            )
        except Exception as exc:
            self._logger.error("Failed to start BrokerPoller: %s", exc, exc_info=True)
            raise

    async def stop(self, *, cleanup: Callable[[], Awaitable[None]]) -> None:
        """Stop background tasks, optionally drain gracefully, and clean up clients."""
        async with self._stop_lock:
            if not self._get_running() and self._get_consumer_task() is None:
                if self._get_shutdown_event().is_set():
                    self._raise_if_failed()
                return
            shutdown_policy = self._get_shutdown_policy()
            self._logger.debug(
                "Shutdown signal received with policy=%s", shutdown_policy
            )
            self._set_running(False)
            cleanup_after_drain = False
            try:
                consumer_task = self._get_consumer_task()
                if consumer_task is not None:
                    cleanup_after_drain = shutdown_policy == "graceful"
                    self._set_defer_consumer_cleanup_for_stop(cleanup_after_drain)
                    await self._get_task_lifecycle_support().stop_runtime(
                        consumer_task=consumer_task,
                        shutdown_event=self._get_shutdown_event(),
                        timeout_seconds=(
                            self._get_consumer_task_stop_timeout_seconds()
                        ),
                        wait_for=asyncio.wait_for,
                        gather=asyncio.gather,
                    )
                    self._set_consumer_task(None)
                self._raise_if_failed()
                if shutdown_policy == "graceful":
                    await self._drain_shutdown_work(
                        timeout_seconds=self._get_shutdown_drain_timeout_seconds()
                    )
            finally:
                if cleanup_after_drain:
                    self._set_defer_consumer_cleanup_for_stop(False)
                    await cleanup()
            self._logger.debug("BrokerPoller stopped")

    async def cleanup_runtime(self) -> None:
        """Drain commit coordination, close Kafka clients, and clear shutdown caches."""
        kafka_config = self._get_kafka_config()
        commit_coordinator_config = kafka_config.parallel_consumer.commit_coordinator
        coordinator_timeout_ms = getattr(
            commit_coordinator_config,
            "stop_drain_timeout_ms",
            0,
        )
        await self._drain_commit_coordinator_for_shutdown(
            time.monotonic() + max(0.0, float(coordinator_timeout_ms) / 1000.0)
        )
        producer = self._get_producer()
        if producer:
            await asyncio.to_thread(producer.flush, timeout=5)
        consumer = self._get_consumer()
        if consumer:
            await self._consumer_operation_guard.run_off_event_loop(consumer.close)
        self._get_message_cache().clear()
        self._get_pending_dlq_events().clear()
        self._set_message_cache_size_bytes(0)

    async def wait_closed(self) -> None:
        """Wait until broker lifecycle shutdown is complete."""
        if not self._get_running() and self._get_consumer_task() is None:
            if self._get_shutdown_event().is_set():
                self._raise_if_failed()
            return
        await self._get_task_lifecycle_support().wait_closed(
            shutdown_event=self._get_shutdown_event(),
            raise_if_failed=self._raise_if_failed,
        )
