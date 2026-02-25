from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable, Dict, Literal, Optional

from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine
from pyrallel_consumer.execution_plane.process_engine import ProcessExecutionEngine

from .kafka_admin import TopicConfig, reset_topics_and_groups
from .stats import BenchmarkResult, BenchmarkStats

topic = "test_topic"
TEST_NUM_MESSAGES = 50000
DEFAULT_TIMEOUT_SEC = 60


conf: Dict[str, Any] = {
    "bootstrap.servers": "localhost:9092",
    "group.id": "pyrallel_consumer_test_group",
    "auto.offset.reset": "earliest",
    "enable.auto.commit": False,
    "session.timeout.ms": 6000,
}

ExecutionMode = Literal["async", "process"]


def create_topic_if_not_exists(
    admin_conf: Dict[str, Any], topic_name: str, num_partitions: int = 1
) -> None:
    admin_client = AdminClient({"bootstrap.servers": admin_conf["bootstrap.servers"]})
    try:
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


def _process_mode_worker(item: WorkItem) -> None:
    payload_bytes = item.payload or b""
    _decode_payload(payload_bytes)
    time.sleep(0.005)


def build_kafka_config(
    *,
    bootstrap_servers: Optional[str] = None,
    consumer_group: Optional[str] = None,
) -> KafkaConfig:
    effective_conf = dict(conf)
    if bootstrap_servers:
        effective_conf["bootstrap.servers"] = bootstrap_servers
    if consumer_group:
        effective_conf["group.id"] = consumer_group

    kafka_config = KafkaConfig(
        BOOTSTRAP_SERVERS=[effective_conf["bootstrap.servers"]],
        CONSUMER_GROUP=effective_conf["group.id"],
        AUTO_OFFSET_RESET=effective_conf["auto.offset.reset"],
        ENABLE_AUTO_COMMIT=effective_conf["enable.auto.commit"],
        SESSION_TIMEOUT_MS=effective_conf["session.timeout.ms"],
    )

    kafka_config.parallel_consumer.execution.max_in_flight = 2000
    kafka_config.parallel_consumer.execution.async_config.task_timeout_ms = 10000

    return kafka_config


async def run_pyrallel_consumer_test(
    num_messages: Optional[int] = TEST_NUM_MESSAGES,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
    *,
    topic_name: Optional[str] = None,
    bootstrap_servers: Optional[str] = None,
    consumer_group: Optional[str] = None,
    execution_mode: ExecutionMode = "async",
    stats_tracker: Optional[BenchmarkStats] = None,
    num_partitions: int = 1,
    reset_topic: bool = False,
    async_worker_fn: Optional[Callable[[WorkItem], Awaitable[None]]] = None,
    process_worker_fn: Optional[Callable[[WorkItem], None]] = None,
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
    )
    consumption_stats = ConsumptionStats(target=num_messages)
    stop_event = asyncio.Event()
    stats = stats_tracker
    if stats:
        stats.start()

    class BenchmarkMetricsObserver:
        def __init__(
            self,
            benchmark_stats: Optional[BenchmarkStats],
            cons_stats: ConsumptionStats,
            completion_event: asyncio.Event,
        ) -> None:
            self._stats = benchmark_stats
            self._consumption_stats = cons_stats
            self._stop_event = completion_event

        def observe_completion(self, tp, status, duration_seconds: float) -> None:
            self._consumption_stats.record()
            if self._stats:
                self._stats.record(duration_seconds)
            if self._stats and self._stats.completed_target():
                self._stop_event.set()
            elif self._consumption_stats.reached_target():
                self._stop_event.set()

    metrics_observer = BenchmarkMetricsObserver(stats, consumption_stats, stop_event)

    async def async_worker(item: WorkItem) -> None:
        payload_bytes = item.payload or b""
        _decode_payload(payload_bytes)
        await asyncio.sleep(0.005)

    process_worker = process_worker_fn or _process_mode_worker
    async_worker_impl = async_worker_fn or async_worker

    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution
    execution_config.mode = execution_mode

    engine: BaseExecutionEngine
    if execution_mode == "process":
        engine = ProcessExecutionEngine(
            config=execution_config,
            worker_fn=process_worker,
        )
    else:
        engine = AsyncExecutionEngine(
            config=execution_config, worker_fn=async_worker_impl
        )

    work_manager = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=execution_config.max_in_flight,
        metrics_exporter=metrics_observer,
    )  # type: ignore[call-arg]

    broker_poller = BrokerPoller(
        consume_topic=effective_topic,
        kafka_config=kafka_config,
        execution_engine=engine,
        work_manager=work_manager,
    )

    print("Starting PyrallelConsumer test for topic '%s'." % effective_topic)
    if num_messages is not None:
        print("Target messages to process: %d" % num_messages)
    else:
        print("Consuming indefinitely. Use Ctrl+C to stop.")
    print("Timeout: %ds" % timeout_sec)

    await broker_poller.start()

    async def _print_diagnostics() -> None:
        while not stop_event.is_set():
            await asyncio.sleep(5)
            if stop_event.is_set():
                break
            metrics = broker_poller.get_metrics()
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

    timed_out = False
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
    finally:
        stop_event.set()
        diagnostics_task.cancel()
        try:
            await diagnostics_task
        except asyncio.CancelledError:
            pass

        print("Stopping PyrallelConsumer...")
        await broker_poller.stop()
        await engine.shutdown()
        if stats:
            stats.stop()

        processed, runtime, tps = consumption_stats.summary()
        print("\n--- PyrallelConsumer Test Summary ---")
        print("Result: %s" % ("TIMEOUT" if timed_out else "COMPLETED"))
        print("Total messages processed: %d" % processed)
        print("Total runtime: %.2f seconds" % runtime)
        print("Final TPS: %.2f" % tps)

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
