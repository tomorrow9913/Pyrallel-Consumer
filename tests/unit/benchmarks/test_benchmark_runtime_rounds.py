# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_benchmark_runtime_rounds.py
# Role: Verifies producer, baseline, Pyrallel round, assignment wait, and round cleanup behavior.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._benchmark_runtime_support import (
    Any,
    BenchmarkResult,
    BenchmarkStats,
    ExecutionMode,
    SimpleNamespace,
    asyncio,
    baseline_consumer,
    cast,
    producer,
    pyrallel_consumer_test,
    pytest,
    run_parallel_benchmark,
)


def test_produce_messages_skips_topic_creation_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for produce messages skips topic creation when disabled.
    producer_instance = SimpleNamespace(
        produce=lambda *args, **kwargs: None,
        poll=lambda _timeout: None,
        flush=lambda timeout=60: None,
    )
    create_calls: list[tuple[str, int]] = []

    monkeypatch.setattr(producer, "Producer", lambda _conf: producer_instance)
    monkeypatch.setattr(
        producer,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions: create_calls.append(
            (topic_name, num_partitions)
        ),
    )

    # When: The benchmark runtime round execution path is exercised for produce messages skips topic creation when disabled.
    producer.produce_messages(
        num_messages=1,
        num_keys=1,
        num_partitions=2,
        topic_name="demo-topic",
        ensure_topic_exists=False,
    )

    # Then: The expected produce messages skips topic creation when disabled behavior is asserted.
    assert create_calls == []


def test_run_baseline_round_preserves_workload_specific_run_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run baseline round preserves workload specific run name.
    monkeypatch.setattr(
        run_parallel_benchmark, "produce_messages", lambda **kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "consume_messages",
        lambda **kwargs: BenchmarkResult(
            run_name="sleep-baseline",
            run_type="baseline",
            workload="sleep",
            topic="demo-topic",
            ordering="key_hash",
            process_transport_mode=None,
            messages_processed=10,
            total_time_sec=1.0,
            throughput_tps=10.0,
            avg_processing_ms=1.0,
            p99_processing_ms=1.0,
        ),
    )

    # When: The benchmark runtime round execution path is exercised for run baseline round preserves workload specific run name.
    result = run_parallel_benchmark._run_baseline_round(
        run_name="sleep-baseline",
        topic_name="demo-topic",
        num_messages=10,
        bootstrap_servers="localhost:9092",
        num_partitions=1,
        num_keys=1,
        group_id="demo-group",
        worker_fn=lambda _payload: None,
        workload="sleep",
        ordering="key_hash",
    )

    # Then: The expected run baseline round preserves workload specific run name behavior is asserted.
    assert result.run_name == "sleep-baseline"


