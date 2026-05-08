# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_commit_cadence_support.py
# Role: Verifies BrokerPoller commit cadence gate and diagnostic counters.
# Extend here when commit cadence behavior moves between BrokerPoller and commit support.

import pytest

from pyrallel_consumer.control_plane.broker_commit_coordinator_support import (
    BrokerCommitCadenceSupport,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def test_commit_cadence_support_tracks_stats_by_source():
    # Given: commit cadence support has no prior diagnostic counters.
    support = BrokerCommitCadenceSupport(
        get_dirty_commit_partitions=set,
        has_pending_dlq_events=lambda: False,
        get_total_in_flight_count=lambda: 0,
        get_total_queued_messages=lambda: _async_int(0),
    )

    # When: invocation, empty scan, and commit success events are recorded.
    support.record_invocation("completion_monitor")
    support.record_empty_candidate_scan("completion_monitor")
    support.record_commit_success("consumer_loop", 2)

    # Then: aggregate and source-specific counters expose the same legacy shape.
    assert support.get_stats() == {
        "invocations_total": 1,
        "empty_candidate_scans_total": 1,
        "commit_calls_total": 1,
        "partitions_advanced_total": 2,
        "invocations_by_source": {"completion_monitor": 1},
        "empty_candidate_scans_by_source": {"completion_monitor": 1},
        "commit_calls_by_source": {"consumer_loop": 1},
        "partitions_advanced_by_source": {"consumer_loop": 2},
    }


def test_commit_cadence_support_respects_threshold_and_interval_gates():
    # Given: one dirty partition is waiting for commit settlement.
    dirty_partitions = {DtoTopicPartition("test-topic", 0)}
    support = BrokerCommitCadenceSupport(
        get_dirty_commit_partitions=lambda: dirty_partitions,
        has_pending_dlq_events=lambda: False,
        get_total_in_flight_count=lambda: 0,
        get_total_queued_messages=lambda: _async_int(0),
        now=lambda: 10.0,
    )

    # When: completions are below threshold and the debounce interval has not elapsed.
    blocked = support.should_attempt_ready_commit(
        completions_since_last_commit=1,
        completion_threshold=3,
        interval_seconds=5.0,
        last_attempt_monotonic=8.0,
    )

    # Then: the commit scan is still cadence-gated.
    assert blocked is False

    # When: completion threshold is reached before the interval elapses.
    threshold_ready = support.should_attempt_ready_commit(
        completions_since_last_commit=3,
        completion_threshold=3,
        interval_seconds=5.0,
        last_attempt_monotonic=8.0,
    )

    # Then: the commit scan is allowed immediately.
    assert threshold_ready is True

    # When: the debounce interval has elapsed with fewer completions.
    interval_ready = support.should_attempt_ready_commit(
        completions_since_last_commit=1,
        completion_threshold=3,
        interval_seconds=2.0,
        last_attempt_monotonic=7.0,
    )

    # Then: the commit scan is allowed by time cadence.
    assert interval_ready is True


@pytest.mark.asyncio
async def test_commit_cadence_support_forces_idle_commit_only_when_drained():
    # Given: dirty commit state exists and broker/work queues are drained.
    dirty_partitions = {DtoTopicPartition("test-topic", 0)}
    support = BrokerCommitCadenceSupport(
        get_dirty_commit_partitions=lambda: dirty_partitions,
        has_pending_dlq_events=lambda: False,
        get_total_in_flight_count=lambda: 0,
        get_total_queued_messages=lambda: _async_int(0),
    )

    # When: idle-force gating is evaluated.
    should_force = await support.should_force_idle_commit()

    # Then: a final commit scan is forced for the drained dirty partition.
    assert should_force is True


@pytest.mark.asyncio
async def test_commit_cadence_support_blocks_idle_force_with_pending_dlq():
    # Given: dirty commit state exists but DLQ publication is still pending.
    dirty_partitions = {DtoTopicPartition("test-topic", 0)}
    support = BrokerCommitCadenceSupport(
        get_dirty_commit_partitions=lambda: dirty_partitions,
        has_pending_dlq_events=lambda: True,
        get_total_in_flight_count=lambda: 0,
        get_total_queued_messages=lambda: _async_int(0),
    )

    # When: idle-force gating is evaluated.
    should_force = await support.should_force_idle_commit()

    # Then: commit forcing stays blocked until DLQ publication settles.
    assert should_force is False


async def _async_int(value: int) -> int:
    """Return an integer through an awaitable test helper."""
    return value
