import asyncio
import json
import time
from typing import Any, Dict, Optional

from confluent_kafka.admin import AdminClient
from confluent_kafka.cimpl import NewTopic

from pyrallel_consumer.config import ExecutionConfig, KafkaConfig
from pyrallel_consumer.control_plane.broker_poller import BrokerPoller
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.async_engine import AsyncExecutionEngine

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


def create_topic_if_not_exists(admin_conf: Dict[str, Any], topic_name: str) -> None:
    admin_client = AdminClient({"bootstrap.servers": admin_conf["bootstrap.servers"]})
    try:
        new_topics = [NewTopic(topic_name, num_partitions=1, replication_factor=1)]
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


def build_kafka_config() -> KafkaConfig:
    kafka_config = KafkaConfig(
        BOOTSTRAP_SERVERS=[conf["bootstrap.servers"]],
        CONSUMER_GROUP=conf["group.id"],
        AUTO_OFFSET_RESET=conf["auto.offset.reset"],
        ENABLE_AUTO_COMMIT=conf["enable.auto.commit"],
        SESSION_TIMEOUT_MS=conf["session.timeout.ms"],
    )

    # Set execution specific configurations for AsyncExecutionEngine
    kafka_config.parallel_consumer.execution.mode = "async"
    kafka_config.parallel_consumer.execution.max_in_flight = 2000
    kafka_config.parallel_consumer.execution.async_config.task_timeout_ms = 10000

    return kafka_config


async def run_pyrallel_consumer_test(
    num_messages: Optional[int] = TEST_NUM_MESSAGES,
    timeout_sec: int = DEFAULT_TIMEOUT_SEC,
) -> None:
    create_topic_if_not_exists(conf, topic)

    kafka_config = build_kafka_config()
    stats = ConsumptionStats(target=num_messages)
    stop_event = asyncio.Event()

    # This is the actual worker function executed by the AsyncExecutionEngine
    async def actual_benchmark_worker(item: WorkItem) -> None:
        payload_bytes = item.payload or b""
        if payload_bytes:
            try:
                json.loads(payload_bytes.decode("utf-8"))
            except json.JSONDecodeError:
                pass
        # Simulate some async work
        await asyncio.sleep(0.0001)  # Small sleep to simulate work
        stats.record()
        if stats.reached_target():
            stop_event.set()

    # Build ExecutionConfig from KafkaConfig
    execution_config: ExecutionConfig = kafka_config.parallel_consumer.execution

    # Initialize AsyncExecutionEngine with the actual worker
    async_execution_engine = AsyncExecutionEngine(
        config=execution_config, worker_fn=actual_benchmark_worker
    )

    # Initialize WorkManager with the ExecutionEngine
    work_manager = WorkManager(
        execution_engine=async_execution_engine,
        max_in_flight_messages=execution_config.max_in_flight,
    )

    # Initialize BrokerPoller
    broker_poller = BrokerPoller(
        consume_topic=topic,
        kafka_config=kafka_config,
        execution_engine=async_execution_engine,
        work_manager=work_manager,
    )

    print("Starting PyrallelConsumer test for topic '%s'." % topic)
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
                % (stats.processed, in_flight, paused, partition_str),
                flush=True,
            )

    diagnostics_task = asyncio.create_task(_print_diagnostics())

    timed_out = False
    try:
        if num_messages is None:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout_sec)
        else:
            await asyncio.wait_for(stop_event.wait(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        timed_out = True
        print(
            "\n*** Test timed out after %ds (processed %d / %s messages) ***"
            % (timeout_sec, stats.processed, num_messages if num_messages else "∞"),
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

        processed, runtime, tps = stats.summary()
        print("\n--- PyrallelConsumer Test Summary ---")
        print("Result: %s" % ("TIMEOUT" if timed_out else "COMPLETED"))
        print("Total messages processed: %d" % processed)
        print("Total runtime: %.2f seconds" % runtime)
        print("Final TPS: %.2f" % tps)


if __name__ == "__main__":
    asyncio.run(
        run_pyrallel_consumer_test(
            num_messages=TEST_NUM_MESSAGES,
            timeout_sec=DEFAULT_TIMEOUT_SEC,
        )
    )
