import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Mapping, Optional, Protocol

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.control_plane.poison_message import PoisonMessageCircuitBreaker
from pyrallel_consumer.control_plane.work_queue_topology import WorkQueueTopology
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    OffsetRange,
    OrderingMode,
)
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import BaseExecutionEngine

OffsetTrackerAssignment = Mapping[DtoTopicPartition, int | OffsetTracker]
GroupedMessage = tuple[int, int, Any] | tuple[int, int, Any, Any]
GroupedMessages = Mapping[tuple[DtoTopicPartition, Any], list[GroupedMessage]]


class MetricsExporter(Protocol):
    def observe_completion(
        self,
        tp: DtoTopicPartition,
        status: CompletionStatus,
        duration_seconds: float,
    ) -> None:
        ...


class WorkManager:
    """
    병렬 처리를 위한 메시지 스케줄링 및 Work-in-Flight 관리를 담당합니다.
    """

    def __init__(
        self,
        execution_engine: BaseExecutionEngine,
        max_in_flight_messages: int = 1000,
        metrics_exporter: Optional[MetricsExporter] = None,
        ordering_mode: OrderingMode = OrderingMode.UNORDERED,
        blocking_cache_ttl: int = 100,
        max_revoke_grace_ms: int = 500,
        poison_message_circuit: Optional[PoisonMessageCircuitBreaker] = None,
    ):
        self._logger = logging.getLogger(__name__)
        self._execution_engine = execution_engine
        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._queue_topology = WorkQueueTopology()
        self._virtual_partition_queues = self._queue_topology.virtual_partition_queues
        self._completion_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        # Track work items by their unique ID
        self._in_flight_work_items: Dict[str, WorkItem] = {}
        self._work_item_ids_by_tp_offset: Dict[tuple[DtoTopicPartition, int], str] = {}
        self._dispatch_timestamps: Dict[str, float] = {}
        self._total_queued_messages = 0
        self._completion_latency_ema_seconds: Optional[float] = None
        self._completion_timestamps: deque[float] = deque(maxlen=256)
        self._completion_rate_window_seconds = 5.0

        self._max_in_flight_messages = max_in_flight_messages
        self._current_in_flight_count = 0
        self._rebalancing = False  # New flag to indicate rebalancing state
        self._metrics_exporter = metrics_exporter
        self._ordering_mode = ordering_mode
        self._max_revoke_grace_ms = max_revoke_grace_ms
        self._keys_in_flight: set[tuple[DtoTopicPartition, Any]] = set()
        self._partitions_in_flight: set[DtoTopicPartition] = set()
        self._blocking_cache: Dict[DtoTopicPartition, Optional[OffsetRange]] = {}
        self._blocking_cache_ttl = blocking_cache_ttl
        self._blocking_cache_counter = 0
        self._shared_offset_trackers: set[DtoTopicPartition] = set()
        self._runnable_queue_keys = self._queue_topology.runnable_queue_keys
        self._active_runnable_queue_keys = (
            self._queue_topology.active_runnable_queue_keys
        )
        self._head_offsets = self._queue_topology.head_offsets
        self._head_queue_keys_by_offset = self._queue_topology.head_queue_keys_by_offset
        self._poison_message_circuit = poison_message_circuit

    def get_ordering_mode(self) -> OrderingMode:
        return self._ordering_mode

    def set_metrics_exporter(self, metrics_exporter: Optional[MetricsExporter]) -> None:
        self._metrics_exporter = metrics_exporter

    def get_average_completion_latency_seconds(self) -> Optional[float]:
        return self._completion_latency_ema_seconds

    def get_recent_completion_rate_per_second(
        self, *, now_monotonic: Optional[float] = None
    ) -> float:
        now = time.perf_counter() if now_monotonic is None else float(now_monotonic)
        self._prune_completion_timestamps(now)
        if self._completion_rate_window_seconds <= 0:
            return 0.0
        return len(self._completion_timestamps) / self._completion_rate_window_seconds

    def _record_completion_latency(
        self, *, duration_seconds: float, now: float
    ) -> None:
        if self._completion_latency_ema_seconds is None:
            self._completion_latency_ema_seconds = duration_seconds
        else:
            self._completion_latency_ema_seconds = (
                self._completion_latency_ema_seconds * 0.8 + duration_seconds * 0.2
            )
        self._completion_timestamps.append(now)
        self._prune_completion_timestamps(now)

    def _prune_completion_timestamps(self, now: float) -> None:
        cutoff = now - self._completion_rate_window_seconds
        while self._completion_timestamps and self._completion_timestamps[0] < cutoff:
            self._completion_timestamps.popleft()

    async def force_fail(
        self,
        tp: DtoTopicPartition,
        offset: int,
        epoch: int,
        error: str,
        attempt: int,
    ) -> bool:
        work_id = self._work_item_ids_by_tp_offset.get((tp, offset))
        if work_id is None:
            for candidate_id, work_item in self._in_flight_work_items.items():
                if work_item.tp == tp and work_item.offset == offset:
                    work_id = candidate_id
                    self._work_item_ids_by_tp_offset[(tp, offset)] = candidate_id
                    break

        if work_id is None:
            return False

        event = CompletionEvent(
            id=work_id,
            tp=tp,
            offset=offset,
            epoch=epoch,
            status=CompletionStatus.FAILURE,
            error=error,
            attempt=attempt,
        )
        await self._completion_queue.put(event)
        return True

    @staticmethod
    def _peek_queue(queue: asyncio.Queue[WorkItem]) -> WorkItem:
        return WorkQueueTopology.peek_queue(queue)

    def _cleanup_empty_queue(self, tp: DtoTopicPartition, key: Any) -> None:
        self._queue_topology.cleanup_empty_queue(tp, key)

    def _deactivate_queue_key(self, queue_key: tuple[DtoTopicPartition, Any]) -> None:
        self._queue_topology.deactivate_queue_key(queue_key)
        self._runnable_queue_keys = self._queue_topology.runnable_queue_keys

    def _activate_queue_key(self, queue_key: tuple[DtoTopicPartition, Any]) -> None:
        self._queue_topology.activate_queue_key(queue_key)

    @staticmethod
    def _enqueue_work_items(
        queue: asyncio.Queue[WorkItem],
        work_items: list[WorkItem],
    ) -> None:
        WorkQueueTopology.enqueue_work_items(queue, work_items)

    def _release_in_flight_item(
        self,
        work_item_id: str,
        *,
        reschedule: bool = False,
    ) -> tuple[bool, Optional[float]]:
        completed_item = self._in_flight_work_items.pop(work_item_id, None)
        dispatch_time = self._dispatch_timestamps.pop(work_item_id, None)
        if completed_item is None:
            return False, dispatch_time
        self._work_item_ids_by_tp_offset.pop(
            (completed_item.tp, completed_item.offset), None
        )

        if self._ordering_mode == OrderingMode.KEY_HASH:
            self._keys_in_flight.discard((completed_item.tp, completed_item.key))
        elif self._ordering_mode == OrderingMode.PARTITION:
            self._partitions_in_flight.discard(completed_item.tp)

        self._cleanup_empty_queue(completed_item.tp, completed_item.key)
        if dispatch_time is not None:
            self._current_in_flight_count = max(0, self._current_in_flight_count - 1)
        if reschedule:
            self._invalidate_blocking_cache()
        return True, dispatch_time

    def _refresh_queue_head(
        self,
        tp: DtoTopicPartition,
        key: Any,
        queue: asyncio.Queue[WorkItem],
    ) -> None:
        self._queue_topology.refresh_queue_head(tp, key, queue)

    def _is_queue_eligible(self, queue_key: tuple[DtoTopicPartition, Any]) -> bool:
        tp, key = queue_key
        if self._ordering_mode == OrderingMode.KEY_HASH:
            return (tp, key) not in self._keys_in_flight
        if self._ordering_mode == OrderingMode.PARTITION:
            return tp not in self._partitions_in_flight
        return True

    def _pick_blocking_queue_key(
        self, blocking_offsets: Dict[DtoTopicPartition, Optional[OffsetRange]]
    ) -> Optional[tuple[DtoTopicPartition, Any]]:
        return self._queue_topology.pick_blocking_queue_key(
            blocking_offsets, self._is_queue_eligible
        )

    def _pick_next_runnable_queue_key(
        self,
    ) -> Optional[tuple[DtoTopicPartition, Any]]:
        return self._queue_topology.pick_next_runnable_queue_key(
            self._is_queue_eligible
        )

    async def _force_fail_queued_item(
        self,
        *,
        item_to_submit: WorkItem,
        selected_tp: DtoTopicPartition,
        selected_key: Any,
    ) -> bool:
        if self._poison_message_circuit is None:
            return False
        if not self._poison_message_circuit.should_force_fail(item_to_submit):
            return False

        dequeued_item = self._queue_topology.dequeue_submitted_item(
            selected_tp, selected_key
        )
        if dequeued_item is None:
            return False

        self._total_queued_messages = max(0, self._total_queued_messages - 1)
        await self._completion_queue.put(
            self._poison_message_circuit.build_forced_failure_event(dequeued_item)
        )
        self._invalidate_blocking_cache()
        return True

    def on_assign(
        self,
        assigned_tps: List[DtoTopicPartition] | OffsetTrackerAssignment,
    ) -> None:
        """
        새로운 파티션이 할당되었을 때 호출됩니다.
        해당 파티션에 대한 OffsetTracker와 가상 파티션 큐를 초기화합니다.

        Args:
            assigned_tps (List[DtoTopicPartition] | OffsetTrackerAssignment):
                할당된 토픽 파티션 또는 시작 오프셋/공유 OffsetTracker가 포함된 매핑

        Returns:
            None
        """
        assignments: OffsetTrackerAssignment
        if isinstance(assigned_tps, Mapping):
            assignments = assigned_tps
        else:
            assignments = {tp: 0 for tp in assigned_tps}

        for tp, tracker_or_offset in assignments.items():
            if isinstance(tracker_or_offset, int):
                self._offset_trackers[tp] = OffsetTracker(
                    topic_partition=tp,
                    starting_offset=tracker_or_offset,
                    max_revoke_grace_ms=self._max_revoke_grace_ms,
                )
                self._shared_offset_trackers.discard(tp)
            else:
                self._offset_trackers[tp] = tracker_or_offset
                self._shared_offset_trackers.add(tp)
            self._queue_topology.assign(tp)
        self._rebalancing = False  # Rebalancing ends when partitions are assigned
        self._invalidate_blocking_cache()

    def on_revoke(self, revoked_tps: List[DtoTopicPartition]) -> None:
        """
        파티션이 해지되었을 때 호출됩니다.
        해당 파티션에 대한 OffsetTracker와 가상 파티션 큐를 제거합니다.

        Args:
            revoked_tps (List[DtoTopicPartition]): 해지된 토픽 파티션

        Returns:
            None
        """
        for tp in revoked_tps:
            removed_count = self._queue_topology.revoke(tp)
            self._runnable_queue_keys = self._queue_topology.runnable_queue_keys
            self._total_queued_messages = max(
                0, self._total_queued_messages - removed_count
            )
            self._offset_trackers.pop(tp, None)
            self._shared_offset_trackers.discard(tp)
            if self._poison_message_circuit is not None:
                self._poison_message_circuit.clear_partition(tp)
        if revoked_tps:
            revoked_tp_set = set(revoked_tps)
            stale_ids = [
                work_id
                for work_id, item in self._in_flight_work_items.items()
                if item.tp in revoked_tp_set
            ]
            for work_id in stale_ids:
                self._release_in_flight_item(work_id)
            if self._ordering_mode == OrderingMode.KEY_HASH:
                self._keys_in_flight = {
                    (tp_key, key)
                    for tp_key, key in self._keys_in_flight
                    if tp_key not in revoked_tp_set
                }
            elif self._ordering_mode == OrderingMode.PARTITION:
                self._partitions_in_flight -= revoked_tp_set
        self._rebalancing = True  # Rebalancing starts when partitions are revoked
        self._invalidate_blocking_cache()

    async def submit_message(
        self, tp: DtoTopicPartition, offset: int, epoch: int, key: Any, payload: Any
    ) -> None:
        """
        BrokerPoller로부터 메시지를 받아 WorkManager의 가상 파티션 큐에 추가합니다.
        실제 ExecutionEngine으로의 제출은 _try_submit_to_execution_engine에서 담당합니다.

        Args:
            tp (DtoTopicPartition): 메시지의 토픽 파티션
            offset (int): 메시지의 오프셋
            epoch (int): 메시지의 epoch
            key (Any): 메시지의 키 (가상 파티션 구분용)
            payload (Any): 메시지의 페이로드

        Returns:
            None
        """
        await self.submit_message_batch({(tp, key): [(offset, epoch, payload)]})

    async def submit_message_batch(self, grouped_messages: GroupedMessages) -> None:
        max_offsets_by_tp: Dict[DtoTopicPartition, int] = {}

        for (tp, key), messages in grouped_messages.items():
            if tp not in self._virtual_partition_queues:
                raise ValueError(
                    "TopicPartition %s is not assigned to WorkManager." % tp
                )

            work_items: list[WorkItem] = []

            for message in messages:
                if len(message) == 3:
                    offset, epoch, payload = message
                    poison_key = key
                else:
                    offset, epoch, payload, poison_key = message
                work_item_id = str(uuid.uuid4())
                work_item = WorkItem(
                    id=work_item_id,
                    tp=tp,
                    offset=offset,
                    epoch=epoch,
                    key=key,
                    payload=payload,
                    poison_key=poison_key,
                )
                work_items.append(work_item)
                self._in_flight_work_items[work_item_id] = work_item
                self._work_item_ids_by_tp_offset[(tp, offset)] = work_item_id
                previous_max = max_offsets_by_tp.get(tp)
                if previous_max is None or offset > previous_max:
                    max_offsets_by_tp[tp] = offset

            self._queue_topology.enqueue_batch(tp, key, work_items)
            self._total_queued_messages += len(work_items)

        if max_offsets_by_tp:
            for tp, max_offset in max_offsets_by_tp.items():
                tracker = self._offset_trackers.get(tp)
                if tracker is not None:
                    tracker.update_last_fetched_offset(max_offset)
            self._invalidate_blocking_cache()

    async def schedule(self) -> None:
        """
        Submits WorkItems to the ExecutionEngine based on current in-flight count and blocking offsets.
        Using "Lowest blocking offset first" scheduling policy to determine which WorkItem to submit next.
        현재 대기 중인 WorkItem을 ExecutionEngine으로 제출할 수 있는지 확인하고 제출합니다.
        "Lowest blocking offset 우선" 스케줄링 정책을 사용합니다.

        Returns:
            None
        """
        if self._blocking_cache_counter <= 0:
            blocking_offsets = self.get_blocking_offsets()
            self._blocking_cache = blocking_offsets
            self._blocking_cache_counter = self._blocking_cache_ttl
        else:
            blocking_offsets = self._blocking_cache
            self._blocking_cache_counter -= 1

        while True:
            if self._current_in_flight_count >= self._max_in_flight_messages:
                return
            if self._rebalancing:
                return

            selected_queue_key = self._pick_blocking_queue_key(blocking_offsets)
            if selected_queue_key is None:
                selected_queue_key = self._pick_next_runnable_queue_key()

            item_to_submit: Optional[WorkItem] = None
            queue_to_dequeue_from: Optional[asyncio.Queue[WorkItem]] = None

            if selected_queue_key is not None:
                selected_tp, selected_key = selected_queue_key
                queue_to_dequeue_from = self._virtual_partition_queues.get(
                    selected_tp, {}
                ).get(selected_key)
                if queue_to_dequeue_from is None or queue_to_dequeue_from.empty():
                    self._deactivate_queue_key(selected_queue_key)
                    continue

                item_to_submit = self._peek_queue(queue_to_dequeue_from)

            if item_to_submit:
                force_failed = await self._force_fail_queued_item(
                    item_to_submit=item_to_submit,
                    selected_tp=selected_tp,
                    selected_key=selected_key,
                )
                if force_failed:
                    continue

                try:
                    await self._execution_engine.submit(item_to_submit)
                    dequeued_item = self._queue_topology.dequeue_submitted_item(
                        selected_tp, selected_key
                    )
                    if dequeued_item is None:
                        return
                    self._total_queued_messages = max(
                        0, self._total_queued_messages - 1
                    )
                    self._current_in_flight_count += 1
                    self._dispatch_timestamps[item_to_submit.id] = time.perf_counter()
                    if self._ordering_mode == OrderingMode.KEY_HASH:
                        self._keys_in_flight.add(
                            (item_to_submit.tp, item_to_submit.key)
                        )
                    elif self._ordering_mode == OrderingMode.PARTITION:
                        self._partitions_in_flight.add(item_to_submit.tp)
                except Exception:
                    self._logger.exception(
                        "Error submitting work item %s", item_to_submit.id
                    )
                    if queue_to_dequeue_from is not None:
                        self._refresh_queue_head(
                            item_to_submit.tp,
                            item_to_submit.key,
                            queue_to_dequeue_from,
                        )
                    return
            else:
                return

    async def poll_completed_events(
        self, *, schedule_after_release: bool = True
    ) -> List[CompletionEvent]:
        """
        Updates the WorkManager by polling completed events from the ExecutionEngine.
        ExecutionEngine으로부터 완료 이벤트를 폴링하고 OffsetTracker를 업데이트합니다.

        Returns:
            List[CompletionEvent]: 폴링된 완료 이벤트 목록
        """
        completed_events: List[CompletionEvent] = []
        # First, poll for completed events from the execution engine
        engine_completed_events = await self._execution_engine.poll_completed_events()
        for event in engine_completed_events:
            await self._completion_queue.put(event)

        while True:
            should_schedule = False
            while not self._completion_queue.empty():
                event = await self._completion_queue.get()
                completed_events.append(event)
                should_schedule = (
                    self._process_completion_event(event) or should_schedule
                )

            if not schedule_after_release or not should_schedule:
                break

            await self.schedule()
            if self._completion_queue.empty():
                break
        return completed_events

    def _process_completion_event(self, event: CompletionEvent) -> bool:
        if event.tp not in self._offset_trackers:
            # Log a warning if the topic-partition is not managed.
            # This could happen if a revoke happened between submission and completion.
            self._logger.warning(
                "Completion event for unmanaged TopicPartition %s", event.tp
            )
            self._dispatch_timestamps.pop(event.id, None)
            return False

        offset_tracker = self._offset_trackers[event.tp]
        current_epoch = offset_tracker.get_current_epoch()
        if event.epoch != current_epoch:
            completed_item = self._in_flight_work_items.get(event.id)
            if completed_item is not None and completed_item.tp == event.tp:
                self._release_in_flight_item(event.id, reschedule=True)
            self._dispatch_timestamps.pop(event.id, None)
            return False

        if event.tp not in self._shared_offset_trackers:
            offset_tracker.mark_complete(event.offset)
            self._invalidate_blocking_cache()

        if event.id not in self._in_flight_work_items:
            self._dispatch_timestamps.pop(event.id, None)
            return False

        completed_item = self._in_flight_work_items[event.id]
        if self._poison_message_circuit is not None:
            self._poison_message_circuit.record_completion(event, completed_item)
        _, dispatch_time = self._release_in_flight_item(event.id)
        if dispatch_time is not None:
            duration = max(0.0, time.perf_counter() - dispatch_time)
            self._record_completion_latency(
                duration_seconds=duration,
                now=time.perf_counter(),
            )
        if dispatch_time is not None and self._metrics_exporter is not None:
            completion_observer = getattr(
                self._metrics_exporter, "observe_work_completion", None
            )
            if callable(completion_observer):
                completion_observer(event, completed_item, duration)
            else:
                self._metrics_exporter.observe_completion(
                    event.tp, event.status, duration
                )
        self._dispatch_timestamps.pop(event.id, None)
        return True

    def get_blocking_offsets(self) -> Dict[DtoTopicPartition, Optional[OffsetRange]]:
        """
        Returns the Blocking Offset information that is currently preventing HWM progress.
        if there is a Gap starting from the lowest offset, that Gap's start is the Blocking Offset.
        현재 HWM 진행을 막고 있는 Blocking Offset 정보를 반환합니다.
        가장 낮은 오프셋부터 시작하는 Gap이 있다면 해당 Gap의 시작이 Blocking Offset입니다.

        Returns:
            Dict[DtoTopicPartition, Optional[OffsetRange]]: Blocking Offset 정보
        """
        blocking_offsets: Dict[DtoTopicPartition, Optional[OffsetRange]] = {}
        for tp, tracker in self._offset_trackers.items():
            if tp not in self._shared_offset_trackers:
                tracker.advance_high_water_mark()  # Ensure HWM is up-to-date
            gaps = tracker.get_gaps()
            if gaps:
                # The first gap's start is the lowest blocking offset
                blocking_offsets[tp] = OffsetRange(
                    gaps[0].start, gaps[0].start
                )  # Return as a single-offset range for simplicity
            else:
                blocking_offsets[tp] = None
        return blocking_offsets

    def get_total_in_flight_count(self) -> int:
        """
        Returns the total number of in-flight messages across all partitions currently managed by the WorkManager.
        현재 WorkManager가 관리하는 모든 파티션의 인플라이트 메시지 총 수를 반환합니다.

        Returns:
            int: 현재 인플라이트 메시지 총 수
        """
        return self._current_in_flight_count  # Updated to use WorkManager's count

    def get_total_queued_messages(self) -> int:
        return self._total_queued_messages

    def get_max_in_flight_messages(self) -> int:
        return self._max_in_flight_messages

    def get_poison_message_open_circuit_count(self) -> int:
        if self._poison_message_circuit is None:
            return 0
        return self._poison_message_circuit.get_open_circuit_count()

    def set_max_in_flight_messages(self, value: int) -> None:
        if value < 1:
            raise ValueError("max_in_flight_messages must be >= 1")
        self._max_in_flight_messages = value

    def is_rebalancing(self) -> bool:
        return self._rebalancing

    def get_in_flight_counts(self) -> Dict[DtoTopicPartition, int]:
        counts: Dict[DtoTopicPartition, int] = {}
        for work_item_id in self._dispatch_timestamps:
            work_item = self._in_flight_work_items.get(work_item_id)
            if work_item is None:
                continue
            counts[work_item.tp] = counts.get(work_item.tp, 0) + 1
        return counts

    def _invalidate_blocking_cache(self) -> None:
        self._blocking_cache_counter = 0

    def get_gaps(self) -> Dict[DtoTopicPartition, List[OffsetRange]]:
        """
        Returns the ranges of uncompleted offsets for each topic-partition.
        완료되지 않은 오프셋 범위를 각 토픽-파티션별로 반환합니다.

        Returns:
            Dict[DtoTopicPartition, List[OffsetRange]]: 완료되지 않은 오프셋 범위
        """
        gaps: Dict[DtoTopicPartition, List[OffsetRange]] = {}
        for tp, tracker in self._offset_trackers.items():
            gaps[tp] = tracker.get_gaps()
        return gaps

    def get_true_lag(self) -> Dict[DtoTopicPartition, int]:
        """
        Returns the true lag for each topic-partition.
        True lag is the difference between the last fetched offset and the last committed offset.
        각 토픽 - 파티션별 실제 지연 시간을 반환합니다.
        실제 지연 시간은 마지막으로 가져온 오프셋과 마지막으로 커밋된 오프셋의 차이입니다.

        Returns:
            Dict[DtoTopicPartition, int]: True lag for each topic-partition
        """
        true_lag: Dict[DtoTopicPartition, int] = {}
        for tp, tracker in self._offset_trackers.items():
            true_lag[tp] = tracker.last_fetched_offset - tracker.last_committed_offset
        return true_lag

    def get_virtual_queue_sizes(self) -> Dict[DtoTopicPartition, Dict[Any, int]]:
        """
        Returns the current size of each virtual partition queue.
        각 가상 파티션 큐의 현재 크기를 반환합니다.

        Returns:
            Dict[DtoTopicPartition, Dict[Any, int]]: 가상 파티션 큐의 현재 크기
        """
        return self._queue_topology.get_virtual_queue_sizes()
