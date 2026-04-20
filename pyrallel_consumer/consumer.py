import asyncio
import logging
from dataclasses import replace
from typing import Any, Awaitable, Callable, Optional, Union

from pyrallel_consumer.config import KafkaConfig, MetricsConfig, ParallelConsumerConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import SystemMetrics, WorkItem
from pyrallel_consumer.execution_plane.engine_factory import create_execution_engine
from pyrallel_consumer.metrics_exporter import PrometheusMetricsExporter
from pyrallel_consumer.resource_signals import (
    NullResourceSignalProvider,
    ResourceSignalProvider,
)

_PROMETHEUS_EXPORTERS: dict[int, PrometheusMetricsExporter] = {}
_PROMETHEUS_EXPORTER_REFCOUNTS: dict[int, int] = {}
_METRICS_POLL_INTERVAL_SECONDS = 0.5


def _acquire_prometheus_exporter(
    metrics_config: MetricsConfig,
) -> Optional[PrometheusMetricsExporter]:
    if not metrics_config.enabled:
        return None

    exporter = _PROMETHEUS_EXPORTERS.get(metrics_config.port)
    if exporter is None:
        exporter = PrometheusMetricsExporter(metrics_config)
        _PROMETHEUS_EXPORTERS[metrics_config.port] = exporter
    _PROMETHEUS_EXPORTER_REFCOUNTS[metrics_config.port] = (
        _PROMETHEUS_EXPORTER_REFCOUNTS.get(metrics_config.port, 0) + 1
    )
    return exporter


def _release_prometheus_exporter(
    metrics_config: MetricsConfig,
    exporter: Optional[PrometheusMetricsExporter],
) -> None:
    if exporter is None or not metrics_config.enabled:
        return

    port = metrics_config.port
    refcount = _PROMETHEUS_EXPORTER_REFCOUNTS.get(port, 0)
    if refcount <= 1:
        _PROMETHEUS_EXPORTER_REFCOUNTS.pop(port, None)
        cached_exporter = _PROMETHEUS_EXPORTERS.pop(port, None)
        if cached_exporter is not None:
            cached_exporter.close()
        return

    _PROMETHEUS_EXPORTER_REFCOUNTS[port] = refcount - 1


