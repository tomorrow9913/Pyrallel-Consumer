from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig, MetricsConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    CompletionStatus,
    ExecutionMode,
    OrderingMode,
    SystemMetrics,
    WorkItem,
)
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine
from pyrallel_consumer.metrics_exporter import PrometheusMetricsExporter

from .kafka_admin import TopicConfig, reset_topics_and_groups
from .stats import BenchmarkResult, BenchmarkStats

topic = "test_topic"
TEST_NUM_MESSAGES = 50000
DEFAULT_TIMEOUT_SEC = 60
ProcessFlushPolicy = Literal["size_or_timer", "demand", "demand_min_residence"]
ProcessTransportMode = Literal["shared_queue", "worker_pipes"]


conf: Dict[str, Any] = {
    "bootstrap.servers": "localhost:9092",
    "group.id": "pyrallel_consumer_test_group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "session.timeout.ms": 6000,
}

_PROMETHEUS_EXPORTERS: dict[int, PrometheusMetricsExporter] = {}


def _get_or_create_prometheus_exporter(port: int) -> PrometheusMetricsExporter:
    exporter = _PROMETHEUS_EXPORTERS.get(port)
    if exporter is None:
        exporter = PrometheusMetricsExporter(MetricsConfig(enabled=True, port=port))
        _PROMETHEUS_EXPORTERS[port] = exporter
    return exporter


def create_topic_if_not_exists(
    admin_conf: Dict[str, Any], topic_name: str, num_partitions: int = 1
) -> None:
    admin_client = AdminClient({"bootstrap.servers": admin_conf["bootstrap.servers"]})
    try:
        metadata = admin_client.list_topics(timeout=5)
        if topic_name in metadata.topics:
            return

        new_topics = [
            NewTopic(topic_name, num_partitions=num_partitions, replication_factor=1)
        ]
        futures = admin_client.create_topics(new_topics)

        for tp, future in futures.items():
            try:
                future.result()
                print("Topic '%s' created successfully." % tp)
            except Exception as exc:  # noqa: BLE001
                if "topic already exists" in str(exc).lower():
                    print("Topic '%s' already exists." % tp)
                else:
                    print("Failed to create topic %s: %s" % (tp, exc))
    except Exception as exc:  # noqa: BLE001
        print("An error occurred during topic check/creation: %s" % exc)


class ConsumptionStats:
    def __init__(self, target: Optional[int]) -> None:
        self._target = target
        self._start_time = time.time()
        self._processed = 0
        self._last_report_time = time.time()

    def record(self) -> None:
        self._processed += 1
        now = time.time()
        if self._processed == 1:
            print(
                "First message processed at %.2fs" % (now - self._start_time),
                flush=True,
            )
        if self._processed % 1000 == 0:
            elapsed = now - self._start_time
            tps = self._processed / elapsed if elapsed > 0 else 0
            print(
                "Processed %d messages. Current TPS: %.2f" % (self._processed, tps),
                flush=True,
            )
            self._last_report_time = now

    def reached_target(self) -> bool:
        return self._target is not None and self._processed >= self._target

    @property
    def processed(self) -> int:
        return self._processed

    def summary(self) -> tuple[int, float, float]:
        runtime = time.time() - self._start_time
        tps = self._processed / runtime if runtime > 0 else 0
        return self._processed, runtime, tps


def _decode_payload(payload_bytes: bytes) -> None:
    if not payload_bytes:
        return
    try:
        json.loads(payload_bytes.decode("utf-8"))
    except json.JSONDecodeError:
        pass


