from __future__ import annotations

import asyncio

import pytest
from confluent_kafka import KafkaException

from pyrallel_consumer.control_plane.commit_coordinator import (
    COMMIT_COORDINATOR_FAILURE_REASONS,
    CommitBatchAborted,
    CommitCandidate,
    CommitCoordinator,
    CommitCoordinatorConfig,
    CommitSettlement,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition


def _candidate(
    tp: DtoTopicPartition, safe_offset: int, lease_id: int = 0
) -> CommitCandidate:
    return CommitCandidate(
        tp=tp,
        safe_offset=safe_offset,
        assignment_epoch=1,
        lease_id=lease_id,
        enqueued_at=0.0,
    )


@pytest.mark.asyncio
async def test_enqueue_coalesces_partition_and_supersedes_old_lease() -> None:
    tp = DtoTopicPartition("topic", 0)
    submitted: list[list[CommitCandidate]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        submitted.append(candidates)

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 3)])
    await coordinator.enqueue([_candidate(tp, 5)])

    assert coordinator.stats.coalesced_count == 1
    assert coordinator.remaining_candidates()[tp].safe_offset == 5
    assert not coordinator.is_active_lease(tp, 1, 1)
    assert coordinator.is_active_lease(tp, 1, 2)


@pytest.mark.asyncio
async def test_success_settlement_is_reported_once_and_advances_active_lease() -> None:
    tp = DtoTopicPartition("topic", 0)
    settlements_seen: list[CommitSettlement] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        assert candidates[0].safe_offset == 4

    def on_success(settlements: list[CommitSettlement]) -> None:
        settlements_seen.extend(settlements)

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 4)])
    await coordinator.drain(timeout=1.0)

    assert len(settlements_seen) == 1
    assert settlements_seen[0].tp == tp
    assert settlements_seen[0].safe_offset == 4
    assert settlements_seen[0].success is True
    assert coordinator.remaining_candidates() == {}
    assert coordinator.latest_settled_offsets[tp] == 4


@pytest.mark.asyncio
async def test_success_callback_runs_while_lease_is_still_active() -> None:
    tp = DtoTopicPartition("topic", 0)
    active_during_callback = False

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        assert candidates[0].safe_offset == 9

    def on_success(settlements: list[CommitSettlement]) -> None:
        nonlocal active_during_callback
        settlement = settlements[0]
        active_during_callback = coordinator.is_active_lease(
            settlement.tp,
            settlement.assignment_epoch,
            settlement.lease_id,
        )

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    await coordinator.drain(timeout=1.0)

    assert active_during_callback is True


@pytest.mark.asyncio
async def test_success_metrics_refresh_cleared_pending_depth() -> None:
    tp = DtoTopicPartition("topic", 0)
    pending_depths: list[int] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        assert candidates[0].safe_offset == 9

    def record_metrics(
        event: str, reason: str | None, count: int, latency: float | None
    ) -> None:
        del reason, count, latency
        if event == "success":
            pending_depths.append(coordinator.stats.queue_depth)

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=record_metrics,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    await coordinator.drain(timeout=1.0)

    assert pending_depths[-1] == 0


@pytest.mark.asyncio
async def test_aborted_batch_does_not_report_success_settlement() -> None:
    tp = DtoTopicPartition("topic", 0)
    settlements_seen: list[CommitSettlement] = []
    failures_seen: list[tuple[list[CommitSettlement], str]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        assert candidates[0].safe_offset == 9
        raise CommitBatchAborted("lease superseded before broker commit")

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=settlements_seen.extend,
        on_commit_failure=lambda settlements, reason: failures_seen.append(
            (settlements, reason)
        ),
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    await coordinator.drain(timeout=1.0)

    assert settlements_seen == []
    assert failures_seen[0][1] == "stale_lease"
    assert coordinator.latest_settled_offsets == {}
    assert coordinator.remaining_candidates() == {}


@pytest.mark.asyncio
async def test_success_callback_error_does_not_strand_in_flight_candidate() -> None:
    tp = DtoTopicPartition("topic", 0)

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        assert candidates[0].safe_offset == 9

    def on_success(settlements: list[CommitSettlement]) -> None:
        assert settlements[0].tp == tp
        raise RuntimeError("callback failed")

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])

    assert await coordinator.drain(timeout=1.0) is True
    assert coordinator.remaining_candidates() == {}