class PyrallelConsumer:
    """
    High-level facade for Pyrallel Consumer.
    Simplifies the initialization and management of the parallel consumption components.

    Attributes:
        config (KafkaConfig): Configuration for Kafka and Execution Engine.
        worker (Callable): User-defined worker function.
    """

    def __init__(
        self,
        config: KafkaConfig,
        worker: Union[Callable[[WorkItem], Awaitable[Any]], Callable[[WorkItem], Any]],
        topic: str,
        resource_signal_provider: Optional[ResourceSignalProvider] = None,
    ):
        """
        Initialize the Pyrallel Consumer.

        Args:
            config (KafkaConfig): Configuration object.
            worker (Callable): The worker function to execute for each message.
                               For 'async' mode, this must be an async function.
                               For 'process' mode, this must be a picklable function.
            topic (str): The Kafka topic to subscribe to.
        """
        self._logger = logging.getLogger(__name__)
        self.config = config
        self._topic = topic
        self._resource_signal_provider = (
            resource_signal_provider or NullResourceSignalProvider()
        )

        metrics_config = getattr(self.config, "metrics", None)
        if metrics_config is None:
            metrics_config = MetricsConfig()
            setattr(self.config, "metrics", metrics_config)
        self._metrics_config = metrics_config
        self._metrics_exporter: Optional[PrometheusMetricsExporter] = None
        self._metrics_task: Optional[asyncio.Task[None]] = None

        parallel_config = getattr(config, "parallel_consumer", None)
        if parallel_config is None:
            parallel_config = ParallelConsumerConfig()
            setattr(self.config, "parallel_consumer", parallel_config)

        execution_config = parallel_config.execution

        # 1. Create Execution Engine
        self._execution_engine = create_execution_engine(execution_config, worker)

        # 2. Create Work Manager
        ordering_mode = parallel_config.ordering_mode
        poison_message_config = getattr(parallel_config, "poison_message", None)
        poison_message_circuit = None
        if poison_message_config is not None:
            poison_message_circuit = PoisonMessageCircuitBreaker(
                enabled=bool(getattr(poison_message_config, "enabled", False)),
                failure_threshold=int(
                    getattr(poison_message_config, "failure_threshold", 3)
                ),
                cooldown_ms=int(getattr(poison_message_config, "cooldown_ms", 0)),
                forced_failure_attempt=execution_config.max_retries,
            )
        self._work_manager = WorkManager(
            execution_engine=self._execution_engine,
            max_in_flight_messages=execution_config.max_in_flight_messages,
            ordering_mode=ordering_mode,
            max_revoke_grace_ms=execution_config.max_revoke_grace_ms,
            poison_message_circuit=poison_message_circuit,
        )

        # 3. Create Broker Poller (The main loop)
        self._poller = BrokerPoller(
            consume_topic=topic,
            kafka_config=config,
            execution_engine=self._execution_engine,
            work_manager=self._work_manager,
        )

    def _publish_metrics_snapshot(self) -> None:
        if self._metrics_exporter is None:
            return
        metrics = self._poller.get_metrics()
        resource_signal = self._resource_signal_provider.snapshot()
        try:
            metrics = replace(metrics, resource_signal=resource_signal)
        except TypeError:
            setattr(metrics, "resource_signal", resource_signal)
        self._metrics_exporter.update_from_system_metrics(metrics)

    async def start(self) -> None:
        """
        Start the consumer.
        This method starts the BrokerPoller loop and the Execution Engine (if needed).
        """
        try:
            if self._metrics_exporter is None:
                self._metrics_exporter = _acquire_prometheus_exporter(
                    self._metrics_config
                )
                self._work_manager.set_metrics_exporter(self._metrics_exporter)
            await self._poller.start()
            if self._metrics_exporter is not None and self._metrics_task is None:
                self._publish_metrics_snapshot()
                self._metrics_task = asyncio.create_task(self._publish_metrics_loop())
        except Exception:
            await self._cleanup_failed_start()
            raise

    async def _publish_metrics_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_METRICS_POLL_INTERVAL_SECONDS)
                self._publish_metrics_snapshot()
        except asyncio.CancelledError:
            raise
        except Exception:
            self._logger.exception("Metrics publisher task failed")

    async def _stop_metrics_loop(self) -> None:
        if self._metrics_task is None:
            return
        metrics_task = self._metrics_task
        self._metrics_task = None
        metrics_task.cancel()
        try:
            await metrics_task
        except asyncio.CancelledError:
            pass

    def _release_metrics_exporter(self) -> None:
        self._work_manager.set_metrics_exporter(None)
        _release_prometheus_exporter(self._metrics_config, self._metrics_exporter)
        self._metrics_exporter = None

    async def _cleanup_failed_start(self) -> None:
        await self._stop_metrics_loop()
        try:
            await self._poller.stop()
        except Exception:
            pass
        await self._execution_engine.shutdown()
        self._release_metrics_exporter()

    async def stop(self) -> None:
        """
        Stop the consumer gracefully.
        """
        poller_error: Exception | None = None
        await self._stop_metrics_loop()
        try:
            await self._poller.stop()
        except Exception as exc:  # pragma: no cover - guarded by facade tests
            poller_error = exc
        self._publish_metrics_snapshot()
        await self._execution_engine.shutdown()
        self._release_metrics_exporter()
        if poller_error is not None:
            raise poller_error

    async def wait_closed(self) -> None:
        """Wait for the broker poller to finish and surface fatal loop failures."""
        await self._poller.wait_closed()

    def get_metrics(self) -> SystemMetrics:
        """
        Get current system metrics.
        """
        return self._poller.get_metrics()