class OrderingValidator:
    def __init__(self, *, ordering_mode: str, topic_name: str) -> None:
        self._ordering_mode = OrderingMode(ordering_mode)
        self._topic_name = topic_name
        self._checks = 0
        self._last_sequence_by_key: dict[str, int] = {}
        self._last_offset_by_partition: dict[int, int] = {}

    def observe(self, item: WorkItem) -> None:
        if self._ordering_mode == OrderingMode.UNORDERED:
            return

        if self._ordering_mode == OrderingMode.KEY_HASH:
            payload = self._decode_ordering_payload(item.payload)
            key = str(payload["key"])
            sequence = int(payload["sequence"])
            expected_sequence = self._last_sequence_by_key.get(key, -1) + 1
            if sequence != expected_sequence:
                raise RuntimeError(
                    "Ordering validation failed for key %s on %s: expected sequence %d but got %d"
                    % (key, self._topic_name, expected_sequence, sequence)
                )
            self._last_sequence_by_key[key] = sequence
        elif self._ordering_mode == OrderingMode.PARTITION:
            partition = item.tp.partition
            expected_offset = (
                self._last_offset_by_partition.get(partition, item.offset - 1) + 1
            )
            if item.offset != expected_offset:
                raise RuntimeError(
                    "Ordering validation failed for partition %d on %s: expected offset %d but got %d"
                    % (partition, self._topic_name, expected_offset, item.offset)
                )
            self._last_offset_by_partition[partition] = item.offset

        self._checks += 1

    def summary(self) -> str:
        if self._ordering_mode == OrderingMode.UNORDERED:
            return "Ordering validation SKIP: unordered"
        if self._ordering_mode == OrderingMode.KEY_HASH:
            return "Ordering validation PASS: key_hash keys=%d checks=%d" % (
                len(self._last_sequence_by_key),
                self._checks,
            )
        return "Ordering validation PASS: partition partitions=%d checks=%d" % (
            len(self._last_offset_by_partition),
            self._checks,
        )

    def _decode_ordering_payload(self, payload: Any) -> dict[str, Any]:
        if not isinstance(payload, (bytes, bytearray)):
            raise RuntimeError(
                "Ordering validation failed for %s: payload must be bytes"
                % self._topic_name
            )
        try:
            decoded = json.loads(bytes(payload).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Ordering validation failed for %s: payload was not valid JSON"
                % self._topic_name
            ) from exc
        if "key" not in decoded or "sequence" not in decoded:
            raise RuntimeError(
                "Ordering validation failed for %s: payload missing key/sequence"
                % self._topic_name
            )
        return decoded


def _process_mode_worker(item: WorkItem) -> None:
    payload_bytes = item.payload or b""
    _decode_payload(payload_bytes)
    time.sleep(0.005)


async def _wait_for_partition_assignment(
    broker_poller: BrokerPoller,
    *,
    topic_name: str,
    timeout_sec: float,
    poll_interval_sec: float = 0.1,
) -> None:
    start = time.monotonic()
    while time.monotonic() - start < timeout_sec:
        metrics = broker_poller.get_metrics()
        if metrics.partitions:
            assigned = ", ".join(
                "%s-%d" % (partition.tp.topic, partition.tp.partition)
                for partition in metrics.partitions
            )
            print(
                "Assigned partitions after %.2fs: %s"
                % (time.monotonic() - start, assigned),
                flush=True,
            )
            return
        await asyncio.sleep(poll_interval_sec)

    raise RuntimeError(
        "No partitions assigned within %.1fs for topic '%s'" % (timeout_sec, topic_name)
    )


def build_kafka_config(
    *,
    bootstrap_servers: Optional[str] = None,
    consumer_group: Optional[str] = None,
    strict_completion_monitor_enabled: bool = True,
    process_count: Optional[int] = None,
    process_batch_size: Optional[int] = None,
    process_max_batch_wait_ms: Optional[int] = None,
    process_flush_policy: Optional[ProcessFlushPolicy] = None,
    process_demand_flush_min_residence_ms: Optional[int] = None,
    process_transport_mode: Optional[ProcessTransportMode] = None,
    metrics_port: Optional[int] = None,
    adaptive_concurrency_enabled: bool = False,
) -> KafkaConfig:
    effective_conf = dict(conf)
    if bootstrap_servers:
        effective_conf["bootstrap.servers"] = bootstrap_servers
    if consumer_group:
        effective_conf["group.id"] = consumer_group

    kafka_config = KafkaConfig(
        bootstrap_servers=[effective_conf["bootstrap.servers"]],
        consumer_group=effective_conf["group.id"],
        auto_offset_reset=effective_conf["auto.offset.reset"],
        enable_auto_commit=effective_conf["enable.auto.commit"],
        session_timeout_ms=effective_conf["session.timeout.ms"],
    )

    kafka_config.parallel_consumer.execution.max_in_flight = 2000
    kafka_config.parallel_consumer.execution.async_config.task_timeout_ms = 10000
    kafka_config.parallel_consumer.strict_completion_monitor_enabled = (
        strict_completion_monitor_enabled
    )
    kafka_config.parallel_consumer.adaptive_concurrency.enabled = (
        adaptive_concurrency_enabled
    )
    if process_count is not None:
        if process_count <= 0:
            raise ValueError("process_count must be greater than 0")
        kafka_config.parallel_consumer.execution.process_config.process_count = (
            process_count
        )
    if process_batch_size is not None:
        kafka_config.parallel_consumer.execution.process_config.batch_size = (
            process_batch_size
        )
    if process_max_batch_wait_ms is not None:
        kafka_config.parallel_consumer.execution.process_config.max_batch_wait_ms = (
            process_max_batch_wait_ms
        )
    if process_flush_policy is not None:
        kafka_config.parallel_consumer.execution.process_config.flush_policy = (
            process_flush_policy
        )
    if process_demand_flush_min_residence_ms is not None:
        (
            kafka_config.parallel_consumer.execution.process_config.demand_flush_min_residence_ms
        ) = process_demand_flush_min_residence_ms
    if process_transport_mode is not None:
        process_config = kafka_config.parallel_consumer.execution.process_config
        process_config.transport_mode = process_transport_mode
        if process_transport_mode == "worker_pipes":
            if process_batch_size is None:
                process_config.batch_size = 1
            if process_max_batch_wait_ms is None:
                process_config.max_batch_wait_ms = 0
            if process_flush_policy is None:
                process_config.flush_policy = "size_or_timer"
            if process_demand_flush_min_residence_ms is None:
                process_config.demand_flush_min_residence_ms = 0
    if metrics_port is not None:
        kafka_config.metrics = MetricsConfig(enabled=True, port=metrics_port)

    return kafka_config


