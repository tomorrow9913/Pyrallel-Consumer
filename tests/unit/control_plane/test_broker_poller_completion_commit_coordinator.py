# -*- coding: utf-8 -*-
# File: tests/unit/control_plane/test_broker_poller_completion_commit_coordinator.py
# Role: Verifies completion-driven commit coordinator integration, fallback, leases, and diagnostics.
# Extend here for focused control-plane regression coverage in this area.

from tests.unit.control_plane._broker_poller_completion_driven_support import (
    AsyncMock,
    CommitBatchAborted,
    CommitCandidate,
    CommitCoordinator,
    CommitCoordinatorConfig,
    CommitSettlement,
    CompletionEvent,
    CompletionProcessingResult,
    CompletionStatus,
    Consumer,
    MagicMock,
    _make_tracker,
    asyncio,
    pytest,
    time,
)


@pytest.mark.asyncio
async def test_commit_ready_offsets_serializes_commit_calls_and_releases_control_lock(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for commit ready offsets serializes commit calls and releases control lock.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    active_commits = 0
    max_active_commits = 0

    async def fake_commit_offsets(commits_to_make):
        nonlocal active_commits, max_active_commits
        assert commits_to_make == [(topic_partition, 0)]
        assert not broker_poller._control_lock.locked()
        active_commits += 1
        max_active_commits = max(max_active_commits, active_commits)
        await asyncio.sleep(0)
        active_commits -= 1

    broker_poller._commit_offsets = AsyncMock(side_effect=fake_commit_offsets)

    # When: The control-plane behavior is exercised for commit ready offsets serializes commit calls and releases control lock.
    await asyncio.gather(
        broker_poller._commit_ready_offsets(force=True),
        broker_poller._commit_ready_offsets(force=True),
    )

    # Then: The expected commit ready offsets serializes commit calls and releases control lock behavior is asserted.
    assert broker_poller._commit_offsets.await_count == 2
    assert max_active_commits == 1


@pytest.mark.asyncio
async def test_commit_ready_offsets_enqueues_when_commit_coordinator_enabled(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for commit ready offsets enqueues when commit coordinator enabled.
    broker_poller._kafka_config.parallel_consumer.commit_coordinator.enabled = True
    broker_poller._commit_coordinator_enabled = True
    enqueue = AsyncMock(return_value=True)
    broker_poller._commit_coordinator = MagicMock(enqueue=enqueue)
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 3
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)

    broker_poller._make_dispatch_support = MagicMock()
    broker_poller._make_dispatch_support.return_value.build_commit_candidates.return_value = [
        (topic_partition, 4)
    ]

    await broker_poller._commit_ready_offsets(force=True, source="test")

    # When: The control-plane behavior is exercised for commit ready offsets enqueues when commit coordinator enabled.
    enqueue.assert_awaited_once()
    # Then: The expected commit ready offsets enqueues when commit coordinator enabled behavior is asserted.
    assert enqueue.await_args is not None
    candidates = enqueue.await_args.args[0]
    assert len(candidates) == 1
    assert candidates[0].tp == topic_partition
    assert candidates[0].safe_offset == 4
    assert candidates[0].assignment_epoch == 3
    broker_poller.consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_commit_ready_offsets_falls_back_to_sync_commit_when_coordinator_rejects(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for commit ready offsets falls back to sync commit when coordinator rejects.
    broker_poller._kafka_config.parallel_consumer.commit_coordinator.enabled = True
    broker_poller._commit_coordinator_enabled = True
    enqueue = AsyncMock(return_value=False)
    broker_poller._commit_coordinator = MagicMock(enqueue=enqueue)
    broker_poller._commit_offsets = AsyncMock(return_value=True)
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 3
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)

    broker_poller._make_dispatch_support = MagicMock()
    broker_poller._make_dispatch_support.return_value.build_commit_candidates.return_value = [
        (topic_partition, 4)
    ]

    await broker_poller._commit_ready_offsets(force=True, source="test")

    enqueue.assert_awaited_once()
    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 4)])
    # When: The control-plane behavior is exercised for commit ready offsets falls back to sync commit when coordinator rejects.
    stats = broker_poller.get_commit_cadence_stats()
    # Then: The expected commit ready offsets falls back to sync commit when coordinator rejects behavior is asserted.
    assert stats["commit_calls_total"] == 1
    assert stats["partitions_advanced_total"] == 1


