# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_benchmark_runtime_ordering.py
# Role: Verifies ordering validation and process worker behavior in benchmark runs.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._benchmark_runtime_support import (
    Any,
    BenchmarkStats,
    Callable,
    CompletionStatus,
    SimpleNamespace,
    TopicPartition,
    WorkItem,
    cast,
    pyrallel_consumer_test,
    pytest,
)


def test_ordering_validator_reports_key_hash_pass_summary() -> None:
    # Given: Inputs and test doubles are prepared for ordering validator reports key hash pass summary.
    validator = pyrallel_consumer_test.OrderingValidator(
        ordering_mode="key_hash", topic_name="demo-topic"
    )

    validator.observe(
        WorkItem(
            id="item-1",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=0,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":0}',
        )
    )
    # When: The benchmark runtime ordering validation path is exercised for ordering validator reports key hash pass summary.
    validator.observe(
        WorkItem(
            id="item-2",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=1,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":1}',
        )
    )

    # Then: The expected ordering validator reports key hash pass summary behavior is asserted.
    assert validator.summary() == "Ordering validation PASS: key_hash keys=1 checks=2"


def test_ordering_validator_allows_nonzero_first_key_hash_sequence() -> None:
    # Given: Inputs and test doubles are prepared for ordering validator allows nonzero first key hash sequence.
    validator = pyrallel_consumer_test.OrderingValidator(
        ordering_mode="key_hash", topic_name="demo-topic"
    )

    validator.observe(
        WorkItem(
            id="item-67",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=67,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":67}',
        )
    )
    # When: The benchmark runtime ordering validation path is exercised for ordering validator allows nonzero first key hash sequence.
    validator.observe(
        WorkItem(
            id="item-68",
            tp=TopicPartition(topic="demo-topic", partition=0),
            offset=68,
            epoch=0,
            key="key-0",
            payload=b'{"key":"key-0","sequence":68}',
        )
    )

    # Then: The expected ordering validator allows nonzero first key hash sequence behavior is asserted.
    assert validator.summary() == "Ordering validation PASS: key_hash keys=1 checks=2"


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_validates_key_hash_ordering_in_process_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test validates key hash ordering in process mode.
    captured_exporter: Any = None
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
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
            assert captured_process_worker is not None
            process_worker = cast(Callable[[WorkItem], None], captured_process_worker)
            for offset, sequence in ((0, 0), (1, 1)):
                item = WorkItem(
                    id=f"item-{offset}",
                    tp=TopicPartition(topic="demo-topic", partition=0),
                    offset=offset,
                    epoch=0,
                    key="key-0",
                    payload=f'{{"key":"key-0","sequence":{sequence}}}'.encode(),
                )
                process_worker(item)  # pylint: disable=not-callable
                captured_exporter.observe_work_completion(
                    SimpleNamespace(status=CompletionStatus.SUCCESS),
                    item,
                    0.01,
                )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    # When: The benchmark runtime ordering validation path is exercised for run pyrallel consumer test validates key hash ordering in process mode.
    (
        timed_out,
        consumption_stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=2,
        timeout_sec=5,
        topic_name="demo-topic",
        execution_mode="process",
        ordering_mode="key_hash",
        stats_tracker=None,
    )

    # Then: The expected run pyrallel consumer test validates key hash ordering in process mode behavior is asserted.
    assert timed_out is False
    assert consumption_stats.processed == 2
    assert (
        "Ordering validation PASS: key_hash keys=1 checks=2" in capsys.readouterr().out
    )


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_uses_picklable_process_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test uses picklable process worker.
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
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

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(
        pyrallel_consumer_test, "WorkManager", lambda **kwargs: object()
    )
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    # When: The benchmark runtime ordering validation path is exercised for run pyrallel consumer test uses picklable process worker.
    await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=1,
        timeout_sec=1,
        topic_name="demo-topic",
        execution_mode="process",
        ordering_mode="unordered",
        stats_tracker=None,
        process_worker_fn=pyrallel_consumer_test._process_mode_worker,
    )

    # Then: The expected run pyrallel consumer test uses picklable process worker behavior is asserted.
    assert captured_process_worker is pyrallel_consumer_test._process_mode_worker


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_raises_on_process_ordering_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test raises on process ordering violation.
    captured_exporter: Any = None
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
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
            assert captured_process_worker is not None
            process_worker = cast(Callable[[WorkItem], None], captured_process_worker)
            item_0 = WorkItem(
                id="item-0",
                tp=TopicPartition(topic="demo-topic", partition=0),
                offset=0,
                epoch=0,
                key="key-0",
                payload=b'{"key":"key-0","sequence":0}',
            )
            process_worker(item_0)  # pylint: disable=not-callable
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.SUCCESS,
                0.01,
            )
            captured_exporter.observe_work_completion(
                SimpleNamespace(status=CompletionStatus.SUCCESS),
                item_0,
                0.01,
            )
            item_1 = WorkItem(
                id="item-1",
                tp=TopicPartition(topic="demo-topic", partition=0),
                offset=1,
                epoch=0,
                key="key-0",
                payload=b'{"key":"key-0","sequence":3}',
            )
            process_worker(item_1)  # pylint: disable=not-callable
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.SUCCESS,
                0.01,
            )
            captured_exporter.observe_work_completion(
                SimpleNamespace(status=CompletionStatus.SUCCESS),
                item_1,
                0.01,
            )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    # When: The benchmark runtime ordering validation path is exercised for run pyrallel consumer test raises on process ordering violation.
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    # Then: The expected run pyrallel consumer test raises on process ordering violation behavior is asserted.
    with pytest.raises(
        RuntimeError, match="Ordering validation failed for key key-0 on demo-topic"
    ):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=2,
            timeout_sec=5,
            topic_name="demo-topic",
            execution_mode="process",
            ordering_mode="key_hash",
            stats_tracker=None,
        )


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_skips_process_ordering_validation_for_large_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_exporter: Any = None
    captured_process_worker: Callable[[WorkItem], None] | None = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            return None

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
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
            assert captured_process_worker is not None
            process_worker = cast(Callable[[WorkItem], None], captured_process_worker)
            for offset, sequence in ((0, 5), (1, 0)):
                item = WorkItem(
                    id=f"item-{offset}",
                    tp=TopicPartition(topic="demo-topic", partition=0),
                    offset=offset,
                    epoch=0,
                    key="key-0",
                    payload=f'{{"key":"key-0","sequence":{sequence}}}'.encode(),
                )
                process_worker(item)  # pylint: disable=not-callable
                captured_exporter.observe_work_completion(
                    SimpleNamespace(status=CompletionStatus.SUCCESS),
                    item,
                    0.01,
                )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            return None

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    def _capture_process_engine(**kwargs):
        nonlocal captured_process_worker
        captured_process_worker = kwargs["worker_fn"]
        return _FakeEngine()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "ProcessExecutionEngine", _capture_process_engine
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    stats = BenchmarkStats(
        run_name="demo",
        run_type="process",
        workload="sleep",
        ordering="key_hash",
        topic="demo-topic",
        large_payload=True,
        target_messages=2,
    )

    (
        timed_out,
        consumption_stats,
        _summary,
    ) = await pyrallel_consumer_test.run_pyrallel_consumer_test(
        num_messages=2,
        timeout_sec=5,
        topic_name="demo-topic",
        execution_mode="process",
        ordering_mode="key_hash",
        stats_tracker=stats,
    )

    assert timed_out is False
    assert consumption_stats.processed == 2