async def run_pyrallel_consumer_test(
    num_messages: Optional[int] = TEST_NUM_MESSAGES,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    *,
    topic_name: Optional[str] = None,
    bootstrap_servers: Optional[str] = None,
    consumer_group: Optional[str] = None,
    execution_mode: str = "async",
    stats_tracker: Optional[BenchmarkStats] = None,
    num_partitions: int = 1,
    reset_topic: bool = False,
    async_worker_fn: Optional[Callable[[WorkItem], Awaitable[None]]] = None,
    process_worker_fn: Optional[Callable[[WorkItem], None]] = None,
    ordering_mode: str = OrderingMode.KEY_HASH.value,
    ensure_topic_exists: bool = True,
    strict_completion_monitor_enabled: bool = True,
    process_count: Optional[int] = None,
    process_batch_size: Optional[int] = None,
    process_max_batch_wait_ms: Optional[int] = None,
    process_flush_policy: Optional[ProcessFlushPolicy] = None,
    process_demand_flush_min_residence_ms: Optional[int] = None,
    process_transport_mode: Optional[ProcessTransportMode] = None,
    metrics_port: Optional[int] = None,
    adaptive_concurrency_enabled: bool = False,
) -> tuple[bool, ConsumptionStats, Optional[BenchmarkResult]]:
    effective_topic = topic_name or topic
    effective_bootstrap = bootstrap_servers or conf["bootstrap.servers"]
    effective_group = consumer_group or conf["group.id"]
    if reset_topic:
        reset_topics_and_groups(
            bootstrap_servers=effective_bootstrap,
            topics={effective_topic: TopicConfig(num_partitions=num_partitions)},
            consumer_groups=[effective_group],
        )
        ensure_topic_exists = False
    if ensure_topic_exists:
        override_conf = dict(conf)
        override_conf["bootstrap.servers"] = effective_bootstrap
        create_topic_if_not_exists(
            override_conf,
            effective_topic,
            num_partitions=num_partitions,
        )

    kafka_config = build_kafka_config(
        bootstrap_servers=bootstrap_servers,
        consumer_group=consumer_group,
        strict_completion_monitor_enabled=strict_completion_monitor_enabled,
        process_count=process_count,
        process_batch_size=process_batch_size,
        process_max_batch_wait_ms=process_max_batch_wait_ms,
        process_flush_policy=process_flush_policy,
        process_demand_flush_min_residence_ms=(process_demand_flush_min_residence_ms),
        process_transport_mode=process_transport_mode,
        metrics_port=metrics_port,
        adaptive_concurrency_enabled=adaptive_concurrency_enabled,
    )
    mode_value = ExecutionMode(execution_mode)
    ordering_mode_value = OrderingMode(ordering_mode)
    consumption_stats = ConsumptionStats(target=num_messages)
    stop_event = asyncio.Event()
    stats = stats_tracker
    if stats:
        stats.start()
    prometheus_exporter = (
        _get_or_create_prometheus_exporter(metrics_port)
        if metrics_port is not None
        else None
    )

    class BenchmarkMetricsObserver:
        def __init__(
            self,
            benchmark_stats: Optional[BenchmarkStats],
            cons_stats: ConsumptionStats,
            completion_event: asyncio.Event,
            completion_ordering_validator: Optional[OrderingValidator] = None,
            prometheus_metrics_exporter: Optional[PrometheusMetricsExporter] = None,
        ) -> None:
            self._stats = benchmark_stats
            self._consumption_stats = cons_stats
            self._stop_event = completion_event
            self._failure_error: Optional[str] = None
            self._completion_ordering_validator = completion_ordering_validator
            self._prometheus_metrics_exporter = prometheus_metrics_exporter

        @property
        def failure_error(self) -> Optional[str]:
            return self._failure_error

        def report_worker_failure(self, error: str) -> None:
            if self._failure_error is None:
                self._failure_error = error
            self._stop_event.set()

        def observe_completion(self, tp, status, duration_seconds: float) -> None:
            if status == CompletionStatus.FAILURE:
                self.report_worker_failure(
                    "Benchmark worker failure on %s[%d]: completion failed"
                    % (tp.topic, tp.partition)
                )
                return
            if self._prometheus_metrics_exporter is not None:
                self._prometheus_metrics_exporter.observe_completion(
                    tp, status, duration_seconds
                )
            self._consumption_stats.record()
            if self._stats:
                self._stats.record(duration_seconds)
            if self._stats and self._stats.completed_target():
                self._stop_event.set()
            elif self._consumption_stats.reached_target():
                self._stop_event.set()

        def observe_work_completion(
            self,
            event: Any,
            work_item: WorkItem,
            duration_seconds: float,
        ) -> None:
            if event.status == CompletionStatus.SUCCESS:
                if self._completion_ordering_validator is not None:
                    try:
                        self._completion_ordering_validator.observe(work_item)
                    except Exception as exc:
                        self.report_worker_failure(str(exc))
                        return
            self.observe_completion(work_item.tp, event.status, duration_seconds)

    ordering_validator: Optional[OrderingValidator] = None
    if (
        mode_value == ExecutionMode.ASYNC
        and ordering_mode_value != OrderingMode.UNORDERED
    ):
        ordering_validator = OrderingValidator(
            ordering_mode=ordering_mode_value.value,
            topic_name=effective_topic,
        )
    process_completion_validator: Optional[OrderingValidator] = None
    if (
        mode_value == ExecutionMode.PROCESS
        and ordering_mode_value != OrderingMode.UNORDERED
    ):
        process_completion_validator = OrderingValidator(
            ordering_mode=ordering_mode_value.value,
            topic_name=effective_topic,
        )
    metrics_observer = BenchmarkMetricsObserver(
        stats,
        consumption_stats,
        stop_event,
        completion_ordering_validator=process_completion_validator,
        prometheus_metrics_exporter=prometheus_exporter,
    )

    async def async_worker(item: WorkItem) -> None:
        payload_bytes = item.payload or b""
        _decode_payload(payload_bytes)
        await asyncio.sleep(0.005)

    process_worker = process_worker_fn or _process_mode_worker
    async_worker_impl = async_worker_fn or async_worker

    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.mode = mode_value

    async def validated_async_worker(item: WorkItem) -> None:
        try:
            if ordering_validator is not None:
                ordering_validator.observe(item)
            await async_worker_impl(item)
        except Exception as exc:
            metrics_observer.report_worker_failure(str(exc))
            raise

    engine: BaseExecutionEngine
    if mode_value == ExecutionMode.PROCESS:
        engine = ProcessExecutionEngine(
            config=execution_config,
            worker_fn=process_worker,
        )
    else:
        engine = AsyncExecutionEngine(
            config=execution_config, worker_fn=validated_async_worker
        )

    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
        ordering_mode=ordering_mode_value,
        metrics_exporter=metrics_observer,
    )  # type: ignore[call-arg]

    broker_poller = BrokerPoller(
        consume_topic=effective_topic,
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )
    broker_poller.ORDERING_MODE = ordering_mode_value

    print("Starting PyrallelConsumer test for topic '%s'." % effective_topic)
    if num_messages is not None:
        print("Target messages to process: %d" % num_messages)
    else:
        print("Consuming indefinitely. Use Ctrl+C to stop.")
    print("Timeout: %ds" % timeout_sec)

    diagnostics_task: Optional[asyncio.Task[None]] = None
    metrics_task: Optional[asyncio.Task[None]] = None
    timed_out = False
    run_completed = False
    metrics_start = time.perf_counter()

    def _record_release_gate_metrics_from_snapshot(metrics: SystemMetrics) -> None:
        if stats is None:
            return
        stats.record_release_gate_observation(
            elapsed_sec=time.perf_counter() - metrics_start,
            consumer_parallel_lag=sum(pm.true_lag for pm in metrics.partitions),
            consumer_gap_count=sum(pm.gap_count for pm in metrics.partitions),
        )

    try:
        await broker_poller.start()
        if prometheus_exporter is not None:

            async def _publish_metrics() -> None:
                while not stop_event.is_set():
                    prometheus_exporter.update_from_system_metrics(
                        broker_poller.get_metrics()
                    )
                    await asyncio.sleep(0.5)

            metrics_task = asyncio.create_task(_publish_metrics())
        assignment_timeout_sec = min(max(timeout_sec / 4, 1.0), 10.0)
        await _wait_for_partition_assignment(
            broker_poller,
            topic_name=effective_topic,
            timeout_sec=assignment_timeout_sec,
        )

        async def _print_diagnostics() -> None:
            while not stop_event.is_set():
                await asyncio.sleep(5)
                if stop_event.is_set():
                    break
                metrics = broker_poller.get_metrics()
                if stats is not None:
                    stats.record_release_gate_observation(
                        elapsed_sec=time.perf_counter() - metrics_start,
                        consumer_parallel_lag=sum(
                            pm.true_lag for pm in metrics.partitions
                        ),
                        consumer_gap_count=sum(
                            pm.gap_count for pm in metrics.partitions
                        ),
                    )
                in_flight = metrics.total_in_flight
                paused = metrics.is_paused
                partitions_info = []
                for pm in metrics.partitions:
                    partitions_info.append(
                        "%s-%d lag=%d gaps=%d queued=%d"
                        % (
                            pm.tp.topic,
                            pm.tp.partition,
                            pm.true_lag,
                            pm.gap_count,
                            pm.queued_count,
                        )
                    )
                partition_str = (
                    ", ".join(partitions_info)
                    if partitions_info
                    else "no partitions assigned"
                )
                print(
                    "[diag] processed=%d in_flight=%d paused=%s | %s"
                    % (consumption_stats.processed, in_flight, paused, partition_str),
                    flush=True,
                )

        diagnostics_task = asyncio.create_task(_print_diagnostics())

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            timed_out = True
            print(
                "\n*** Test timed out after %ds (processed %d / %s messages) ***"
                % (
                    timeout_sec,
                    consumption_stats.processed,
                    num_messages if num_messages else "∞",
                ),
                flush=True,
            )
        except KeyboardInterrupt:
            print("Consumer interrupted by user.")
        run_completed = True
    finally:
        stop_event.set()
        if diagnostics_task is not None:
            diagnostics_task.cancel()
            try:
                await diagnostics_task
            except asyncio.CancelledError:
                pass
        if metrics_task is not None:
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass

        print("Stopping PyrallelConsumer...")
        await broker_poller.stop()
        final_metrics = broker_poller.get_metrics()
        _record_release_gate_metrics_from_snapshot(final_metrics)
        if prometheus_exporter is not None:
            prometheus_exporter.update_from_system_metrics(final_metrics)
        await engine.shutdown()
        if stats:
            stats.stop()

        if metrics_observer.failure_error is not None:
            raise RuntimeError(metrics_observer.failure_error)

        if run_completed:
            processed, runtime, tps = consumption_stats.summary()
            print("\n--- PyrallelConsumer Test Summary ---")
            print("Result: %s" % ("TIMEOUT" if timed_out else "COMPLETED"))
            print("Total messages processed: %d" % processed)
            print("Total runtime: %.2f seconds" % runtime)
            print("Final TPS: %.2f" % tps)
            if ordering_validator is not None:
                print(ordering_validator.summary())
            elif process_completion_validator is not None:
                print(process_completion_validator.summary())

    summary = stats.summary() if stats else None
    return timed_out, consumption_stats, summary


if __name__ == "__main__":
    asyncio.run(
        run_pyrallel_consumer_test(
            num_messages=TEST_NUM_MESSAGES,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
            reset_topic=True,
        )
    )