@pytest.mark.asyncio
async def test_enqueue_ignores_duplicate_candidate_while_commit_is_in_flight() -> None:
    tp = DtoTopicPartition("topic", 0)
    release_commit = asyncio.Event()
    submitted: list[list[CommitCandidate]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        submitted.append(candidates)
        await release_commit.wait()

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    for _ in range(10):
        if submitted:
            break
        await asyncio.sleep(0)
    assert submitted
    assert coordinator.stats.queue_depth == 1
    in_flight_candidate = submitted[0][0]

    assert await coordinator.enqueue([_candidate(tp, 9)]) is True
    assert coordinator.stats.queue_depth == 1
    assert coordinator.is_active_lease(
        in_flight_candidate.tp,
        in_flight_candidate.assignment_epoch,
        in_flight_candidate.lease_id,
    )

    release_commit.set()
    await coordinator.drain(timeout=1.0)

    assert len(submitted) == 1
    assert coordinator.latest_settled_offsets[tp] == 9


@pytest.mark.asyncio
async def test_newer_pending_candidate_does_not_mask_in_flight_settlement() -> None:
    tp = DtoTopicPartition("topic", 0)
    release_first_commit = asyncio.Event()
    submitted_offsets: list[int] = []
    settled_offsets: list[int] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        submitted_offsets.append(candidates[0].safe_offset)
        if candidates[0].safe_offset == 9:
            await release_first_commit.wait()

    def on_success(settlements: list[CommitSettlement]) -> None:
        settled_offsets.extend(settlement.safe_offset for settlement in settlements)

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    for _ in range(10):
        if submitted_offsets:
            break
        await asyncio.sleep(0)
    assert submitted_offsets == [9]
    in_flight_candidate = coordinator.remaining_candidates()[tp]

    assert await coordinator.enqueue([_candidate(tp, 12)]) is True
    assert coordinator.remaining_candidates()[tp].safe_offset == 12
    assert coordinator.is_active_lease(
        in_flight_candidate.tp,
        in_flight_candidate.assignment_epoch,
        in_flight_candidate.lease_id,
    )

    release_first_commit.set()
    await coordinator.drain(timeout=1.0)

    assert submitted_offsets == [9, 12]
    assert settled_offsets == [9, 12]
    assert coordinator.latest_settled_offsets[tp] == 12


@pytest.mark.asyncio
async def test_stop_accepting_partitions_preserves_in_flight_settlement() -> None:
    tp = DtoTopicPartition("topic", 0)
    release_commit = asyncio.Event()
    commit_started = asyncio.Event()
    settled_offsets: list[int] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        commit_started.set()
        await release_commit.wait()

    def on_success(settlements: list[CommitSettlement]) -> None:
        settled_offsets.extend(settlement.safe_offset for settlement in settlements)

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 9)])
    await asyncio.wait_for(commit_started.wait(), timeout=1.0)
    in_flight_candidate = coordinator.remaining_candidates()[tp]

    coordinator.stop_accepting_partitions([tp])

    assert coordinator.is_active_lease(
        in_flight_candidate.tp,
        in_flight_candidate.assignment_epoch,
        in_flight_candidate.lease_id,
    )
    assert await coordinator.enqueue([_candidate(tp, 12)]) is True
    assert coordinator.remaining_candidates()[tp].safe_offset == 9

    release_commit.set()
    await coordinator.drain(timeout=1.0)

    assert settled_offsets == [9]
    assert coordinator.latest_settled_offsets[tp] == 9

    coordinator.start_accepting_partitions([tp])
    assert await coordinator.enqueue([_candidate(tp, 12)]) is True
    await coordinator.drain(timeout=1.0)

    assert settled_offsets == [9, 12]
    assert coordinator.latest_settled_offsets[tp] == 12