@pytest.mark.asyncio
async def test_commit_ready_offsets_fences_coordinator_leases_before_sync_fallback(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for commit ready offsets fences coordinator leases before sync fallback.
    broker_poller._kafka_config.parallel_consumer.commit_coordinator.enabled = True
    broker_poller._commit_coordinator_enabled = True
    call_order: list[str] = []

    async def enqueue_rejects(candidates, *, force=False, source="unknown"):
        del candidates, force, source
        return False

    coordinator = MagicMock()
    coordinator.enqueue = AsyncMock(side_effect=enqueue_rejects)
    coordinator.cancel_leases.side_effect = lambda tps: call_order.append(
        f"cancel:{list(tps)}"
    )
    broker_poller._commit_coordinator = coordinator

    async def sync_fallback(commits_to_make):
        call_order.append(f"fallback:{commits_to_make}")
        return True

    broker_poller._commit_offsets = AsyncMock(side_effect=sync_fallback)
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 3
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)

    broker_poller._make_dispatch_support = MagicMock()
    broker_poller._make_dispatch_support.return_value.build_commit_candidates.return_value = [
        (topic_partition, 4)
    ]

    await broker_poller._commit_ready_offsets(force=True, source="test")

    coordinator.cancel_leases.assert_called_once_with([topic_partition])
    # When: The control-plane behavior is exercised for commit ready offsets fences coordinator leases before sync fallback.
    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 4)])
    # Then: The expected commit ready offsets fences coordinator leases before sync fallback behavior is asserted.
    assert call_order == [
        f"cancel:{[topic_partition]}",
        f"fallback:{[(topic_partition, 4)]}",
    ]


@pytest.mark.asyncio
async def test_commit_ready_offsets_updates_runtime_coordinator_pending_gauge(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for commit ready offsets updates runtime coordinator pending gauge.
    broker_poller._kafka_config.parallel_consumer.commit_coordinator.enabled = True
    broker_poller._commit_coordinator_enabled = True
    enqueue = AsyncMock(return_value=True)
    coordinator = MagicMock(enqueue=enqueue)
    coordinator.stats.queue_depth = 1
    broker_poller._commit_coordinator = coordinator
    metrics_exporter = MagicMock()
    broker_poller._metrics_exporter = metrics_exporter
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 3
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)

    broker_poller._make_dispatch_support = MagicMock()
    broker_poller._make_dispatch_support.return_value.build_commit_candidates.return_value = [
        (topic_partition, 4)
    ]

    # When: The control-plane behavior is exercised for commit ready offsets updates runtime coordinator pending gauge.
    await broker_poller._commit_ready_offsets(force=True, source="test")

    # Then: The expected commit ready offsets updates runtime coordinator pending gauge behavior is asserted.
    metrics_exporter.set_commit_coordinator_pending_partitions.assert_called_with(
        "async",
        1,
    )


