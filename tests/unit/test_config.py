# -*- coding: utf-8 -*-
# File: tests/unit/test_config.py
# Role: Verifies configuration defaults, environment overrides, validation bounds, and Kafka client config serialization.
# Extend here for config surface, environment variable, and rdkafka mapping changes.

from pathlib import Path
from typing import Any, cast

import pytest
from _pytest.monkeypatch import MonkeyPatch
from pydantic import ValidationError

import pyrallel_consumer.config as config_module
from pyrallel_consumer.config import (
    CommitCoordinatorConfig,
    ExecutionConfig,
    KafkaConfig,
    MetricsConfig,
    ParallelConsumerConfig,
    ProcessConfig,
)
from pyrallel_consumer.dto import ExecutionMode, OrderingMode

BENCHMARK_RUNTIME_ENV_SAMPLE_KEYS = {
    "EXECUTION_ASYNC_CONFIG__SHUTDOWN_GRACE_TIMEOUT_MS",
    "EXECUTION_ASYNC_CONFIG__TASK_TIMEOUT_MS",
    "EXECUTION_CONSUMER_TASK_STOP_TIMEOUT_MS",
    "EXECUTION_MAX_IN_FLIGHT",
    "EXECUTION_SHUTDOWN_DRAIN_TIMEOUT_MS",
    "EXECUTION_SHUTDOWN_POLICY",
    "KAFKA_DLQ_FLUSH_TIMEOUT_MS",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__COOLDOWN_MS",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__ENABLED",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__HIGH_LATENCY_THRESHOLD_MS",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__LAG_SCALE_UP_THRESHOLD",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__LOW_LATENCY_THRESHOLD_MS",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__MIN_IN_FLIGHT",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__SCALE_DOWN_STEP",
    "PARALLEL_CONSUMER_ADAPTIVE_BACKPRESSURE__SCALE_UP_STEP",
    "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__COOLDOWN_MS",
    "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED",
    "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__MIN_IN_FLIGHT",
    "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_DOWN_STEP",
    "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_UP_STEP",
    "PARALLEL_CONSUMER_COMMIT_DEBOUNCE_COMPLETION_THRESHOLD",
    "PARALLEL_CONSUMER_COMMIT_DEBOUNCE_INTERVAL_MS",
    "PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS",
    "PARALLEL_CONSUMER_MESSAGE_CACHE_MAX_BYTES",
    "PARALLEL_CONSUMER_ORDERING_MODE",
    "PARALLEL_CONSUMER_POISON_MESSAGE__COOLDOWN_MS",
    "PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED",
    "PARALLEL_CONSUMER_POISON_MESSAGE__FAILURE_THRESHOLD",
    "PARALLEL_CONSUMER_QUEUE_MAX_MESSAGES",
    "PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY",
    "PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED",
    "PROCESS_DEMAND_FLUSH_MIN_RESIDENCE_MS",
    "PROCESS_FLUSH_POLICY",
    "PROCESS_MAX_BATCH_WAIT_MS",
    "PROCESS_MAX_TASKS_PER_CHILD",
    "PROCESS_RECYCLE_JITTER_MS",
    "PROCESS_ROUTE_BATCH_SIZE",
    "PROCESS_SHUTDOWN_DRAIN_TIMEOUT_MS",
}


def _env_sample_keys() -> set[str]:
    keys: set[str] = set()
    for raw_line in Path(".env.sample").read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            line = line[1:].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def test_env_sample_documents_benchmark_runtime_tuning_surface() -> None:
    # Given: .env.sample and the benchmark runtime tuning key allowlist are available.
    sample_keys = _env_sample_keys()

    missing_keys = sorted(BENCHMARK_RUNTIME_ENV_SAMPLE_KEYS - sample_keys)

    # When: the documented sample keys are compared with the required tuning keys.
    # Then: no benchmark runtime tuning keys are missing from .env.sample.
    assert missing_keys == []


def test_env_sample_does_not_document_removed_process_transport_mode() -> None:
    # Given: .env.sample is available for environment surface inspection.
    # When: the sample keys are checked for the removed process transport variable.
    # Then: PROCESS_TRANSPORT_MODE is not documented as a supported setting.
    assert "PROCESS_TRANSPORT_MODE" not in _env_sample_keys()


def test_env_sample_does_not_document_deprecated_execution_route_batch_size() -> None:
    # Given: .env.sample is available for deprecated variable inspection.
    # When: the sample keys are checked for the old execution route batch variable.
    # Then: EXECUTION_ROUTE_BATCH_SIZE is absent from the documented environment surface.
    assert "EXECUTION_ROUTE_BATCH_SIZE" not in _env_sample_keys()


