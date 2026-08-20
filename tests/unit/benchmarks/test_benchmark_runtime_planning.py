# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_benchmark_runtime_planning.py
# Role: Verifies benchmark matrix planning, mode expansion, process overrides, and run scheduling.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._benchmark_runtime_support import (
    BenchmarkResult,
    _build_args,
    asyncio,
    pytest,
    run_parallel_benchmark,
)


def test_run_benchmark_resets_each_mode_immediately_before_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark resets each mode immediately before round.
    events: list[tuple[str, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )

    def _record_reset(*, topics, consumer_groups, **_kwargs) -> None:
        topic_name = next(iter(topics.keys()))
        events.append(("reset", topic_name))
        events.append(("groups", consumer_groups[0]))

    def _baseline_round(**kwargs) -> BenchmarkResult:
        events.append(("run", kwargs["topic_name"]))
        return benchmark_result

    async def _async_round(**kwargs) -> BenchmarkResult:
        events.append(("run", kwargs["topic_name"]))
        return benchmark_result

    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", _record_reset
    )
    monkeypatch.setattr(run_parallel_benchmark, "_run_baseline_round", _baseline_round)
    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark resets each mode immediately before round.
    run_parallel_benchmark.run_benchmark(
        _build_args(), raw_argv=["--num-messages", "10"]
    )

    # Then: The expected run benchmark resets each mode immediately before round behavior is asserted.
    assert events == [
        ("reset", "demo-topic-sleep-key_hash-baseline"),
        ("groups", "baseline-group-sleep-key_hash"),
        ("run", "demo-topic-sleep-key_hash-baseline"),
        ("reset", "demo-topic-sleep-key_hash-async"),
        ("groups", "async-group-sleep-key_hash"),
        ("run", "demo-topic-sleep-key_hash-async"),
    ]


def test_run_benchmark_expands_selected_workloads_and_orderings(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark expands selected workloads and orderings.
    baseline_calls: list[tuple[str, str, str]] = []
    async_calls: list[tuple[str, str, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )

    def _baseline_round(**kwargs) -> BenchmarkResult:
        baseline_calls.append(
            (kwargs["run_name"], kwargs["workload"], kwargs["ordering"])
        )
        return benchmark_result

    async def _async_round(**kwargs) -> BenchmarkResult:
        async_calls.append((kwargs["run_name"], kwargs["workload"], kwargs["ordering"]))
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_baseline_round", _baseline_round)
    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark expands selected workloads and orderings.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
        ),
        raw_argv=[
            "--workloads",
            "sleep,cpu",
            "--order",
            "key_hash,partition",
        ],
    )

    # Then: The expected run benchmark expands selected workloads and orderings behavior is asserted.
    assert baseline_calls == [
        ("sleep-key_hash-baseline", "sleep", "key_hash"),
        ("sleep-partition-baseline", "sleep", "partition"),
        ("cpu-key_hash-baseline", "cpu", "key_hash"),
        ("cpu-partition-baseline", "cpu", "partition"),
    ]
    assert async_calls == [
        ("sleep-key_hash-pyrallel-async", "sleep", "key_hash"),
        ("sleep-partition-pyrallel-async", "sleep", "partition"),
        ("cpu-key_hash-pyrallel-async", "cpu", "key_hash"),
        ("cpu-partition-pyrallel-async", "cpu", "partition"),
    ]


