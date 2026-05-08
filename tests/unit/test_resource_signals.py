# -*- coding: utf-8 -*-
# File: tests/unit/test_resource_signals.py
# Role: Verifies resource signal provider fail-open snapshots and actionable tuning states.
# Extend here for resource signal status semantics or tuning actionability changes.

from pyrallel_consumer.dto import ResourceSignalStatus
from pyrallel_consumer.resource_signals import NullResourceSignalProvider


def test_null_resource_signal_provider_returns_fail_open_unavailable_snapshot() -> None:
    # Given: a NullResourceSignalProvider is created.
    provider = NullResourceSignalProvider()

    snapshot = provider.snapshot()

    # When: snapshot is requested from the provider.
    # Then: the snapshot is unavailable, has no utilization values, and is not actionable for tuning.
    assert snapshot.status == ResourceSignalStatus.UNAVAILABLE
    assert snapshot.cpu_utilization is None
    assert snapshot.memory_utilization is None
    assert snapshot.is_actionable_for_tuning is False


def test_resource_signal_non_available_states_are_not_actionable_for_tuning() -> None:
    # Given: the resource signal snapshot type and non-available statuses are prepared.
    provider = NullResourceSignalProvider()
    snapshot_type = type(provider.snapshot())

    # When: each non-available status and an available status are evaluated for tuning actionability.
    # Then: only the available snapshot with utilization values is actionable for tuning.
    for status in (
        ResourceSignalStatus.UNAVAILABLE,
        ResourceSignalStatus.STALE,
        ResourceSignalStatus.FIRST_SAMPLE_PENDING,
    ):
        snapshot = snapshot_type(status=status)
        assert snapshot.is_actionable_for_tuning is False

    assert (
        snapshot_type(
            status=ResourceSignalStatus.AVAILABLE,
            cpu_utilization=0.5,
            memory_utilization=0.75,
        ).is_actionable_for_tuning
        is True
    )