@pytest.mark.asyncio
async def test_run_pyrparallel_round_omits_process_transport_helper_argument(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrparallel round omits process transport helper argument.
    captured: dict[str, Any] = {}

    monkeypatch.setattr(
        run_parallel_benchmark, "produce_messages", lambda **kwargs: None
    )

    async def _fake_run_pyrallel_consumer_test(**kwargs):
        captured.update(kwargs)
        return (False, None, benchmark_result)

    monkeypatch.setattr(
        run_parallel_benchmark,
        "run_pyrallel_consumer_test",
        _fake_run_pyrallel_consumer_test,
    )

    # When: The benchmark runtime round execution path is exercised for run pyrparallel round omits process transport helper argument.
    result = await run_parallel_benchmark._run_pyrparallel_round(
        topic_name="demo-topic",
        run_name="async-round",
        mode=ExecutionMode.ASYNC,
        num_messages=10,
        bootstrap_servers="localhost:9092",
        num_partitions=1,
        num_keys=1,
        group_id="demo-group",
        timeout_sec=10,
        async_worker_fn=lambda _item: asyncio.sleep(0),
        process_worker_fn=lambda _item: None,
        workload="sleep",
        ordering="key_hash",
    )

    # Then: The expected run pyrparallel round omits process transport helper argument behavior is asserted.
    assert result is benchmark_result
    assert "process_transport_mode" not in captured


def test_baseline_consumer_logs_effective_topic_name(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: Inputs and test doubles are prepared for baseline consumer logs effective topic name.
    class _FakeConsumer:
        def subscribe(self, topics) -> None:
            self.topics = topics

        def poll(self, timeout: float = 1.0):
            del timeout
            return None

        def commit(self, asynchronous: bool = False) -> None:
            del asynchronous

        def close(self) -> None:
            return None

    monkeypatch.setattr(baseline_consumer, "Consumer", lambda _conf: _FakeConsumer())

    baseline_consumer.consume_messages(
        num_messages_to_process=0,
        topic_name="demo-baseline-topic",
    )

    # When: The benchmark runtime round execution path is exercised for baseline consumer logs effective topic name.
    output = capsys.readouterr().out
    # Then: The expected baseline consumer logs effective topic name behavior is asserted.
    assert "Starting baseline consumer for topic 'demo-baseline-topic'." in output


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_skips_topic_creation_after_reset(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test skips topic creation after reset.
    create_calls: list[str] = []
    reset_calls: list[tuple[dict[str, Any], list[str]]] = []

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            self._metrics = SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=0,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def start(self) -> None:
            return None

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "reset_topics_and_groups",
        lambda **kwargs: reset_calls.append(
            (kwargs["topics"], list(kwargs["consumer_groups"]))
        ),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: create_calls.append(topic_name),
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    # When: The benchmark runtime round execution path is exercised for run pyrallel consumer test skips topic creation after reset.
    (
        timed_out,
        _stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=0,
        timeout_sec=1,
        topic_name="demo-topic",
        consumer_group="demo-group",
        execution_mode="async",
        reset_topic=True,
        stats_tracker=None,
    )

    # Then: The expected run pyrallel consumer test skips topic creation after reset behavior is asserted.
    assert timed_out is True
    assert reset_calls == [
        (
            {"demo-topic": pyrallel_consumer_test.TopicConfig(num_partitions=1)},
            ["demo-group"],
        )
    ]
    assert create_calls == []


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_records_metrics_after_poller_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test records metrics after poller stop.
    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            self._lag = 99

        async def start(self) -> None:
            return None

        def get_metrics(self):
            return SimpleNamespace(
                total_in_flight=0,
                is_paused=False,
                partitions=[
                    SimpleNamespace(
                        tp=SimpleNamespace(topic="demo-topic", partition=0),
                        true_lag=self._lag,
                        gap_count=0,
                        queued_count=0,
                    )
                ],
            )

        async def stop(self) -> None:
            self._lag = 0

    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda *args, **kwargs: None,
    )
    stats = BenchmarkStats(
        run_name="demo",
        run_type="async",
        workload="io",
        topic="demo-topic",
        ordering="key_hash",
        target_messages=1,
    )

    # When: The benchmark runtime round execution path is exercised for run pyrallel consumer test records metrics after poller stop.
    (
        _timed_out,
        _stats,
        summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=1,
        timeout_sec=0,
        topic_name="demo-topic",
        consumer_group="demo-group",
        execution_mode="async",
        stats_tracker=stats,
    )

    # Then: The expected run pyrallel consumer test records metrics after poller stop behavior is asserted.
    assert summary is not None
    assert summary.final_lag == 0


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_stops_poller_and_engine_when_assignment_wait_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test stops poller and engine when assignment wait fails.
    events: list[str] = []

    class _FakeEngine:
        async def shutdown(self) -> None:
            events.append("engine.shutdown")

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        async def start(self) -> None:
            events.append("poller.start")

        def get_metrics(self):
            return SimpleNamespace(partitions=[])

        async def stop(self) -> None:
            events.append("poller.stop")

    async def _fail_wait(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("assignment failed")

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )
    # When: The benchmark runtime round execution path is exercised for run pyrallel consumer test stops poller and engine when assignment wait fails.
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _fail_wait,
    )

    # Then: The expected run pyrallel consumer test stops poller and engine when assignment wait fails behavior is asserted.
    with pytest.raises(RuntimeError, match="assignment failed"):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=1,
            timeout_sec=1,
            topic_name="demo-topic",
            execution_mode="async",
            stats_tracker=None,
        )

    assert events == ["poller.start", "poller.stop", "engine.shutdown"]


@pytest.mark.asyncio
async def test_wait_for_partition_assignment_raises_clear_error_for_topic() -> None:
    # Given: Inputs and test doubles are prepared for wait for partition assignment raises clear error for topic.
    class _NoAssignmentPoller:
        def get_metrics(self):
            return SimpleNamespace(partitions=[])

    # When: The benchmark runtime round execution path is exercised for wait for partition assignment raises clear error for topic.
    # Then: The expected wait for partition assignment raises clear error for topic behavior is asserted.
    with pytest.raises(RuntimeError, match="demo-topic"):
        await pyrallel_consumer_test._wait_for_partition_assignment(
            cast(Any, _NoAssignmentPoller()),
            topic_name="demo-topic",
            timeout_sec=0.0,
        )