def test_parallel_consumer_config_defaults():
    # Given: a ParallelConsumerConfig is created without overrides.
    config = ParallelConsumerConfig()

    # When: its default control-plane, concurrency, and poison-message settings are read.
    # Then: the defaults match the expected safe runtime configuration.
    assert config.blocking_warn_seconds == 5.0
    assert config.message_cache_max_bytes == 64 * 1024 * 1024
    assert config.max_blocking_duration_ms == 0
    assert config.ordering_mode == OrderingMode.KEY_HASH
    assert config.strict_completion_monitor_enabled is True
    assert config.commit_debounce_completion_threshold == 100
    assert config.commit_debounce_interval_ms == 100
    assert config.adaptive_concurrency.enabled is False
    assert config.adaptive_concurrency.min_in_flight == 0
    assert config.poison_message.enabled is False
    assert config.poison_message.failure_threshold == 3
    assert config.poison_message.cooldown_ms == 30000
    assert isinstance(config.commit_coordinator, CommitCoordinatorConfig)
    assert config.commit_coordinator.enabled is False
    assert config.commit_coordinator.queue_max_partitions == 1024
    assert config.commit_coordinator.retry_backoff_ms == 100
    assert config.commit_coordinator.max_retry_backoff_ms == 5000
    assert config.commit_coordinator.stop_drain_timeout_ms == 5000


def test_parallel_consumer_config_commit_coordinator_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: commit coordinator environment variables are set to concrete override values.
    monkeypatch.setenv("PARALLEL_CONSUMER_COMMIT_COORDINATOR__ENABLED", "true")
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__QUEUE_MAX_PARTITIONS", "7"
    )
    monkeypatch.setenv("PARALLEL_CONSUMER_COMMIT_COORDINATOR__RETRY_BACKOFF_MS", "11")
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__MAX_RETRY_BACKOFF_MS", "111"
    )
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__STOP_DRAIN_TIMEOUT_MS", "222"
    )

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads commit coordinator settings from the environment.
    # Then: the nested commit coordinator config reflects the provided override values.
    assert config.commit_coordinator.enabled is True
    assert config.commit_coordinator.queue_max_partitions == 7
    assert config.commit_coordinator.retry_backoff_ms == 11
    assert config.commit_coordinator.max_retry_backoff_ms == 111
    assert config.commit_coordinator.stop_drain_timeout_ms == 222

    monkeypatch.delenv("PARALLEL_CONSUMER_COMMIT_COORDINATOR__ENABLED", raising=False)
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__QUEUE_MAX_PARTITIONS", raising=False
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__RETRY_BACKOFF_MS", raising=False
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__MAX_RETRY_BACKOFF_MS", raising=False
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_COMMIT_COORDINATOR__STOP_DRAIN_TIMEOUT_MS", raising=False
    )


def test_parallel_consumer_config_poison_message_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: poison message environment variables are set to enabled, threshold 2, and cooldown 7500.
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED", "true")
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__FAILURE_THRESHOLD", "2")
    monkeypatch.setenv("PARALLEL_CONSUMER_POISON_MESSAGE__COOLDOWN_MS", "7500")

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads poison message settings from the environment.
    # Then: the nested poison message circuit config reflects those override values.
    assert config.poison_message.enabled is True
    assert config.poison_message.failure_threshold == 2
    assert config.poison_message.cooldown_ms == 7500

    monkeypatch.delenv("PARALLEL_CONSUMER_POISON_MESSAGE__ENABLED", raising=False)
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_POISON_MESSAGE__FAILURE_THRESHOLD",
        raising=False,
    )
    monkeypatch.delenv("PARALLEL_CONSUMER_POISON_MESSAGE__COOLDOWN_MS", raising=False)


def test_parallel_consumer_config_adaptive_concurrency_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: adaptive concurrency environment variables are set to enabled with min 64 and step 32.
    monkeypatch.setenv("PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED", "true")
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__MIN_IN_FLIGHT",
        "64",
    )
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_UP_STEP",
        "32",
    )

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads adaptive concurrency settings from the environment.
    # Then: the nested adaptive concurrency config reflects those override values.
    assert config.adaptive_concurrency.enabled is True
    assert config.adaptive_concurrency.min_in_flight == 64
    assert config.adaptive_concurrency.scale_up_step == 32

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__ENABLED",
        raising=False,
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__MIN_IN_FLIGHT",
        raising=False,
    )
    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ADAPTIVE_CONCURRENCY__SCALE_UP_STEP",
        raising=False,
    )


def test_execution_config_shutdown_policy_defaults_to_graceful() -> None:
    # Given: an async ExecutionConfig is created without shutdown overrides.
    config = ExecutionConfig(mode=ExecutionMode.ASYNC)

    # When: shutdown policy and drain timeout fields are resolved.
    # Then: the graceful shutdown defaults resolve to 5000 ms.
    assert config.shutdown_policy == "graceful"
    assert config.consumer_task_stop_timeout_ms == 5000
    assert config.shutdown_drain_timeout_ms == 5000
    assert config.resolve_shutdown_drain_timeout_ms() == 5000


