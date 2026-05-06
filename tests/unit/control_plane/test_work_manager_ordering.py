"""Tests for WorkManager ordering mode behavior."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.control_plane.work_manager import WorkManager
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    OrderingMode,
    PipelineBlockedReason,
    PipelineDiagnosticsScope,
    PipelineDiagnosticsSection,
    PipelineDiagnosticsSupportState,
    PipelineDispatchCapacityReason,
    PipelineStage,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine


@pytest.fixture
def mock_engine():
    return AsyncMock(spec=BaseExecutionEngine)


@pytest.fixture
def tp():
    return DtoTopicPartition(topic="test-topic", partition=0)


def _make_tracker_mock(tp):
    mock = MagicMock(
        spec=OffsetTracker(
            topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
        )
    )
    mock.get_gaps.return_value = []
    mock.advance_high_water_mark.return_value = None
    mock.get_current_epoch.return_value = 1
    return mock


def _setup_wm(engine, tp, ordering_mode, max_in_flight=100, route_batch_size=1):
    wm = WorkManager(
        execution_engine=engine,
        max_in_flight_messages=max_in_flight,
        ordering_mode=ordering_mode,
        route_batch_size=route_batch_size,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker
    return wm, tracker


class _PartiallyFailingBatchEngine(BaseExecutionEngine):
    def __init__(self, fail_offset: int) -> None:
        self.fail_offset = fail_offset
        self.submitted_offsets: list[int] = []

    async def submit(self, work_item) -> None:
        self.submitted_offsets.append(work_item.offset)
        if work_item.offset == self.fail_offset:
            raise RuntimeError("submit failed")

    async def poll_completed_events(self, batch_limit: int = 1000):
        return []

    async def wait_for_completion(self, timeout_seconds=None) -> bool:
        return True

    def get_in_flight_count(self) -> int:
        return 0

    async def shutdown(self) -> None:
        return None


class _OrderedRouteBatchEngine(BaseExecutionEngine):
    def __init__(self) -> None:
        self.submitted_offsets: list[int] = []
        self.submitted_batches: list[list[int]] = []

    @property
    def supports_ordered_route_batch(self) -> bool:
        return True

    async def submit(self, work_item) -> None:
        self.submitted_offsets.append(work_item.offset)

    async def submit_batch(self, work_items) -> None:
        self.submitted_batches.append([item.offset for item in work_items])

    async def poll_completed_events(self, batch_limit: int = 1000):
        return []

    async def wait_for_completion(self, timeout_seconds=None) -> bool:
        return True

    def get_in_flight_count(self) -> int:
        return 0

    async def shutdown(self) -> None:
        return None


class _BlockingSubmitEngine(_OrderedRouteBatchEngine):
    def __init__(self) -> None:
        super().__init__()
        self.submit_started = asyncio.Event()
        self.submit_release = asyncio.Event()

    async def submit(self, work_item) -> None:
        self.submit_started.set()
        await self.submit_release.wait()
        await super().submit(work_item)


class _BlockingBatchSubmitEngine(_OrderedRouteBatchEngine):
    def __init__(self) -> None:
        super().__init__()
        self.submit_batch_started = asyncio.Event()
        self.submit_batch_release = asyncio.Event()

    async def submit_batch(self, work_items) -> None:
        self.submit_batch_started.set()
        await self.submit_batch_release.wait()
        await super().submit_batch(work_items)


class _ScriptedOrderedRouteBatchEngine(_OrderedRouteBatchEngine):
    def __init__(self) -> None:
        super().__init__()
        self.completion_batches: list[list[CompletionEvent]] = []

    async def poll_completed_events(self, batch_limit: int = 1000):
        del batch_limit
        if not self.completion_batches:
            return []
        return self.completion_batches.pop(0)


@pytest.mark.asyncio
async def test_key_hash_route_batching_is_deferred_until_engine_batches_are_ordering_safe(
    mock_engine, tp
):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    mock_engine.submit_batch.assert_not_awaited()
    submitted_item = mock_engine.submit.await_args.args[0]
    assert submitted_item.offset == 0
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_eligible_frontier_and_frontier_deferred(
    tp,
):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=2,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.stage_counts[PipelineStage.QUEUED].count == 3
    assert diagnostics.stage_counts[PipelineStage.DISPATCHED].count == 0
    assert diagnostics.subqueues.total == 1
    assert diagnostics.subqueues.queued == 1
    assert diagnostics.subqueues.queued_items == 3
    assert diagnostics.subqueues.eligible_subqueues == 1
    assert diagnostics.subqueues.eligible_items == 2
    assert diagnostics.subqueues.blocked_subqueues == 1
    assert diagnostics.subqueues.blocked_items == 1
    assert diagnostics.subqueues.top_k_depths == [3]
    assert (
        diagnostics.blocked_counts[PipelineBlockedReason.FRONTIER_DEFERRED].count == 1
    )


@pytest.mark.asyncio
async def test_pipeline_diagnostics_truncates_eligible_prefix_before_mid_prefix_poison(
    tp,
):
    engine = _OrderedRouteBatchEngine()
    circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=30000,
        forced_failure_attempt=1,
        clock=lambda: 100.0,
    )
    circuit.record_completion(
        CompletionEvent(
            id="failed-id",
            tp=tp,
            offset=999,
            epoch=1,
            status=CompletionStatus.FAILURE,
            error="boom",
            attempt=1,
        ),
        WorkItem(
            id="failed-id",
            tp=tp,
            offset=999,
            epoch=1,
            key=b"key-A",
            payload=b"failed-payload",
            poison_key=b"poison-key",
        ),
    )
    wm = WorkManager(
        execution_engine=engine,
        ordering_mode=OrderingMode.KEY_HASH,
        poison_message_circuit=circuit,
        route_batch_size=3,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker

    await wm.submit_message_batch(
        {
            (tp, b"key-A"): [
                (0, 1, b"payload-0", b"good-key"),
                (1, 1, b"payload-1", b"poison-key"),
                (2, 1, b"payload-2", b"good-key-2"),
            ]
        }
    )

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.subqueues.eligible_items == 1
    assert diagnostics.subqueues.blocked_items == 2
    assert diagnostics.blocked_counts[PipelineBlockedReason.POISON_GUARD].count == 2


@pytest.mark.asyncio
async def test_pipeline_diagnostics_does_not_prune_expired_poison_circuit(tp):
    engine = _OrderedRouteBatchEngine()
    now = 100.0

    def clock():
        return now

    circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=30000,
        forced_failure_attempt=1,
        clock=clock,
    )
    failed_item = WorkItem(
        id="failed-id",
        tp=tp,
        offset=999,
        epoch=1,
        key=b"key-A",
        payload=b"failed-payload",
    )
    circuit.record_completion(
        CompletionEvent(
            id="failed-id",
            tp=tp,
            offset=999,
            epoch=1,
            status=CompletionStatus.FAILURE,
            error="boom",
            attempt=1,
        ),
        failed_item,
    )
    wm = WorkManager(
        execution_engine=engine,
        ordering_mode=OrderingMode.KEY_HASH,
        poison_message_circuit=circuit,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")

    now = 131.0
    diagnostics = wm.get_pipeline_diagnostics()
    now = 101.0

    assert diagnostics.subqueues.eligible_items == 1
    assert circuit.should_force_fail(failed_item) is True


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_ordering_lock_separately_from_capacity(
    tp,
):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(engine, tp, OrderingMode.KEY_HASH, max_in_flight=10)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.stage_counts[PipelineStage.QUEUED].count == 1
    assert diagnostics.stage_counts[PipelineStage.DISPATCHED].count == 1
    assert diagnostics.subqueues.eligible_items == 0
    assert diagnostics.subqueues.blocked_items == 1
    assert diagnostics.blocked_counts[PipelineBlockedReason.ORDERING_LOCK].count == 1
    assert diagnostics.dispatch_capacity.blocked_items == 0
    assert diagnostics.dispatch_capacity.reason is None


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_max_in_flight_capacity_after_eligibility(
    tp,
):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(engine, tp, OrderingMode.KEY_HASH, max_in_flight=1)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.stage_counts[PipelineStage.QUEUED].count == 1
    assert diagnostics.stage_counts[PipelineStage.DISPATCHED].count == 1
    assert diagnostics.subqueues.eligible_items == 1
    assert diagnostics.subqueues.blocked_items == 0
    assert diagnostics.dispatch_capacity.blocked_items == 1
    assert (
        diagnostics.dispatch_capacity.reason
        == PipelineDispatchCapacityReason.MAX_IN_FLIGHT
    )


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_poison_guard_as_logical_blocker(tp):
    engine = _OrderedRouteBatchEngine()
    circuit = PoisonMessageCircuitBreaker(
        enabled=True,
        failure_threshold=1,
        cooldown_ms=30000,
        forced_failure_attempt=1,
        clock=lambda: 100.0,
    )
    circuit.record_completion(
        CompletionEvent(
            id="failed-id",
            tp=tp,
            offset=999,
            epoch=1,
            status=CompletionStatus.FAILURE,
            error="boom",
            attempt=1,
        ),
        WorkItem(
            id="failed-id",
            tp=tp,
            offset=999,
            epoch=1,
            key=b"key-A",
            payload=b"failed-payload",
        ),
    )
    wm = WorkManager(
        execution_engine=engine,
        ordering_mode=OrderingMode.KEY_HASH,
        poison_message_circuit=circuit,
    )
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker = _make_tracker_mock(tp)
        MockOT.return_value = tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = tracker

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.subqueues.eligible_items == 0
    assert diagnostics.subqueues.blocked_items == 1
    assert diagnostics.blocked_counts[PipelineBlockedReason.POISON_GUARD].count == 1


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_route_lock_during_submit_lease(tp):
    engine = _BlockingSubmitEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=1,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    schedule_task = asyncio.create_task(wm.schedule())
    await engine.submit_started.wait()

    diagnostics = wm.get_pipeline_diagnostics()

    engine.submit_release.set()
    await schedule_task

    assert diagnostics.subqueues.eligible_items == 0
    assert diagnostics.subqueues.blocked_items == 1
    assert diagnostics.blocked_counts[PipelineBlockedReason.ROUTE_LOCK].count == 1


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_route_lock_during_batch_submit_lease(tp):
    engine = _BlockingBatchSubmitEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=2,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    schedule_task = asyncio.create_task(wm.schedule())
    await engine.submit_batch_started.wait()

    diagnostics = wm.get_pipeline_diagnostics()

    engine.submit_batch_release.set()
    await schedule_task

    assert diagnostics.subqueues.eligible_items == 0
    assert diagnostics.subqueues.blocked_items == 2
    assert diagnostics.blocked_counts[PipelineBlockedReason.ROUTE_LOCK].count == 2


@pytest.mark.asyncio
async def test_pipeline_diagnostics_exposes_support_metadata_for_partial_sidecar(tp):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(engine, tp, OrderingMode.KEY_HASH, max_in_flight=10)

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.scope == PipelineDiagnosticsScope.WORK_MANAGER_ONLY
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.STAGES]
        == PipelineDiagnosticsSupportState.SUPPORTED
    )
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.BLOCKED]
        == PipelineDiagnosticsSupportState.SUPPORTED
    )
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.SUBQUEUES]
        == PipelineDiagnosticsSupportState.SUPPORTED
    )
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.DISPATCH_CAPACITY]
        == PipelineDiagnosticsSupportState.SUPPORTED
    )
    assert (
        diagnostics.section_support[PipelineDiagnosticsSection.ADMISSION]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.stage_support[PipelineStage.QUEUED]
        == PipelineDiagnosticsSupportState.SUPPORTED
    )
    assert (
        diagnostics.stage_support[PipelineStage.EXECUTING]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.stage_support[PipelineStage.COMPLETED_UNSETTLED]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.stage_support[PipelineStage.FAILED]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.stage_support[PipelineStage.DLQ]
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )
    assert (
        diagnostics.admission.support_state
        == PipelineDiagnosticsSupportState.NOT_IMPLEMENTED
    )


@pytest.mark.asyncio
async def test_pipeline_diagnostics_falls_back_to_item_prefix_without_ordered_batch_capability(
    mock_engine,
    tp,
):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.subqueues.eligible_items == 1
    assert diagnostics.subqueues.blocked_items == 1
    assert (
        diagnostics.blocked_counts[PipelineBlockedReason.FRONTIER_DEFERRED].count == 1
    )


@pytest.mark.asyncio
async def test_pipeline_diagnostics_reports_rebalancing_as_logical_blocker(tp):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(engine, tp, OrderingMode.KEY_HASH, max_in_flight=10)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    wm._rebalancing = True

    diagnostics = wm.get_pipeline_diagnostics()

    assert diagnostics.subqueues.eligible_items == 0
    assert diagnostics.subqueues.blocked_items == 1
    assert diagnostics.blocked_counts[PipelineBlockedReason.REBALANCING].count == 1


@pytest.mark.asyncio
async def test_partition_route_batching_is_deferred_without_engine_capability(
    mock_engine, tp
):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.PARTITION,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    mock_engine.submit_batch.assert_not_awaited()
    submitted_item = mock_engine.submit.await_args.args[0]
    assert submitted_item.offset == 0
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_key_hash_supported_engine_batches_to_route_size_and_capacity(tp):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=2,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    assert engine.submitted_offsets == []
    assert engine.submitted_batches == [[0, 1]]
    assert wm.get_total_in_flight_count() == 2
    assert wm.get_total_queued_messages() == 1


@pytest.mark.asyncio
async def test_partition_supported_engine_keeps_item_level_leasing(tp):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.PARTITION,
        max_in_flight=2,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    assert engine.submitted_offsets == [0]
    assert engine.submitted_batches == []
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_partition_route_batch_does_not_skip_lower_offset_on_another_key(tp):
    engine = _OrderedRouteBatchEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.PARTITION,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")

    await wm.schedule()

    assert engine.submitted_offsets == [0]
    assert engine.submitted_batches == []
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_key_hash_route_batch_tail_completion_releases_ordering_lock(tp):
    engine = _ScriptedOrderedRouteBatchEngine()
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=3,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")
    await wm.submit_message(tp, 3, 1, b"key-A", b"payload-3")

    await wm.schedule()
    submitted_batch = list(wm._in_flight_work_items.values())
    by_offset = {item.offset: item for item in submitted_batch}

    engine.completion_batches.append(
        [
            CompletionEvent(
                id=by_offset[0].id,
                tp=tp,
                offset=0,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            ),
            CompletionEvent(
                id=by_offset[1].id,
                tp=tp,
                offset=1,
                epoch=1,
                status=CompletionStatus.FAILURE,
                error="boom",
                attempt=1,
            ),
        ]
    )

    await wm.poll_completed_events()

    assert engine.submitted_batches == [[0, 1, 2]]
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 1

    engine.completion_batches.append(
        [
            CompletionEvent(
                id=by_offset[2].id,
                tp=tp,
                offset=2,
                epoch=1,
                status=CompletionStatus.SUCCESS,
                error=None,
                attempt=1,
            )
        ]
    )

    await wm.poll_completed_events()

    assert engine.submitted_batches == [[0, 1, 2]]
    assert engine.submitted_offsets == [3]
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 0


@pytest.mark.asyncio
async def test_unordered_route_batch_respects_remaining_in_flight_capacity(
    mock_engine, tp
):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.UNORDERED,
        max_in_flight=2,
        route_batch_size=5,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    batch = mock_engine.submit_batch.await_args.args[0]
    assert [item.offset for item in batch] == [0, 1]
    assert wm.get_total_in_flight_count() == 2
    assert wm.get_total_queued_messages() == 1


@pytest.mark.asyncio
async def test_unordered_route_batch_tracks_items_accepted_before_later_submit_failure(
    tp,
):
    engine = _PartiallyFailingBatchEngine(fail_offset=1)
    wm, tracker = _setup_wm(
        engine,
        tp,
        OrderingMode.UNORDERED,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")

    await wm.schedule()

    assert engine.submitted_offsets == [0, 1]
    assert wm.get_total_in_flight_count() == 1
    assert wm.get_total_queued_messages() == 2


@pytest.mark.asyncio
async def test_route_batch_size_one_preserves_item_submit_path(mock_engine, tp):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.KEY_HASH,
        max_in_flight=10,
        route_batch_size=1,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")

    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    mock_engine.submit_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_partition_mode_defers_cross_key_route_batching_for_now(mock_engine, tp):
    wm, tracker = _setup_wm(
        mock_engine,
        tp,
        OrderingMode.PARTITION,
        max_in_flight=10,
        route_batch_size=3,
    )

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")

    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    mock_engine.submit_batch.assert_not_awaited()


@pytest.mark.asyncio
async def test_key_hash_blocks_same_key_concurrent(mock_engine, tp):
    """KEY_HASH: second item with same key must NOT be submitted while first is in-flight."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_key_hash_allows_different_keys_concurrent(mock_engine, tp):
    """KEY_HASH: items with different keys CAN run concurrently."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_key_hash_unblocks_after_completion(mock_engine, tp):
    """KEY_HASH: after completion, the next item for that key is eligible."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    first_item = mock_engine.submit.call_args_list[0].args[0]

    completion = CompletionEvent(
        id=first_item.id,
        tp=tp,
        offset=first_item.offset,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_engine.poll_completed_events.return_value = [completion]
    await wm.poll_completed_events()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_unordered_allows_same_key_concurrent(mock_engine, tp):
    """UNORDERED: same-key items CAN run concurrently."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.UNORDERED)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-A", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_unordered_prefers_lowest_head_offset_after_blocking_pick(
    mock_engine, tp
):
    """UNORDERED: stale runnable entries must not outrank lower current head offsets."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.UNORDERED)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.submit_message(tp, 2, 1, b"key-A", b"payload-2")
    tracker.get_gaps.return_value = [type("Gap", (), {"start": 0})()]
    await wm.schedule()

    submitted_offsets = [
        call.args[0].offset for call in mock_engine.submit.await_args_list
    ]
    assert submitted_offsets == [0, 1, 2]


@pytest.mark.asyncio
async def test_default_ordering_mode_is_unordered(mock_engine):
    """WorkManager default ordering_mode should be UNORDERED for backward compat."""
    wm = WorkManager(execution_engine=mock_engine)
    assert wm._ordering_mode == OrderingMode.UNORDERED


@pytest.mark.asyncio
async def test_key_hash_on_revoke_clears_keys_in_flight(mock_engine, tp):
    """on_revoke must clear keys_in_flight for revoked partitions."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.KEY_HASH)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1

    wm.on_revoke([tp])

    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        new_tracker = _make_tracker_mock(tp)
        MockOT.return_value = new_tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = new_tracker

    mock_engine.submit.reset_mock()
    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_partition_blocks_same_partition_concurrent(mock_engine, tp):
    """PARTITION: second item on same partition must NOT be submitted while first is in-flight."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1


@pytest.mark.asyncio
async def test_partition_allows_different_partitions_concurrent(mock_engine, tp):
    """PARTITION: items on different partitions CAN run concurrently."""
    tp2 = DtoTopicPartition(topic="test-topic", partition=1)
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)
    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        tracker2 = _make_tracker_mock(tp2)
        MockOT.return_value = tracker2
        wm.on_assign([tp2])
        wm._offset_trackers[tp2] = tracker2

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp2, 0, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_partition_unblocks_after_completion(mock_engine, tp):
    """PARTITION: after completion, the next item on that partition is eligible."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.submit_message(tp, 1, 1, b"key-B", b"payload-1")
    await wm.schedule()

    assert mock_engine.submit.await_count == 1
    first_item = mock_engine.submit.call_args_list[0].args[0]

    completion = CompletionEvent(
        id=first_item.id,
        tp=tp,
        offset=first_item.offset,
        epoch=1,
        status=CompletionStatus.SUCCESS,
        error=None,
        attempt=1,
    )
    mock_engine.poll_completed_events.return_value = [completion]
    await wm.poll_completed_events()

    assert mock_engine.submit.await_count == 2


@pytest.mark.asyncio
async def test_partition_on_revoke_clears_partitions_in_flight(mock_engine, tp):
    """on_revoke must clear partitions_in_flight for revoked partitions."""
    wm, tracker = _setup_wm(mock_engine, tp, OrderingMode.PARTITION)

    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1

    wm.on_revoke([tp])

    with patch("pyrallel_consumer.control_plane.work_manager.OffsetTracker") as MockOT:
        new_tracker = _make_tracker_mock(tp)
        MockOT.return_value = new_tracker
        wm.on_assign([tp])
        wm._offset_trackers[tp] = new_tracker

    mock_engine.submit.reset_mock()
    await wm.submit_message(tp, 0, 1, b"key-A", b"payload-0")
    await wm.schedule()
    assert mock_engine.submit.await_count == 1
