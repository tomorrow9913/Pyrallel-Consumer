# Public Contract Freeze (v1)

This document freezes the external public contract of `Pyrallel Consumer` as v1 before stable promotion.

The scope of this document matches the P0-B items locked in [MQU-27](/MQU/issues/MQU-27).

## 1) Freeze Scope

- rebalance state surface
- DLQ payload shape
- commit/offset external contract

## 2) Canonical v1 Contract

| Surface | Frozen v1 default | Allowed values (v1) | Compatibility rule |
| --- | --- | --- | --- |
| Ordering guidance (`ParallelConsumerConfig.ordering_mode`) | `key_hash` | `key_hash`, `partition`, `unordered` | Removing or renaming existing values is a breaking change |
| Rebalance state strategy (`ParallelConsumerConfig.rebalance_state_strategy`) | `contiguous_only` | `contiguous_only`, `metadata_snapshot` | The fail-closed guarantee of `contiguous_only` must be preserved |
| DLQ payload mode (`KafkaConfig.dlq_payload_mode`) | `full` | `full`, `metadata_only` | Changing the default is breaking; `metadata_only` must remain opt-in |
| Offset commit model (`KafkaConfig.ENABLE_AUTO_COMMIT`) | `False` | `False` (operations default) | Enabling auto-commit by default is breaking |
| Commit public surface | completion-driven (`on_complete`) only | no explicit public knob for periodic commit | Adding a periodic commit option to facade/config changes the contract |

Operational rules:

- Failed messages are committed only after successful DLQ publish (preserve the existing at-least-once boundary).
- `metadata_snapshot` is an optional recovery optimization and must safely degrade to `contiguous_only` semantics on failure.

## 3) Contract Regression Tests

The v1 frozen contract is guarded by the following regression tests.

- `tests/unit/test_public_contract_v1.py`
- `tests/unit/control_plane/test_broker_poller_dlq.py`
- `tests/unit/control_plane/test_broker_support.py`

## 4) Allowed Change Policy (major/minor/patch)

### Major (breaking)

- Change to frozen defaults (`key_hash`, `contiguous_only`, `full`, `ENABLE_AUTO_COMMIT=False`)
- Removal or rename of existing public enum/value
- Breaking completion-driven commit semantics
- Adding incompatible required arguments to facade constructors or public config surfaces

### Minor (backward-compatible)

- Add opt-in functionality while preserving defaults
- Add metrics/docs/guides that do not break existing behavior
- Add optional config proven to preserve compatibility

### Patch (non-breaking)

- Internal refactoring/performance improvements/bug fixes
- Changes that do not affect public defaults, allowed values, or commit semantics

## 5) Exception Handling

If a change outside the frozen contract is required, submit all of the following together.

- design rationale (why it is breaking/minor)
- regression test updates
- release notes/operations docs updates
