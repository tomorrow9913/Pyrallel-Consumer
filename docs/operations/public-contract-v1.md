# Public Contract Freeze (v1)

This document freezes the external public contract of `Pyrallel Consumer` as the v1 stable baseline.

The scope of this document matches the P0-B items locked in [MQU-27](/MQU/issues/MQU-27).

## 1) Freeze Scope

- rebalance state surface
- DLQ payload shape
- commit/offset external contract

## 2) Canonical v1 Contract

| Surface | Frozen v1 default | Allowed values (v1) | Compatibility rule |
| --- | --- | --- | --- |
| Python config naming (`KafkaConfig.bootstrap_servers`, `consumer_group`, `dlq_topic_suffix`, `auto_offset_reset`, `enable_auto_commit`, `session_timeout_ms`) | lowercase snake_case | lowercase snake_case is canonical; legacy uppercase attributes remain compatibility aliases | Renaming/removing lowercase canonical attributes is breaking; keeping uppercase aliases is backward-compatible |
| Shutdown controls (`ExecutionConfig.shutdown_policy`, `consumer_task_stop_timeout_ms`, `shutdown_drain_timeout_ms`) | `graceful`, `5000`, `5000` | `shutdown_policy`: `graceful`, `abort`; timeouts are non-negative integers in milliseconds | Changing the default policy or removing the explicit timeout knobs is breaking |
| Ordering guidance (`ParallelConsumerConfig.ordering_mode`) | `key_hash` | `key_hash`, `partition`, `unordered` | Removing or renaming existing values is a breaking change |
| Rebalance state strategy (`ParallelConsumerConfig.rebalance_state_strategy`) | `contiguous_only` | `contiguous_only`, `metadata_snapshot` | The fail-closed guarantee of `contiguous_only` must be preserved |
| DLQ payload mode (`KafkaConfig.dlq_payload_mode`) | `full` | `full`, `metadata_only` | Changing the default is breaking; `metadata_only` must remain opt-in |
| Offset commit model (`KafkaConfig.enable_auto_commit`) | `False` | `False` (operations default) | Enabling auto-commit by default is breaking |
| Secure Kafka connection fields (`KafkaConfig.security_protocol`, `sasl_mechanisms`, `sasl_username`, `sasl_password`, `ssl_ca_location`, `ssl_certificate_location`, `ssl_key_location`, `ssl_key_password`) | unset | optional allowlisted librdkafka TLS/SASL keys only | Adding optional allowlisted keys is minor; generic passthrough or logging/snapshotting secrets is not allowed |
| Commit public surface | completion-driven (`on_complete`) only | no explicit public knob for periodic commit | Adding a periodic commit option to facade/config changes the contract |
| Runtime diagnostics facade (`PyrallelConsumer.get_runtime_snapshot()`) | read-only structured snapshot | queue summary, retry policy, DLQ status, per-partition assignment/runtime state | Additive read-only fields are backward-compatible; renaming/removing existing snapshot fields is breaking once documented |

Operational rules:

- Failed messages are committed only after successful DLQ publish (preserve the existing at-least-once boundary).
- `metadata_snapshot` is an optional recovery optimization and must safely degrade to `contiguous_only` semantics on failure.
- Secure Kafka config values must be forwarded through the supported Kafka client builders without exposing secrets through logs, metrics labels, runtime snapshots, or support artifacts.
- `shutdown_policy=graceful` must stop new fetches first, then perform a bounded drain of queued/in-flight work before the forced-abort engine shutdown path takes over.

### Runtime diagnostics field boundary

`PyrallelConsumer.get_runtime_snapshot()` returns a read-only `RuntimeSnapshot`
projection. The documented v1 field names below are stable unless a future major
release explicitly breaks the contract.

- `queue.total_in_flight`, `queue.total_queued`, `queue.max_in_flight`,
  `queue.configured_max_in_flight`, `queue.is_paused`,
  `queue.is_rebalancing`, `queue.ordering_mode`