def test_run_benchmark_expands_strict_completion_monitor_modes(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark expands strict completion monitor modes.
    async_calls: list[tuple[str, bool, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        async_calls.append(
            (
                kwargs["run_name"],
                kwargs["strict_completion_monitor_enabled"],
                kwargs["topic_name"],
            )
        )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark expands strict completion monitor modes.
    run_parallel_benchmark.run_benchmark(
        _build_args(strict_completion_monitor=["on", "off"]),
        raw_argv=["--strict-completion-monitor", "on,off"],
    )

    # Then: The expected run benchmark expands strict completion monitor modes behavior is asserted.
    assert async_calls == [
        (
            "sleep-key_hash-pyrallel-async-strict-on",
            True,
            "demo-topic-sleep-key_hash-async-strict-on",
        ),
        (
            "sleep-key_hash-pyrallel-async-strict-off",
            False,
            "demo-topic-sleep-key_hash-async-strict-off",
        ),
    ]


def test_build_parser_accepts_adaptive_concurrency_matrix() -> None:
    # Given: Inputs and test doubles are prepared for build parser accepts adaptive concurrency matrix.
    parser = run_parallel_benchmark.build_parser()

    # When: The benchmark runtime planning path is exercised for build parser accepts adaptive concurrency matrix.
    args = parser.parse_args(["--adaptive-concurrency", "off,on"])

    # Then: The expected build parser accepts adaptive concurrency matrix behavior is asserted.
    assert args.adaptive_concurrency == ["off", "on"]


def test_run_benchmark_expands_adaptive_concurrency_modes(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark expands adaptive concurrency modes.
    async_calls: list[tuple[str, bool, str, str]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        async_calls.append(
            (
                kwargs["run_name"],
                kwargs["adaptive_concurrency_enabled"],
                kwargs["topic_name"],
                kwargs["group_id"],
            )
        )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark expands adaptive concurrency modes.
    run_parallel_benchmark.run_benchmark(
        _build_args(adaptive_concurrency=["off", "on"]),
        raw_argv=["--adaptive-concurrency", "off,on"],
    )

    # Then: The expected run benchmark expands adaptive concurrency modes behavior is asserted.
    assert async_calls == [
        (
            "sleep-key_hash-pyrallel-async-adaptive-off",
            False,
            "demo-topic-sleep-key_hash-async-adaptive-off",
            "async-group-sleep-key_hash-adaptive-off",
        ),
        (
            "sleep-key_hash-pyrallel-async-adaptive-on",
            True,
            "demo-topic-sleep-key_hash-async-adaptive-on",
            "async-group-sleep-key_hash-adaptive-on",
        ),
    ]


def test_build_benchmark_run_plans_expands_selected_workloads_and_orderings() -> None:
    # Given: Inputs and test doubles are prepared for build benchmark run plans expands selected workloads and orderings.
    # When: The benchmark runtime planning path is exercised for build benchmark run plans expands selected workloads and orderings.
    plans = run_parallel_benchmark._build_benchmark_run_plans(
        _build_args(
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
            skip_process=False,
        )
    )

    # Then: The expected build benchmark run plans expands selected workloads and orderings behavior is asserted.
    assert [
        (plan.kind, plan.run_name, plan.workload, plan.ordering) for plan in plans
    ] == [
        ("baseline", "sleep-key_hash-baseline", "sleep", "key_hash"),
        ("async", "sleep-key_hash-pyrallel-async", "sleep", "key_hash"),
        ("process", "sleep-key_hash-pyrallel-process", "sleep", "key_hash"),
        ("baseline", "sleep-partition-baseline", "sleep", "partition"),
        ("async", "sleep-partition-pyrallel-async", "sleep", "partition"),
        ("process", "sleep-partition-pyrallel-process", "sleep", "partition"),
        ("baseline", "cpu-key_hash-baseline", "cpu", "key_hash"),
        ("async", "cpu-key_hash-pyrallel-async", "cpu", "key_hash"),
        ("process", "cpu-key_hash-pyrallel-process", "cpu", "key_hash"),
        ("baseline", "cpu-partition-baseline", "cpu", "partition"),
        ("async", "cpu-partition-pyrallel-async", "cpu", "partition"),
        ("process", "cpu-partition-pyrallel-process", "cpu", "partition"),
    ]


def test_build_benchmark_run_plans_preserves_mode_suffixes_and_options() -> None:
    # Given: Inputs and test doubles are prepared for build benchmark run plans preserves mode suffixes and options.
    # When: The benchmark runtime planning path is exercised for build benchmark run plans preserves mode suffixes and options.
    plans = run_parallel_benchmark._build_benchmark_run_plans(
        _build_args(
            skip_baseline=True,
            skip_process=False,
            strict_completion_monitor=["on", "off"],
            adaptive_concurrency=["off", "on"],
            workload_options={"sleep": {"sleep_ms": 1.25}},
        )
    )

    # Then: The expected build benchmark run plans preserves mode suffixes and options behavior is asserted.
    assert [
        (
            plan.kind,
            plan.run_name,
            plan.topic_name,
            plan.group_id,
            plan.strict_completion_monitor_enabled,
            plan.adaptive_concurrency_enabled,
            plan.workload_options,
        )
        for plan in plans
    ] == [
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-on-adaptive-off",
            "demo-topic-sleep-key_hash-async-strict-on-adaptive-off",
            "async-group-sleep-key_hash-strict-on-adaptive-off",
            True,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-on-adaptive-off",
            "demo-topic-sleep-key_hash-process-strict-on-adaptive-off",
            "process-group-sleep-key_hash-strict-on-adaptive-off",
            True,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-on-adaptive-on",
            "demo-topic-sleep-key_hash-async-strict-on-adaptive-on",
            "async-group-sleep-key_hash-strict-on-adaptive-on",
            True,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-on-adaptive-on",
            "demo-topic-sleep-key_hash-process-strict-on-adaptive-on",
            "process-group-sleep-key_hash-strict-on-adaptive-on",
            True,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-off-adaptive-off",
            "demo-topic-sleep-key_hash-async-strict-off-adaptive-off",
            "async-group-sleep-key_hash-strict-off-adaptive-off",
            False,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-off-adaptive-off",
            "demo-topic-sleep-key_hash-process-strict-off-adaptive-off",
            "process-group-sleep-key_hash-strict-off-adaptive-off",
            False,
            False,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "async",
            "sleep-key_hash-pyrallel-async-strict-off-adaptive-on",
            "demo-topic-sleep-key_hash-async-strict-off-adaptive-on",
            "async-group-sleep-key_hash-strict-off-adaptive-on",
            False,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
        (
            "process",
            "sleep-key_hash-pyrallel-process-strict-off-adaptive-on",
            "demo-topic-sleep-key_hash-process-strict-off-adaptive-on",
            "process-group-sleep-key_hash-strict-off-adaptive-on",
            False,
            True,
            {"sleep": {"sleep_ms": 1.25}},
        ),
    ]


def test_run_benchmark_preserves_event_loop_per_workload_ordering(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark preserves event loop per workload ordering.
    loop_runs: list[int] = []
    real_asyncio_run = asyncio.run

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )

    async def _async_round(**_kwargs) -> BenchmarkResult:
        return benchmark_result

    def _record_asyncio_run(coro):
        loop_runs.append(1)
        return real_asyncio_run(coro)

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)
    monkeypatch.setattr(run_parallel_benchmark.asyncio, "run", _record_asyncio_run)

    # When: The benchmark runtime planning path is exercised for run benchmark preserves event loop per workload ordering.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_process=False,
            skip_reset=True,
            workloads=["sleep", "cpu"],
            order=["key_hash", "partition"],
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-reset",
            "--workloads",
            "sleep,cpu",
            "--order",
            "key_hash,partition",
        ],
    )

    # Then: The expected run benchmark preserves event loop per workload ordering behavior is asserted.
    assert loop_runs == [1, 1, 1, 1]


def test_run_benchmark_passes_process_overrides_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark passes process overrides to process round.
    process_calls: list[tuple[int, int, int, str | None, int | None]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(
                (
                    kwargs["process_batch_size"],
                    kwargs["process_max_batch_wait_ms"],
                    kwargs["process_count"],
                    kwargs["process_flush_policy"],
                    kwargs["process_demand_flush_min_residence_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark passes process overrides to process round.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
            process_count=2,
            process_batch_size=1,
            process_max_batch_wait_ms=0,
            process_flush_policy="demand_min_residence",
            process_demand_flush_min_residence_ms=2,
        ),
        raw_argv=[
            "--skip-async",
            "--process-count",
            "2",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
            "--process-flush-policy",
            "demand_min_residence",
            "--process-demand-flush-min-residence-ms",
            "2",
        ],
    )

    # Then: The expected run benchmark passes process overrides to process round behavior is asserted.
    assert process_calls == [(1, 0, 2, "demand_min_residence", 2)]


def test_run_benchmark_does_not_pass_process_transport_mode_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark does not pass process transport mode to process round.
    process_calls: list[bool] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append("process_transport_mode" in kwargs)
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark does not pass process transport mode to process round.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
        ),
        raw_argv=[
            "--skip-async",
        ],
    )

    # Then: The expected run benchmark does not pass process transport mode to process round behavior is asserted.
    assert process_calls == [False]


def test_run_benchmark_passes_route_batch_size_to_process_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark passes route batch size to process round.
    process_calls: list[int] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(kwargs["route_batch_size"])
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark passes route batch size to process round.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            skip_process=False,
            route_batch_size=64,
        ),
        raw_argv=[
            "--skip-async",
            "--process-route-batch-size",
            "64",
        ],
    )

    # Then: The expected run benchmark passes route batch size to process round behavior is asserted.
    assert process_calls == [64]