@pytest.mark.asyncio
async def test_kafka_exception_retains_candidate_and_records_retry() -> None:
    tp = DtoTopicPartition("topic", 0)
    events: list[tuple[str, str | None, int]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        raise KafkaException("boom")

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(retry_backoff_ms=10, max_retry_backoff_ms=10),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: events.append(
            (event, reason, count)
        ),
    )

    await coordinator.enqueue([_candidate(tp, 7)])
    await asyncio.sleep(0.02)
    assert tp in coordinator.remaining_candidates()
    assert coordinator.stats.retry_count >= 1
    assert ("retry", "kafka_exception", 1) in events

    coordinator.stop_accepting()
    coordinator.cancel_leases([tp])

    assert coordinator.remaining_candidates() == {}


@pytest.mark.asyncio
async def test_kafka_exception_invokes_failure_callback_for_active_lease() -> None:
    tp = DtoTopicPartition("topic", 0)
    failures_seen: list[tuple[list[CommitSettlement], str]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        raise KafkaException("boom")

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(retry_backoff_ms=50, max_retry_backoff_ms=50),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: failures_seen.append(
            (settlements, reason)
        ),
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 7)])
    await asyncio.sleep(0.01)
    coordinator.stop_accepting()
    coordinator.cancel_leases([tp])

    assert failures_seen
    assert failures_seen[0][1] == "kafka_exception"
    assert failures_seen[0][0][0].tp == tp


@pytest.mark.asyncio
async def test_cancelled_lease_settlement_is_ignored() -> None:
    tp = DtoTopicPartition("topic", 0)
    success_called = False
    release_commit: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        await release_commit

    def on_success(settlements: list[CommitSettlement]) -> None:
        nonlocal success_called
        success_called = True

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=on_success,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 2)])
    coordinator.cancel_leases([tp])
    release_commit.set_result(None)
    await coordinator.drain(timeout=1.0)

    assert success_called is False
    assert coordinator.remaining_candidates() == {}


@pytest.mark.asyncio
async def test_cancelled_lease_marker_is_pruned_after_candidate_removal() -> None:
    tp = DtoTopicPartition("topic", 0)
    release_commit: asyncio.Future[None] = asyncio.get_running_loop().create_future()

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        await release_commit

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )

    await coordinator.enqueue([_candidate(tp, 2)])
    coordinator.cancel_leases([tp])
    release_commit.set_result(None)
    await coordinator.drain(timeout=1.0)

    assert coordinator.remaining_candidates() == {}
    assert coordinator._cancelled_leases == set()


@pytest.mark.asyncio
async def test_worker_crash_marks_unhealthy_and_stops_accepting() -> None:
    tp = DtoTopicPartition("topic", 0)
    events: list[tuple[str, str | None, int]] = []

    async def commit_sync(candidates: list[CommitCandidate]) -> None:
        raise RuntimeError("unexpected")

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=commit_sync,
        on_commit_success=lambda settlements: None,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: events.append(
            (event, reason, count)
        ),
    )

    await coordinator.enqueue([_candidate(tp, 1)])
    await coordinator.drain(timeout=1.0)

    assert coordinator.healthy is False
    assert coordinator.accepting is False
    assert ("failure", "worker_crash", 1) in events


def test_failure_reason_set_is_bounded() -> None:
    assert COMMIT_COORDINATOR_FAILURE_REASONS == (
        "kafka_exception",
        "queue_full",
        "worker_crash",
        "stale_lease",
        "shutdown_timeout",
        "rebalance_bridge_failed",
        "close_commit_failed",
    )