- `retry.max_retries`, `retry.retry_backoff_ms`,
  `retry.exponential_backoff`, `retry.max_retry_backoff_ms`,
  `retry.retry_jitter_ms`
- `dlq.enabled`, `dlq.topic`, `dlq.payload_mode`,
  `dlq.message_cache_size_bytes`, `dlq.message_cache_entry_count`
- `poison_message.enabled`, `poison_message.failure_threshold`,
  `poison_message.cooldown_ms`, `poison_message.open_circuit_count`
- `adaptive_concurrency.configured_max_in_flight`,
  `adaptive_concurrency.effective_max_in_flight`,
  `adaptive_concurrency.min_in_flight`,
  `adaptive_concurrency.scale_up_step`,
  `adaptive_concurrency.scale_down_step`,
  `adaptive_concurrency.cooldown_ms`
- `adaptive_backpressure.configured_max_in_flight`,
  `adaptive_backpressure.effective_max_in_flight`,
  `adaptive_backpressure.min_in_flight`,
  `adaptive_backpressure.scale_up_step`,
  `adaptive_backpressure.scale_down_step`,
  `adaptive_backpressure.cooldown_ms`,
  `adaptive_backpressure.lag_scale_up_threshold`,
  `adaptive_backpressure.low_latency_threshold_ms`,
  `adaptive_backpressure.high_latency_threshold_ms`,
  `adaptive_backpressure.last_decision`,
  `adaptive_backpressure.avg_completion_latency_seconds`
- `partitions[].tp`, `partitions[].current_epoch`,
  `partitions[].last_committed_offset`, `partitions[].last_fetched_offset`,
  `partitions[].true_lag`, `partitions[].gaps`,
  `partitions[].blocking_offset`, `partitions[].blocking_duration_sec`,
  `partitions[].queued_count`, `partitions[].in_flight_count`,
  `partitions[].min_in_flight_offset`

The retry section is a policy snapshot and does not expose per-message retry attempts
or a retry scheduler ledger.

The DLQ section is a configuration/cache snapshot and does not expose a DLQ publish ledger
or historical DLQ message log.

The poison-message section is a policy/runtime counter snapshot and does not expose
per-key identities, payload data, or a historical poison-message ledger.

The adaptive sections are opt-in policy/runtime snapshots. They report the configured
ceiling, the current control-plane live limit, and tuning guardrails; they do not
expose process counts, CPU pressure, async semaphores, or engine-private queues.

## 3) Contract Regression Tests

The v1 frozen contract is guarded by the following regression tests.

- `tests/unit/test_public_contract_v1.py`
- `tests/unit/test_consumer.py`
- `tests/unit/control_plane/test_broker_poller_dlq.py`
- `tests/unit/control_plane/test_broker_poller_metrics.py`
- `tests/unit/control_plane/test_broker_runtime_support.py`

## 4) Allowed Change Policy (major/minor/patch)

This taxonomy is the normative basis for stable/hotfix `x.y.z` classification in
`docs/operations/release-versioning-policy.md`.

### Major (breaking)

- Change to frozen defaults (`key_hash`, `contiguous_only`, `full`, `enable_auto_commit=False`)
- Removal or rename of existing public enum/value
- Breaking completion-driven commit semantics
- Adding incompatible required arguments to facade constructors or public config surfaces

### Minor (backward-compatible)

- Add opt-in functionality while preserving defaults
- Add metrics/docs/guides that do not break existing behavior
- Add read-only diagnostics snapshot fields or projections while preserving existing field names and semantics
- Add optional config proven to preserve compatibility

### Patch (non-breaking)

- Internal refactoring/performance improvements/bug fixes
- Changes that do not affect public defaults, allowed values, or commit semantics

## 5) Exception Handling

If a change outside the frozen contract is required, submit all of the following together.

- design rationale (why it is breaking/minor)
- regression test updates
- release notes/operations docs updates