def test_run_benchmark_does_not_forward_process_transport_mode_to_async_round(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark does not forward process transport mode to async round.
    async_calls: list[str | None] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "async":
            async_calls.append(kwargs.get("process_transport_mode"))
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark does not forward process transport mode to async round.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=False,
            skip_process=True,
        ),
        raw_argv=[
            "--skip-process",
        ],
    )

    # Then: The expected run benchmark does not forward process transport mode to async round behavior is asserted.
    assert async_calls == [None]


def test_run_benchmark_warns_for_tiny_partition_process_defaults(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark warns for tiny partition process defaults.
    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_pyrparallel_round",
        lambda **_kwargs: asyncio.sleep(0, result=benchmark_result),
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--order",
            "partition",
        ],
    )

    # When: The benchmark runtime planning path is exercised for run benchmark warns for tiny partition process defaults.
    output = capsys.readouterr().out
    # Then: The expected run benchmark warns for tiny partition process defaults behavior is asserted.
    assert "Tiny process partition benchmark detected" in output
    assert "--process-batch-size 1 --process-max-batch-wait-ms 0" in output


def test_run_benchmark_auto_tunes_tiny_partition_strict_process_defaults(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark auto tunes tiny partition strict process defaults.
    process_calls: list[tuple[int | None, int | None]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(
                (
                    kwargs["process_batch_size"],
                    kwargs["process_max_batch_wait_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            strict_completion_monitor=["on"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--workloads",
            "sleep",
            "--order",
            "partition",
            "--strict-completion-monitor",
            "on",
        ],
    )

    # When: The benchmark runtime planning path is exercised for run benchmark auto tunes tiny partition strict process defaults.
    output = capsys.readouterr().out
    # Then: The expected run benchmark auto tunes tiny partition strict process defaults behavior is asserted.
    assert "Auto-tuning process micro-batch for strict partition run" in output
    assert process_calls == [(1, 0)]


def test_run_benchmark_resolves_process_batching_per_strict_mode(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark resolves process batching per strict mode.
    process_calls: list[tuple[str, int | None, int | None]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )

    async def _async_round(**kwargs) -> BenchmarkResult:
        if kwargs["mode"].value == "process":
            process_calls.append(
                (
                    kwargs["run_name"],
                    kwargs["process_batch_size"],
                    kwargs["process_max_batch_wait_ms"],
                )
            )
        return benchmark_result

    monkeypatch.setattr(run_parallel_benchmark, "_run_pyrparallel_round", _async_round)

    # When: The benchmark runtime planning path is exercised for run benchmark resolves process batching per strict mode.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            strict_completion_monitor=["on", "off"],
            worker_sleep_ms=0.5,
            process_batch_size=None,
            process_max_batch_wait_ms=None,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--workloads",
            "sleep",
            "--order",
            "partition",
            "--strict-completion-monitor",
            "on,off",
        ],
    )

    # Then: The expected run benchmark resolves process batching per strict mode behavior is asserted.
    assert process_calls == [
        ("sleep-partition-pyrallel-process-strict-on", 1, 0),
        ("sleep-partition-pyrallel-process-strict-off", None, None),
    ]


def test_run_benchmark_skips_tiny_partition_warning_when_batching_is_overridden(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark skips tiny partition warning when batching is overridden.
    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_select_workers",
        lambda **_kwargs: (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        ),
    )
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_pyrparallel_round",
        lambda **_kwargs: asyncio.sleep(0, result=benchmark_result),
    )

    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_baseline=True,
            skip_async=True,
            skip_process=False,
            workloads=["sleep"],
            order=["partition"],
            worker_sleep_ms=0.5,
            process_batch_size=1,
            process_max_batch_wait_ms=0,
        ),
        raw_argv=[
            "--skip-baseline",
            "--skip-async",
            "--order",
            "partition",
            "--process-batch-size",
            "1",
            "--process-max-batch-wait-ms",
            "0",
        ],
    )

    # When: The benchmark runtime planning path is exercised for run benchmark skips tiny partition warning when batching is overridden.
    output = capsys.readouterr().out
    # Then: The expected run benchmark skips tiny partition warning when batching is overridden behavior is asserted.
    assert "Tiny process partition benchmark detected" not in output


