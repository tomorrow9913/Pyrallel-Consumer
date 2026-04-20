from dataclasses import fields
from pathlib import Path

from pyrallel_consumer.dto import (
    AdaptiveBackpressureSnapshot,
    AdaptiveConcurrencyRuntimeSnapshot,
    DlqRuntimeSnapshot,
    PartitionRuntimeSnapshot,
    PoisonMessageRuntimeSnapshot,
    QueueRuntimeSnapshot,
    RetryPolicySnapshot,
    RuntimeSnapshot,
)

ROOT = Path(__file__).resolve().parents[2]
PUBLIC_CONTRACT_DOC = ROOT / "docs" / "operations" / "public-contract-v1.md"


def test_public_contract_doc_lists_runtime_snapshot_regression_tests() -> None:
    document = PUBLIC_CONTRACT_DOC.read_text(encoding="utf-8")

    expected_tests = [
        "tests/unit/test_public_contract_v1.py",
        "tests/unit/test_consumer.py",
        "tests/unit/control_plane/test_broker_poller_metrics.py",
        "tests/unit/control_plane/test_broker_runtime_support.py",
    ]

    for expected_test in expected_tests:
        assert expected_test in document
        assert (ROOT / expected_test).exists()


def test_public_contract_doc_freezes_runtime_snapshot_field_boundary() -> None:
    document = PUBLIC_CONTRACT_DOC.read_text(encoding="utf-8")

    expected_terms = [
        "Runtime diagnostics field boundary",
        "queue.total_in_flight",
        "queue.configured_max_in_flight",
        "retry.max_retries",
        "dlq.message_cache_entry_count",
        "partitions[].blocking_duration_sec",
        "The retry section is a policy snapshot",
        "does not expose per-message retry attempts",
        "The DLQ section is a configuration/cache snapshot",
        "does not expose a DLQ publish ledger",
        "poison_message.enabled",
        "poison_message.open_circuit_count",
        "adaptive_concurrency.configured_max_in_flight",
        "adaptive_concurrency.effective_max_in_flight",
        "adaptive_backpressure.last_decision",
    ]

    for expected_term in expected_terms:
        assert expected_term in document


def test_runtime_snapshot_public_field_names_remain_stable() -> None:
    assert [field.name for field in fields(RuntimeSnapshot)] == [
        "queue",
        "retry",
        "dlq",
        "partitions",
        "adaptive_backpressure",
        "adaptive_concurrency",
        "process_batch_metrics",
        "poison_message",
    ]
    assert [field.name for field in fields(QueueRuntimeSnapshot)] == [
        "total_in_flight",
        "total_queued",
        "max_in_flight",
        "is_paused",
        "is_rebalancing",
        "ordering_mode",
        "configured_max_in_flight",
    ]
    assert [field.name for field in fields(RetryPolicySnapshot)] == [
        "max_retries",
        "retry_backoff_ms",
        "exponential_backoff",
        "max_retry_backoff_ms",
        "retry_jitter_ms",
    ]
    assert [field.name for field in fields(DlqRuntimeSnapshot)] == [
        "enabled",
        "topic",
        "payload_mode",
        "message_cache_size_bytes",
        "message_cache_entry_count",
    ]
    assert [field.name for field in fields(AdaptiveConcurrencyRuntimeSnapshot)] == [
        "configured_max_in_flight",
        "effective_max_in_flight",
        "min_in_flight",
        "scale_up_step",
        "scale_down_step",
        "cooldown_ms",
    ]
    assert [field.name for field in fields(AdaptiveBackpressureSnapshot)] == [
        "configured_max_in_flight",
        "effective_max_in_flight",
        "min_in_flight",
        "scale_up_step",
        "scale_down_step",
        "cooldown_ms",
        "lag_scale_up_threshold",
        "low_latency_threshold_ms",
        "high_latency_threshold_ms",
        "last_decision",
        "avg_completion_latency_seconds",
    ]
    assert [field.name for field in fields(PoisonMessageRuntimeSnapshot)] == [
        "enabled",
        "failure_threshold",
        "cooldown_ms",
        "open_circuit_count",
    ]
    assert [field.name for field in fields(PartitionRuntimeSnapshot)] == [
        "tp",
        "current_epoch",
        "last_committed_offset",
        "last_fetched_offset",
        "true_lag",
        "gaps",
        "blocking_offset",
        "blocking_duration_sec",
        "queued_count",
        "in_flight_count",
        "min_in_flight_offset",
    ]