def test_execution_config_shutdown_policy_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: shutdown policy is set to abort and drain timeout to 250 via environment variables.
    monkeypatch.setenv("EXECUTION_SHUTDOWN_POLICY", "abort")
    monkeypatch.setenv("EXECUTION_SHUTDOWN_DRAIN_TIMEOUT_MS", "250")

    config = ExecutionConfig()

    # When: ExecutionConfig loads shutdown settings from the environment.
    # Then: abort mode preserves the configured value but resolves the drain timeout to zero.
    assert config.shutdown_policy == "abort"
    assert config.shutdown_drain_timeout_ms == 250
    assert config.resolve_shutdown_drain_timeout_ms() == 0

    monkeypatch.delenv("EXECUTION_SHUTDOWN_POLICY", raising=False)
    monkeypatch.delenv("EXECUTION_SHUTDOWN_DRAIN_TIMEOUT_MS", raising=False)


def test_execution_config_has_no_route_batch_size_surface(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: the deprecated EXECUTION_ROUTE_BATCH_SIZE variable and constructor field are provided.
    monkeypatch.setenv("EXECUTION_ROUTE_BATCH_SIZE", "64")

    config = ExecutionConfig(route_batch_size=64)

    # When: ExecutionConfig is built with the deprecated route batch input.
    # Then: route_batch_size is not exposed on ExecutionConfig.
    assert not hasattr(config, "route_batch_size")

    monkeypatch.delenv("EXECUTION_ROUTE_BATCH_SIZE", raising=False)


def test_process_config_defaults_to_worker_pipes_route_batch_profile() -> None:
    # Given: a ProcessConfig is created without environment file overrides.
    config = cast(Any, ProcessConfig)(_env_file=None)

    # When: the process batching profile defaults are read.
    # Then: worker-pipe routing uses batch size 1, wait 0, and route batch size 64.
    assert not hasattr(config, "transport_mode")
    assert config.batch_size == 1
    assert config.max_batch_wait_ms == 0
    assert config.route_batch_size == 64


def test_process_config_route_batch_size_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: PROCESS_ROUTE_BATCH_SIZE is set to 32.
    monkeypatch.setenv("PROCESS_ROUTE_BATCH_SIZE", "32")

    config = ProcessConfig()

    # When: ProcessConfig loads route batch settings from the environment.
    # Then: route_batch_size is overridden to 32.
    assert config.route_batch_size == 32

    monkeypatch.delenv("PROCESS_ROUTE_BATCH_SIZE", raising=False)


def test_process_config_rejects_invalid_route_batch_size() -> None:
    # Given: route_batch_size is provided as zero.
    # When: ProcessConfig validates the invalid route batch size.
    # Then: a validation error names route_batch_size.
    with pytest.raises(ValidationError) as excinfo:
        ProcessConfig(route_batch_size=0)

    assert "route_batch_size" in str(excinfo.value)


def test_resolve_work_manager_route_batch_size_uses_process_profile() -> None:
    # Given: a process-mode ParallelConsumerConfig has process route_batch_size set to 64.
    resolver = getattr(config_module, "resolve_work_manager_route_batch_size", None)
    # When: resolve_work_manager_route_batch_size is invoked for the process config.
    # Then: the work manager route batch size resolves to 64.
    assert callable(resolver)
    config = ParallelConsumerConfig(_env_file=None)
    config.execution.mode = ExecutionMode.PROCESS
    config.execution.process_config.route_batch_size = 64

    assert resolver(config) == 64


def test_resolve_work_manager_route_batch_size_rejects_bool_process_profile() -> None:
    # Given: a process-mode config has route_batch_size forced to boolean True.
    resolver = getattr(config_module, "resolve_work_manager_route_batch_size", None)
    # When: resolve_work_manager_route_batch_size validates the process profile value.
    # Then: a ValueError rejects the non-integer route batch size.
    assert callable(resolver)
    config = ParallelConsumerConfig(_env_file=None)
    config.execution.mode = ExecutionMode.PROCESS
    config.execution.process_config.route_batch_size = cast(Any, True)

    with pytest.raises(ValueError, match="route_batch_size"):
        resolver(config)


def test_resolve_work_manager_route_batch_size_rejects_non_int_process_profile() -> (
    None
):
    # Given: a process-mode config has route_batch_size forced to string 64.
    resolver = getattr(config_module, "resolve_work_manager_route_batch_size", None)
    # When: resolve_work_manager_route_batch_size validates the process profile value.
    # Then: a ValueError rejects the non-integer route batch size.
    assert callable(resolver)
    config = ParallelConsumerConfig(_env_file=None)
    config.execution.mode = ExecutionMode.PROCESS
    config.execution.process_config.route_batch_size = cast(Any, "64")

    with pytest.raises(ValueError, match="route_batch_size"):
        resolver(config)


def test_resolve_work_manager_route_batch_size_keeps_async_item_level() -> None:
    # Given: an async-mode config has process route_batch_size set to 32.
    resolver = getattr(config_module, "resolve_work_manager_route_batch_size", None)
    # When: resolve_work_manager_route_batch_size is invoked for the async config.
    # Then: async execution keeps item-level route batch size 1.
    assert callable(resolver)
    config = ParallelConsumerConfig(_env_file=None)
    config.execution.mode = ExecutionMode.ASYNC
    config.execution.process_config.route_batch_size = 32

    assert resolver(config) == 1


def test_kafka_config_parent_env_file_propagates_nested_runtime_settings(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a temporary env file defines ordering mode, execution mode, and route batch size.
    monkeypatch.delenv("PARALLEL_CONSUMER_ORDERING_MODE", raising=False)
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.delenv("PROCESS_ROUTE_BATCH_SIZE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "PARALLEL_CONSUMER_ORDERING_MODE=partition",
                "EXECUTION_MODE=process",
                "PROCESS_ROUTE_BATCH_SIZE=77",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = KafkaConfig(_env_file=env_file)

    # When: KafkaConfig loads nested runtime settings from the parent env file.
    # Then: parallel consumer, execution, and process nested settings receive the env values.
    assert config.parallel_consumer.ordering_mode == OrderingMode.PARTITION
    assert config.parallel_consumer.execution.mode == ExecutionMode.PROCESS
    assert config.parallel_consumer.execution.process_config.route_batch_size == 77


def test_kafka_config_parent_env_file_propagates_through_partial_nested_dicts(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a temporary env file defines execution mode and route batch size alongside a partial nested dict.
    monkeypatch.delenv("EXECUTION_MODE", raising=False)
    monkeypatch.delenv("PROCESS_ROUTE_BATCH_SIZE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "EXECUTION_MODE=process",
                "PROCESS_ROUTE_BATCH_SIZE=77",
                "",
            ]
        ),
        encoding="utf-8",
    )

    config = KafkaConfig(
        _env_file=env_file,
        parallel_consumer={"ordering_mode": "key_hash"},
    )

    # When: KafkaConfig merges parent env values through the partial parallel_consumer input.
    # Then: explicit ordering is preserved while nested execution settings come from the env file.
    assert config.parallel_consumer.ordering_mode == OrderingMode.KEY_HASH
    assert config.parallel_consumer.execution.mode == ExecutionMode.PROCESS
    assert config.parallel_consumer.execution.process_config.route_batch_size == 77


def test_parallel_consumer_env_file_propagates_through_partial_execution_dict(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a temporary env file defines PROCESS_ROUTE_BATCH_SIZE and execution input only specifies async mode.
    monkeypatch.delenv("PROCESS_ROUTE_BATCH_SIZE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PROCESS_ROUTE_BATCH_SIZE=77\n", encoding="utf-8")

    config = ParallelConsumerConfig(
        _env_file=env_file,
        execution={"mode": "async"},
    )

    # When: ParallelConsumerConfig merges env values through the partial execution dict.
    # Then: async mode is preserved while process_config.route_batch_size comes from the env file.
    assert config.execution.mode == ExecutionMode.ASYNC
    assert config.execution.process_config.route_batch_size == 77


def test_execution_config_env_file_propagates_through_partial_process_dict(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: a temporary env file defines PROCESS_ROUTE_BATCH_SIZE and process_config only specifies process_count.
    monkeypatch.delenv("PROCESS_ROUTE_BATCH_SIZE", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("PROCESS_ROUTE_BATCH_SIZE=77\n", encoding="utf-8")

    config = ExecutionConfig(
        _env_file=env_file,
        mode="process",
        process_config={"process_count": 2},
    )

    # When: ExecutionConfig merges env values through the partial process_config dict.
    # Then: process mode and process_count are preserved while route_batch_size comes from the env file.
    assert config.mode == ExecutionMode.PROCESS
    assert config.process_config.process_count == 2
    assert config.process_config.route_batch_size == 77


def test_parallel_consumer_config_env_override(monkeypatch: MonkeyPatch) -> None:
    # Given: PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS is set to 2500.
    monkeypatch.setenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", "2500")

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads the blocking duration setting from the environment.
    # Then: max_blocking_duration_ms is overridden to 2500.
    assert config.max_blocking_duration_ms == 2500

    monkeypatch.delenv("PARALLEL_CONSUMER_MAX_BLOCKING_DURATION_MS", raising=False)


def test_parallel_consumer_config_rebalance_state_strategy_defaults() -> None:
    # Given: a ParallelConsumerConfig is created without rebalance strategy overrides.
    config = ParallelConsumerConfig()

    # When: the rebalance state strategy default is read.
    # Then: the default strategy is contiguous_only.
    assert config.rebalance_state_strategy == "contiguous_only"


def test_parallel_consumer_config_rebalance_state_strategy_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY is set to metadata_snapshot.
    monkeypatch.setenv(
        "PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY", "metadata_snapshot"
    )

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads the rebalance strategy from the environment.
    # Then: rebalance_state_strategy is overridden to metadata_snapshot.
    assert config.rebalance_state_strategy == "metadata_snapshot"

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_REBALANCE_STATE_STRATEGY",
        raising=False,
    )


def test_parallel_consumer_config_ordering_mode_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: PARALLEL_CONSUMER_ORDERING_MODE is set to partition.
    monkeypatch.setenv("PARALLEL_CONSUMER_ORDERING_MODE", "partition")

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads ordering mode from the environment.
    # Then: ordering_mode is overridden to partition.
    assert config.ordering_mode == OrderingMode.PARTITION

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_ORDERING_MODE",
        raising=False,
    )


def test_parallel_consumer_config_can_disable_strict_completion_monitor(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED is set to false.
    monkeypatch.setenv("PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED", "false")

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads the strict completion monitor flag.
    # Then: strict_completion_monitor_enabled is disabled.
    assert config.strict_completion_monitor_enabled is False

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_STRICT_COMPLETION_MONITOR_ENABLED",
        raising=False,
    )


def test_parallel_consumer_config_commit_debounce_env_override(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: commit debounce threshold and interval environment variables are set.
    monkeypatch.setenv("PARALLEL_CONSUMER_COMMIT_DEBOUNCE_COMPLETION_THRESHOLD", "32")
    monkeypatch.setenv("PARALLEL_CONSUMER_COMMIT_DEBOUNCE_INTERVAL_MS", "25")

    config = ParallelConsumerConfig()

    # When: ParallelConsumerConfig loads commit debounce settings from the environment.
    # Then: the debounce threshold and interval reflect the provided values.
    assert config.commit_debounce_completion_threshold == 32
    assert config.commit_debounce_interval_ms == 25

    monkeypatch.delenv(
        "PARALLEL_CONSUMER_COMMIT_DEBOUNCE_COMPLETION_THRESHOLD",
        raising=False,
    )
    monkeypatch.delenv("PARALLEL_CONSUMER_COMMIT_DEBOUNCE_INTERVAL_MS", raising=False)


def test_parallel_consumer_config_rejects_zero_batch_and_worker_pool_size() -> None:
    # Given: poll_batch_size and worker_pool_size are each provided as zero.
    # When: ParallelConsumerConfig validates each unsafe zero value.
    # Then: validation errors identify the offending field and greater-than-zero rule.
    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(poll_batch_size=0)
    assert "poll_batch_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)

    with pytest.raises(ValidationError) as excinfo:
        _ = ParallelConsumerConfig(worker_pool_size=0)
    assert "worker_pool_size" in str(excinfo.value)
    assert "greater than 0" in str(excinfo.value)


@pytest.mark.parametrize(
    ("config_type", "kwargs", "field_name"),
    [
        (ExecutionConfig, {"max_in_flight": 0}, "max_in_flight"),
        (ExecutionConfig, {"max_in_flight": -1}, "max_in_flight"),
        (ProcessConfig, {"process_count": 0}, "process_count"),
        (ProcessConfig, {"queue_size": 0}, "queue_size"),
        (ProcessConfig, {"batch_size": 0}, "batch_size"),
        (ProcessConfig, {"msgpack_max_bytes": 0}, "msgpack_max_bytes"),
        (MetricsConfig, {"port": 0}, "port"),
        (MetricsConfig, {"port": 65536}, "port"),
    ],
)
def test_resource_config_rejects_unsafe_bounds(
    config_type, kwargs: dict[str, int], field_name: str
) -> None:
    # Given: a config type, invalid numeric kwargs, and expected field name are provided.
    # When: the config type validates the unsafe bound.
    # Then: a validation error includes the expected field name.
    with pytest.raises(ValidationError) as excinfo:
        _ = config_type(**kwargs)

    assert field_name in str(excinfo.value)


def test_execution_config_consumer_stop_timeout_default() -> None:
    # Given: an async ExecutionConfig is created without timeout overrides.
    config = ExecutionConfig(mode=ExecutionMode.ASYNC)

    # When: consumer stop and drain timeouts are resolved.
    # Then: both default to 5000 ms and resolve to 5000 ms.
    assert config.consumer_task_stop_timeout_ms == 5000
    assert config.shutdown_drain_timeout_ms == 5000
    assert config.resolve_shutdown_drain_timeout_ms() == 5000


def test_execution_config_rejects_negative_consumer_stop_timeout() -> None:
    # Given: consumer_task_stop_timeout_ms is provided as -1.
    # When: ExecutionConfig validates the negative timeout.
    # Then: a validation error identifies consumer_task_stop_timeout_ms.
    with pytest.raises(ValidationError) as excinfo:
        _ = ExecutionConfig(consumer_task_stop_timeout_ms=-1)

    assert "consumer_task_stop_timeout_ms" in str(excinfo.value)


def test_execution_config_accepts_zero_consumer_stop_timeout() -> None:
    # Given: consumer_task_stop_timeout_ms is provided as zero.
    config = ExecutionConfig(consumer_task_stop_timeout_ms=0)

    # When: ExecutionConfig validates the zero timeout.
    # Then: zero is accepted as the configured consumer stop timeout.
    assert config.consumer_task_stop_timeout_ms == 0


def test_kafka_config_exposes_canonical_snake_case_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: legacy and canonical Kafka environment variables are cleared.
    for name in (
        "BOOTSTRAP_SERVERS",
        "CONSUMER_GROUP",
        "DLQ_TOPIC_SUFFIX",
        "DLQ_FLUSH_TIMEOUT_MS",
        "AUTO_OFFSET_RESET",
        "ENABLE_AUTO_COMMIT",
        "SESSION_TIMEOUT_MS",
        "KAFKA_BOOTSTRAP_SERVERS",
        "KAFKA_CONSUMER_GROUP",
        "KAFKA_DLQ_TOPIC_SUFFIX",
        "KAFKA_DLQ_FLUSH_TIMEOUT_MS",
        "KAFKA_AUTO_OFFSET_RESET",
        "KAFKA_ENABLE_AUTO_COMMIT",
        "KAFKA_SESSION_TIMEOUT_MS",
    ):
        monkeypatch.delenv(name, raising=False)

    config = KafkaConfig(_env_file=None)

    # When: KafkaConfig is created without env file overrides.
    # Then: canonical snake_case fields expose the expected default values.
    assert config.bootstrap_servers == ["localhost:9092"]
    assert config.consumer_group == "pyrallel-consumer-group"
    assert config.dlq_topic_suffix == ".dlq"
    assert config.auto_offset_reset == "earliest"
    assert config.enable_auto_commit is False
    assert config.session_timeout_ms == 60000


def test_kafka_config_accepts_snake_case_constructor_fields() -> None:
    # Given: canonical snake_case Kafka constructor fields are provided.
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["kafka-1:9092", "kafka-2:9092"],
        consumer_group="demo-group",
        dlq_topic_suffix=".failed",
        dlq_flush_timeout_ms=1234,
        auto_offset_reset="latest",
        enable_auto_commit=True,
        session_timeout_ms=7777,
    )

    # When: KafkaConfig is built from those constructor values.
    # Then: the canonical fields retain the provided values.
    assert config.bootstrap_servers == ["kafka-1:9092", "kafka-2:9092"]
    assert config.consumer_group == "demo-group"
    assert config.dlq_topic_suffix == ".failed"
    assert config.dlq_flush_timeout_ms == 1234
    assert config.auto_offset_reset == "latest"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 7777


def test_kafka_config_normalizes_string_bootstrap_servers_after_assignment() -> None:
    # Given: bootstrap_servers is assigned as a comma-delimited string.
    config = KafkaConfig(_env_file=None)

    config.bootstrap_servers = "kafka-1:9092,kafka-2:9092"

    # When: producer and consumer client configs are generated.
    # Then: both client configs use the normalized bootstrap server string.
    assert (
        config.get_producer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )
    assert (
        config.get_consumer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )


def test_kafka_config_preserves_list_bootstrap_servers_after_assignment() -> None:
    # Given: bootstrap_servers is assigned as a broker list.
    config = KafkaConfig(_env_file=None)

    config.bootstrap_servers = ["kafka-1:9092", "kafka-2:9092"]

    # When: producer and consumer client configs are generated.
    # Then: both client configs join the broker list into the expected rdkafka string.
    assert (
        config.get_producer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )
    assert (
        config.get_consumer_config()["bootstrap.servers"] == "kafka-1:9092,kafka-2:9092"
    )


def test_kafka_config_includes_allowlisted_security_fields_in_client_configs() -> None:
    # Given: secure Kafka connection and credential fields are provided.
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        consumer_group="secure-group",
        security_protocol="SASL_SSL",
        sasl_mechanisms="SCRAM-SHA-512",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_ca_location="/etc/kafka/ca.pem",
        ssl_certificate_location="/etc/kafka/client.pem",
        ssl_key_location="/etc/kafka/client.key",
        ssl_key_password="key-secret",
    )

    expected_security_config = {
        "bootstrap.servers": "secure-1:9093,secure-2:9093",
        "security.protocol": "SASL_SSL",
        "sasl.mechanisms": "SCRAM-SHA-512",
        "sasl.username": "pyrallel-user",
        "sasl.password": "super-secret",
        "ssl.ca.location": "/etc/kafka/ca.pem",
        "ssl.certificate.location": "/etc/kafka/client.pem",
        "ssl.key.location": "/etc/kafka/client.key",
        "ssl.key.password": "key-secret",
    }

    producer_config = config.get_producer_config()
    consumer_config = config.get_consumer_config()
    admin_config = config.get_admin_config()

    # When: producer, consumer, and admin client configs are generated.
    # Then: allowlisted security fields are forwarded to every client config.
    for key, value in expected_security_config.items():
        assert producer_config[key] == value
        assert consumer_config[key] == value
        assert admin_config[key] == value
    assert consumer_config["group.id"] == "secure-group"


def test_kafka_config_masks_secret_security_fields_in_snapshots() -> None:
    # Given: KafkaConfig contains SASL and SSL key secrets.
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        security_protocol="SASL_SSL",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_key_password="key-secret",
    )

    dumped_json = config.model_dump_json()
    redacted_snapshot = config.dump_to_rdkafka()
    rdkafka_snapshot = repr(redacted_snapshot)

    # When: model JSON and redacted rdkafka snapshots are rendered.
    # Then: secret values are absent from snapshots while non-secret fields remain visible.
    assert "super-secret" not in dumped_json
    assert "key-secret" not in dumped_json
    assert "super-secret" not in rdkafka_snapshot
    assert "key-secret" not in rdkafka_snapshot
    assert redacted_snapshot["bootstrap.servers"] == "secure-1:9093,secure-2:9093"


def test_kafka_config_get_rdkafka_config_includes_secret_security_fields() -> None:
    # Given: KafkaConfig contains SASL and SSL key secrets.
    config = KafkaConfig(
        _env_file=None,
        bootstrap_servers=["secure-1:9093", "secure-2:9093"],
        security_protocol="SASL_SSL",
        sasl_username="pyrallel-user",
        sasl_password="super-secret",
        ssl_key_password="key-secret",
    )

    rdkafka_config = config.get_rdkafka_config()

    # When: the raw rdkafka client config is generated.
    # Then: secret values are included for the actual client configuration path.
    assert rdkafka_config["bootstrap.servers"] == "secure-1:9093,secure-2:9093"
    assert rdkafka_config["security.protocol"] == "SASL_SSL"
    assert rdkafka_config["sasl.username"] == "pyrallel-user"
    assert rdkafka_config["sasl.password"] == "super-secret"
    assert rdkafka_config["ssl.key.password"] == "key-secret"


def test_kafka_config_omits_blank_security_fields_from_client_configs() -> None:
    # Given: Kafka security fields are provided as blank strings or whitespace.
    config = KafkaConfig(
        _env_file=None,
        security_protocol="",
        sasl_mechanisms="  ",
        sasl_username="",
        sasl_password="",
        ssl_ca_location="",
        ssl_certificate_location="",
        ssl_key_location="",
        ssl_key_password="",
    )

    producer_config = config.get_producer_config()

    # When: the producer client config is generated.
    # Then: blank security fields are omitted from the client config.
    assert "security.protocol" not in producer_config
    assert "sasl.mechanisms" not in producer_config
    assert "sasl.username" not in producer_config
    assert "sasl.password" not in producer_config
    assert "ssl.ca.location" not in producer_config
    assert "ssl.certificate.location" not in producer_config
    assert "ssl.key.location" not in producer_config
    assert "ssl.key.password" not in producer_config


def test_kafka_config_preserves_boundary_whitespace_in_secret_fields() -> None:
    # Given: SASL and SSL key secrets include leading or trailing whitespace.
    config = KafkaConfig(
        _env_file=None,
        sasl_password=" leading-trailing ",
        ssl_key_password="\tkey secret\n",
    )

    producer_config = config.get_producer_config()

    # When: the producer client config is generated.
    # Then: secret field boundary whitespace is preserved exactly.
    assert producer_config["sasl.password"] == " leading-trailing "
    assert producer_config["ssl.key.password"] == "\tkey secret\n"


def test_kafka_config_keeps_legacy_uppercase_aliases() -> None:
    # Given: KafkaConfig is built and mutated through legacy uppercase aliases.
    config = KafkaConfig(
        _env_file=None,
        BOOTSTRAP_SERVERS=["alias-broker:9092"],
        CONSUMER_GROUP="alias-group",
        DLQ_TOPIC_SUFFIX=".alias",
        DLQ_FLUSH_TIMEOUT_MS=4321,
        AUTO_OFFSET_RESET="none",
        ENABLE_AUTO_COMMIT=True,
        SESSION_TIMEOUT_MS=8765,
    )

    # When: canonical snake_case fields are read after alias construction and mutation.
    # Then: legacy aliases continue to mirror the canonical fields.
    assert config.bootstrap_servers == ["alias-broker:9092"]
    assert config.consumer_group == "alias-group"
    assert config.dlq_topic_suffix == ".alias"
    assert config.dlq_flush_timeout_ms == 4321
    assert config.auto_offset_reset == "none"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 8765

    config.BOOTSTRAP_SERVERS = ["mutated-broker:9092"]
    config.CONSUMER_GROUP = "mutated-group"
    config.DLQ_TOPIC_SUFFIX = ".mutated"
    config.DLQ_FLUSH_TIMEOUT_MS = 2468
    config.AUTO_OFFSET_RESET = "earliest"
    config.ENABLE_AUTO_COMMIT = False
    config.SESSION_TIMEOUT_MS = 1357

    assert config.bootstrap_servers == ["mutated-broker:9092"]
    assert config.consumer_group == "mutated-group"
    assert config.dlq_topic_suffix == ".mutated"
    assert config.dlq_flush_timeout_ms == 2468
    assert config.auto_offset_reset == "earliest"
    assert config.enable_auto_commit is False
    assert config.session_timeout_ms == 1357


def test_kafka_config_env_vars_populate_canonical_snake_case_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: canonical Kafka environment variables are set to concrete values.
    monkeypatch.setenv("KAFKA_BOOTSTRAP_SERVERS", "env-a:9092,env-b:9092")
    monkeypatch.setenv("KAFKA_CONSUMER_GROUP", "env-group")
    monkeypatch.setenv("KAFKA_DLQ_TOPIC_SUFFIX", ".env")
    monkeypatch.setenv("KAFKA_DLQ_FLUSH_TIMEOUT_MS", "999")
    monkeypatch.setenv("KAFKA_AUTO_OFFSET_RESET", "latest")
    monkeypatch.setenv("KAFKA_ENABLE_AUTO_COMMIT", "true")
    monkeypatch.setenv("KAFKA_SESSION_TIMEOUT_MS", "2222")

    config = KafkaConfig(_env_file=None)

    # When: KafkaConfig loads canonical fields from the environment.
    # Then: snake_case Kafka fields reflect the environment values.
    assert config.bootstrap_servers == ["env-a:9092", "env-b:9092"]
    assert config.consumer_group == "env-group"
    assert config.dlq_topic_suffix == ".env"
    assert config.dlq_flush_timeout_ms == 999
    assert config.auto_offset_reset == "latest"
    assert config.enable_auto_commit is True
    assert config.session_timeout_ms == 2222

    monkeypatch.delenv("KAFKA_BOOTSTRAP_SERVERS", raising=False)
    monkeypatch.delenv("KAFKA_CONSUMER_GROUP", raising=False)
    monkeypatch.delenv("KAFKA_DLQ_TOPIC_SUFFIX", raising=False)
    monkeypatch.delenv("KAFKA_DLQ_FLUSH_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("KAFKA_AUTO_OFFSET_RESET", raising=False)
    monkeypatch.delenv("KAFKA_ENABLE_AUTO_COMMIT", raising=False)
    monkeypatch.delenv("KAFKA_SESSION_TIMEOUT_MS", raising=False)


def test_kafka_config_security_env_vars_populate_allowlisted_fields(
    monkeypatch: MonkeyPatch,
) -> None:
    # Given: Kafka security environment variables are set to concrete credential and file values.
    monkeypatch.setenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL")
    monkeypatch.setenv("KAFKA_SASL_MECHANISMS", "SCRAM-SHA-512")
    monkeypatch.setenv("KAFKA_SASL_USERNAME", "env-user")
    monkeypatch.setenv("KAFKA_SASL_PASSWORD", "env-secret")
    monkeypatch.setenv("KAFKA_SSL_CA_LOCATION", "/env-ca.pem")
    monkeypatch.setenv("KAFKA_SSL_CERTIFICATE_LOCATION", "/env-cert.pem")
    monkeypatch.setenv("KAFKA_SSL_KEY_LOCATION", "/env-key.pem")
    monkeypatch.setenv("KAFKA_SSL_KEY_PASSWORD", "env-key-secret")

    config = KafkaConfig(_env_file=None)

    # When: KafkaConfig loads allowlisted security fields from the environment.
    # Then: security fields and secrets reflect the environment values.
    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanisms == "SCRAM-SHA-512"
    assert config.sasl_username == "env-user"
    assert config.sasl_password is not None
    assert config.sasl_password.get_secret_value() == "env-secret"
    assert config.ssl_ca_location == "/env-ca.pem"
    assert config.ssl_certificate_location == "/env-cert.pem"
    assert config.ssl_key_location == "/env-key.pem"
    assert config.ssl_key_password is not None
    assert config.ssl_key_password.get_secret_value() == "env-key-secret"

    monkeypatch.delenv("KAFKA_SECURITY_PROTOCOL", raising=False)
    monkeypatch.delenv("KAFKA_SASL_MECHANISMS", raising=False)
    monkeypatch.delenv("KAFKA_SASL_USERNAME", raising=False)
    monkeypatch.delenv("KAFKA_SASL_PASSWORD", raising=False)
    monkeypatch.delenv("KAFKA_SSL_CA_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_CERTIFICATE_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_KEY_LOCATION", raising=False)
    monkeypatch.delenv("KAFKA_SSL_KEY_PASSWORD", raising=False)