@pytest.mark.asyncio
async def test_settle_committed_offsets_ignores_stale_epoch(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for settle committed offsets ignores stale epoch.
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 2
    broker_poller._offset_trackers[topic_partition] = tracker
    broker_poller._dirty_commit_partitions.add(topic_partition)
    broker_poller._commit_coordinator = MagicMock()
    broker_poller._commit_coordinator.is_active_lease.return_value = True

    await broker_poller._settle_committed_offsets(
        [
            CommitSettlement(
                tp=topic_partition,
                safe_offset=5,
                assignment_epoch=1,
                lease_id=99,
                success=True,
                reason=None,
                latency_seconds=0.01,
            )
        ]
    )

    # When: The control-plane behavior is exercised for settle committed offsets ignores stale epoch.
    tracker.commit_through.assert_not_called()
    # Then: The expected settle committed offsets ignores stale epoch behavior is asserted.
    assert topic_partition in broker_poller._dirty_commit_partitions


@pytest.mark.asyncio
async def test_commit_coordinator_sync_revalidates_lease_before_guarded_commit(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for commit coordinator sync revalidates lease before guarded commit.
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 3
    broker_poller._offset_trackers[topic_partition] = tracker
    coordinator = MagicMock()
    coordinator.is_active_lease.return_value = True
    broker_poller._commit_coordinator = coordinator

    def revoke_before_commit(operation):
        coordinator.is_active_lease.return_value = False
        return operation()

    broker_poller._consumer_operation_guard.run = MagicMock(
        side_effect=revoke_before_commit
    )

    # When: The control-plane behavior is exercised for commit coordinator sync revalidates lease before guarded commit.
    candidate = CommitCandidate(
        tp=topic_partition,
        safe_offset=4,
        assignment_epoch=3,
        lease_id=9,
        enqueued_at=0.0,
    )

    # Then: The expected commit coordinator sync revalidates lease before guarded commit behavior is asserted.
    with pytest.raises(CommitBatchAborted):
        await broker_poller._commit_coordinator_sync([candidate])

    broker_poller.consumer.commit.assert_not_called()
    coordinator.cancel_leases.assert_called_once_with([topic_partition])


@pytest.mark.asyncio
async def test_stale_epoch_coordinator_candidate_does_not_settle_success(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for stale epoch coordinator candidate does not settle success.
    tracker = _make_tracker(topic_partition)
    tracker.last_committed_offset = -1
    tracker.get_current_epoch.return_value = 4
    broker_poller._offset_trackers[topic_partition] = tracker
    settlements_seen: list[CommitSettlement] = []

    coordinator = CommitCoordinator(
        config=CommitCoordinatorConfig(),
        commit_sync=broker_poller._commit_coordinator_sync,
        on_commit_success=settlements_seen.extend,
        on_commit_failure=lambda settlements, reason: None,
        record_metrics=lambda event, reason, count, latency: None,
    )
    broker_poller._commit_coordinator = coordinator

    # When: The control-plane behavior is exercised for stale epoch coordinator candidate does not settle success.
    await coordinator.enqueue(
        [
            CommitCandidate(
                tp=topic_partition,
                safe_offset=4,
                assignment_epoch=3,
                lease_id=0,
                enqueued_at=0.0,
            )
        ]
    )
    # Then: The expected stale epoch coordinator candidate does not settle success behavior is asserted.
    assert await coordinator.drain(timeout=1.0) is True

    broker_poller.consumer.commit.assert_not_called()
    assert settlements_seen == []
    assert coordinator.latest_settled_offsets == {}


@pytest.mark.asyncio
async def test_transient_coordinator_retry_retains_dirty_without_final_failure_metric(
    broker_poller, topic_partition
) -> None:
    # Given: Inputs and test doubles are prepared for transient coordinator retry retains dirty without final failure metric.
    coordinator = MagicMock()
    coordinator.is_active_lease.return_value = True
    coordinator.stats.queue_depth = 1
    broker_poller._commit_coordinator = coordinator
    broker_poller._record_commit_failure = MagicMock()

    # When: The control-plane behavior is exercised for transient coordinator retry retains dirty without final failure metric.
    await broker_poller._retain_failed_commit_offsets(
        [
            CommitSettlement(
                tp=topic_partition,
                safe_offset=4,
                assignment_epoch=3,
                lease_id=9,
                success=False,
                reason="kafka_exception",
                latency_seconds=0.01,
            )
        ],
        "kafka_exception",
    )

    # Then: The expected transient coordinator retry retains dirty without final failure metric behavior is asserted.
    assert topic_partition in broker_poller._dirty_commit_partitions
    broker_poller._record_commit_failure.assert_not_called()


@pytest.mark.asyncio
async def test_commit_ready_offsets_tolerates_tracker_removed_after_candidate_generation(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for commit ready offsets tolerates tracker removed after candidate generation.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller.consumer = MagicMock(spec=Consumer)
    dispatch_support = MagicMock()

    def build_commit_candidates():
        broker_poller._offset_trackers.pop(topic_partition, None)
        return [(topic_partition, 0)]

    dispatch_support.build_commit_candidates.side_effect = build_commit_candidates
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    # When: The control-plane behavior is exercised for commit ready offsets tolerates tracker removed after candidate generation.
    await broker_poller._commit_ready_offsets()

    # Then: The expected commit ready offsets tolerates tracker removed after candidate generation behavior is asserted.
    broker_poller.consumer.commit.assert_not_called()


@pytest.mark.asyncio
async def test_commit_ready_offsets_waits_for_completion_cadence_before_commit(
    broker_poller, topic_partition, completion_event
):
    # Given: Inputs and test doubles are prepared for commit ready offsets waits for completion cadence before commit.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    broker_poller._commit_debounce_completion_threshold = 3
    broker_poller._commit_debounce_interval_seconds = 9999.0
    broker_poller._last_commit_attempt_monotonic = time.monotonic()
    broker_poller._commit_offsets = AsyncMock()
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 2)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)

    completion_support = MagicMock()
    completion_support.process_completed_events = AsyncMock(
        side_effect=[
            CompletionProcessingResult(1, frozenset({topic_partition})),
            CompletionProcessingResult(2, frozenset({topic_partition})),
        ]
    )
    broker_poller._make_completion_support = MagicMock(return_value=completion_support)

    await broker_poller._process_completed_events([completion_event])
    await broker_poller._commit_ready_offsets()

    broker_poller._commit_offsets.assert_not_awaited()

    await broker_poller._process_completed_events(
        [
            CompletionEvent(
                id="work-2",
                tp=topic_partition,
                offset=1,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
            CompletionEvent(
                id="work-3",
                tp=topic_partition,
                offset=2,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
        ]
    )
    # When: The control-plane behavior is exercised for commit ready offsets waits for completion cadence before commit.
    await broker_poller._commit_ready_offsets()

    # Then: The expected commit ready offsets waits for completion cadence before commit behavior is asserted.
    broker_poller._commit_offsets.assert_awaited_once_with([(topic_partition, 2)])


@pytest.mark.asyncio
async def test_commit_ready_offsets_tracks_empty_candidate_scans_by_source(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for commit ready offsets tracks empty candidate scans by source.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = []
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)
    broker_poller._commit_offsets = AsyncMock()

    await broker_poller._commit_ready_offsets(source="completion_monitor")

    # When: The control-plane behavior is exercised for commit ready offsets tracks empty candidate scans by source.
    stats = broker_poller.get_commit_cadence_stats()
    # Then: The expected commit ready offsets tracks empty candidate scans by source behavior is asserted.
    assert stats["invocations_total"] == 1
    assert stats["empty_candidate_scans_total"] == 1
    assert stats["commit_calls_total"] == 0
    assert stats["partitions_advanced_total"] == 0
    assert stats["invocations_by_source"]["completion_monitor"] == 1
    assert stats["empty_candidate_scans_by_source"]["completion_monitor"] == 1
    assert "completion_monitor" not in stats["commit_calls_by_source"]
    assert "completion_monitor" not in stats["partitions_advanced_by_source"]


@pytest.mark.asyncio
async def test_commit_ready_offsets_tracks_commit_calls_by_source(
    broker_poller, topic_partition
):
    # Given: Inputs and test doubles are prepared for commit ready offsets tracks commit calls by source.
    broker_poller._offset_trackers[topic_partition] = _make_tracker(topic_partition)
    dispatch_support = MagicMock()
    dispatch_support.build_commit_candidates.return_value = [(topic_partition, 0)]
    broker_poller._make_dispatch_support = MagicMock(return_value=dispatch_support)
    broker_poller._commit_offsets = AsyncMock(return_value=1)
    broker_poller._dirty_commit_partitions = {topic_partition}

    await broker_poller._commit_ready_offsets(force=True, source="consumer_loop")

    # When: The control-plane behavior is exercised for commit ready offsets tracks commit calls by source.
    stats = broker_poller.get_commit_cadence_stats()
    # Then: The expected commit ready offsets tracks commit calls by source behavior is asserted.
    assert stats["invocations_total"] == 1
    assert stats["empty_candidate_scans_total"] == 0
    assert stats["commit_calls_total"] == 1
    assert stats["partitions_advanced_total"] == 1
    assert stats["invocations_by_source"]["consumer_loop"] == 1
    assert stats["commit_calls_by_source"]["consumer_loop"] == 1
    assert stats["partitions_advanced_by_source"]["consumer_loop"] == 1