@pytest.mark.asyncio
async def test_run_pyrallel_consumer_test_raises_clear_error_on_completion_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run pyrallel consumer test raises clear error on completion failure.
    events: list[str] = []
    captured_exporter: Any = None

    class _FakeEngine:
        async def shutdown(self) -> None:
            events.append("engine.shutdown")

    class _FakeBrokerPoller:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
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
            events.append("poller.start")
            captured_exporter.observe_completion(
                TopicPartition(topic="demo-topic", partition=0),
                CompletionStatus.FAILURE,
                0.01,
            )

        def get_metrics(self):
            return self._metrics

        async def stop(self) -> None:
            events.append("poller.stop")

    def _capture_work_manager(**kwargs):
        nonlocal captured_exporter
        captured_exporter = kwargs["metrics_exporter"]
        return object()

    async def _skip_wait(*args, **kwargs) -> None:
        return None

    monkeypatch.setattr(
        pyrallel_consumer_test,
        "create_topic_if_not_exists",
        lambda _conf, topic_name, num_partitions=1: None,
    )
    monkeypatch.setattr(
        pyrallel_consumer_test, "AsyncExecutionEngine", lambda **kwargs: _FakeEngine()
    )
    monkeypatch.setattr(pyrallel_consumer_test, "BrokerPoller", _FakeBrokerPoller)
    monkeypatch.setattr(pyrallel_consumer_test, "WorkManager", _capture_work_manager)
    # When: The benchmark runtime ordering validation path is exercised for run pyrallel consumer test raises clear error on completion failure.
    monkeypatch.setattr(
        pyrallel_consumer_test,
        "_wait_for_partition_assignment",
        _skip_wait,
    )

    # Then: The expected run pyrallel consumer test raises clear error on completion failure behavior is asserted.
    with pytest.raises(
        RuntimeError,
        match="Benchmark worker failure on demo-topic\\[0\\]: completion failed",
    ):
        await pyrallel_consumer_test.run_pyrallel_consumer_test(
            num_messages=1,
            timeout_sec=5,
            topic_name="demo-topic",
            execution_mode="async",
            stats_tracker=None,
        )

    assert events == ["poller.start", "poller.stop", "engine.shutdown"]