def test_run_benchmark_filters_workload_options_per_selected_workload(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark filters workload options per selected workload.
    selected_options: list[tuple[str, dict[str, dict[str, object]] | None]] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )

    def _select_workers(**kwargs):
        selected_options.append((kwargs["workload"], kwargs["workload_options"]))
        return (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        )

    monkeypatch.setattr(run_parallel_benchmark, "_select_workers", _select_workers)
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    # When: The benchmark runtime planning path is exercised for run benchmark filters workload options per selected workload.
    run_parallel_benchmark.run_benchmark(
        _build_args(
            skip_async=True,
            workloads=["sleep", "cpu"],
            workload_options={
                "sleep": {"sleep_ms": 1.25},
                "cpu": {"iterations": 2000},
            },
        ),
        raw_argv=[
            "--skip-async",
            "--workloads",
            "sleep,cpu",
            "--workload-option",
            "sleep.sleep_ms=1.25",
            "--workload-option",
            "cpu.iterations=2000",
        ],
    )

    # Then: The expected run benchmark filters workload options per selected workload behavior is asserted.
    assert selected_options == [
        ("sleep", {"sleep": {"sleep_ms": 1.25}}),
        ("cpu", {"cpu": {"iterations": 2000}}),
    ]


def test_run_benchmark_skips_process_worker_validation_when_process_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
    benchmark_result: BenchmarkResult,
) -> None:
    # Given: Inputs and test doubles are prepared for run benchmark skips process worker validation when process is skipped.
    validate_process_flags: list[bool] = []

    monkeypatch.setattr(
        run_parallel_benchmark, "_check_kafka_connection", lambda _bootstrap: None
    )

    def _select_workers(**kwargs):
        validate_process_flags.append(kwargs["validate_process_worker"])
        return (
            lambda _payload: None,
            lambda _item: None,
            lambda _item: None,
        )

    monkeypatch.setattr(run_parallel_benchmark, "_select_workers", _select_workers)
    monkeypatch.setattr(run_parallel_benchmark, "_print_table", lambda _results: None)
    monkeypatch.setattr(
        run_parallel_benchmark,
        "write_results_json",
        lambda _results, _path, options=None, artifact_metadata=None: None,
    )
    monkeypatch.setattr(
        run_parallel_benchmark, "reset_topics_and_groups", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        run_parallel_benchmark,
        "_run_baseline_round",
        lambda **_kwargs: benchmark_result,
    )

    # When: The benchmark runtime planning path is exercised for run benchmark skips process worker validation when process is skipped.
    run_parallel_benchmark.run_benchmark(
        _build_args(skip_async=True, skip_process=True),
        raw_argv=["--skip-async", "--skip-process"],
    )

    # Then: The expected run benchmark skips process worker validation when process is skipped behavior is asserted.
    assert validate_process_flags == [False]
