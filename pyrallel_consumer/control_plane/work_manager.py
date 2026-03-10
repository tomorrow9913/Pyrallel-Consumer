import asyncio
import logging
import time
import uuid
from collections import deque
from typing import Any, Dict, List, Mapping, Optional, Protocol

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
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
    ):
        self._logger = logging.getLogger(__name__)
        self._execution_engine = execution_engine
        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._virtual_partition_queues: Dict[
            DtoTopicPartition, Dict[Any, asyncio.Queue[WorkItem]]
        ] = {}
        self._completion_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        # Track work items by their unique ID
        self._in_flight_work_items: Dict[str, WorkItem] = {}
        self._dispatch_timestamps: Dict[str, float] = {}

        self._max_in_flight_messages = max_in_flight_messages
        self._current_in_flight_count = 0
        self._rebalancing = False  # New flag to indicate rebalancing state
        self._metrics_exporter = metrics_exporter
        self._ordering_mode = ordering_mode
        self._keys_in_flight: set[tuple[DtoTopicPartition, Any]] = set()
        self._partitions_in_flight: set[DtoTopicPartition] = set()
        self._blocking_cache: Dict[DtoTopicPartition, Optional[OffsetRange]] = {}
        self._blocking_cache_ttl = blocking_cache_ttl
        self._blocking_cache_counter = 0
        self._shared_offset_trackers: set[DtoTopicPartition] = set()
        self._runnable_queue_keys: deque[tuple[DtoTopicPartition, Any]] = deque()
        self._active_runnable_queue_keys: set[tuple[DtoTopicPartition, Any]] = set()
        self._head_offsets: Dict[tuple[DtoTopicPartition, Any], int] = {}
        self._head_queue_keys_by_offset: Dict[
            tuple[DtoTopicPartition, int], tuple[DtoTopicPartition, Any]
        ] = {}

    async def force_fail(
        self,
        tp: DtoTopicPartition,
        offset: int,
        epoch: int,
        error: str,
        attempt: int,
    ) -> bool:
        work_id: Optional[str] = None
        for candidate_id, item in self._in_flight_work_items.items():
            if item.tp == tp and item.offset == offset:
                work_id = candidate_id
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
        internal = getattr(queue, "_queue")  # type: ignore[attr-defined]
        return internal[0]

    def _cleanup_empty_queue(self, tp: DtoTopicPartition, key: Any) -> None:
        queues = self._virtual_partition_queues.get(tp)
        if not queues:
            return

        queue = queues.get(key)
        if queue and queue.empty():
            self._deactivate_queue_key((tp, key))
            queues.pop(key, None)
            if not queues:
                self._virtual_partition_queues.pop(tp, None)

    def _deactivate_queue_key(self, queue_key: tuple[DtoTopicPartition, Any]) -> None:
        self._active_runnable_queue_keys.discard(queue_key)
        head_offset = self._head_offsets.pop(queue_key, None)
        if head_offset is not None:
            self._head_queue_keys_by_offset.pop((queue_key[0], head_offset), None)

    def _activate_queue_key(self, queue_key: tuple[DtoTopicPartition, Any]) -> None:
        if queue_key not in self._active_runnable_queue_keys:
            self._runnable_queue_keys.append(queue_key)
            self._active_runnable_queue_keys.add(queue_key)

    def _refresh_queue_head(
        self,
        tp: DtoTopicPartition,
        key: Any,
        queue: asyncio.Queue[WorkItem],
    ) -> None:
        queue_key = (tp, key)
        self._deactivate_queue_key(queue_key)
        if queue.empty():
            return

        head_offset = self._peek_queue(queue).offset
        self._head_offsets[queue_key] = head_offset
        self._head_queue_keys_by_offset[(tp, head_offset)] = queue_key
        self._activate_queue_key(queue_key)

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
        best_queue_key: Optional[tuple[DtoTopicPartition, Any]] = None
        best_offset: Optional[int] = None

        for tp, gap in blocking_offsets.items():
            if gap is None:
                continue
            queue_key = self._head_queue_keys_by_offset.get((tp, gap.start))
            if queue_key is None:
                continue
            if queue_key not in self._active_runnable_queue_keys:
                continue
            if not self._is_queue_eligible(queue_key):
                continue
            if best_offset is None or gap.start < best_offset:
                best_queue_key = queue_key
                best_offset = gap.start

        if best_queue_key is not None:
            self._active_runnable_queue_keys.discard(best_queue_key)
        return best_queue_key

    def _pick_next_runnable_queue_key(
        self,
    ) -> Optional[tuple[DtoTopicPartition, Any]]:
        attempts = len(self._runnable_queue_keys)
        for _ in range(attempts):
            queue_key = self._runnable_queue_keys.popleft()

            if queue_key not in self._active_runnable_queue_keys:
                continue

            tp, key = queue_key
            queue = self._virtual_partition_queues.get(tp, {}).get(key)
            if queue is None or queue.empty():
                self._deactivate_queue_key(queue_key)
                continue

            if not self._is_queue_eligible(queue_key):
                self._runnable_queue_keys.append(queue_key)
                continue

            self._active_runnable_queue_keys.discard(queue_key)
            return queue_key

        return None

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
                    max_revoke_grace_ms=500,
                )
                self._shared_offset_trackers.discard(tp)
            else:
                self._offset_trackers[tp] = tracker_or_offset
                self._shared_offset_trackers.add(tp)
            self._virtual_partition_queues[tp] = {}
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
            for key in list(self._virtual_partition_queues.get(tp, {}).keys()):
                self._deactivate_queue_key((tp, key))
            self._offset_trackers.pop(tp, None)
            self._shared_offset_trackers.discard(tp)
            self._virtual_partition_queues.pop(tp, None)
        if revoked_tps:
            revoked_tp_set = set(revoked_tps)
            stale_ids = [
                work_id
                for work_id, item in self._in_flight_work_items.items()
                if item.tp in revoked_tp_set
            ]
            for work_id in stale_ids:
                self._in_flight_work_items.pop(work_id, None)
                self._dispatch_timestamps.pop(work_id, None)
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
        if tp not in self._virtual_partition_queues:
            raise ValueError("TopicPartition %s is not assigned to WorkManager." % tp)

        # Get or create the virtual partition queue for this key
        if key not in self._virtual_partition_queues[tp]:
            self._virtual_partition_queues[tp][key] = asyncio.Queue()
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug("Created virtual queue for %s key=%s", tp, key)

        virtual_partition_queue = self._virtual_partition_queues[tp][key]
        was_empty = virtual_partition_queue.empty()

        # Create a WorkItem with a unique ID and put it into the queue
        work_item_id = str(uuid.uuid4())
        work_item = WorkItem(
            id=work_item_id, tp=tp, offset=offset, epoch=epoch, key=key, payload=payload
        )
        await virtual_partition_queue.put(work_item)
        if was_empty:
            self._refresh_queue_head(tp, key, virtual_partition_queue)
        self._in_flight_work_items[work_item_id] = work_item  # Track all work items

        # Keep WorkManager's OffsetTracker in sync so that get_blocking_offsets()
        # can compute gaps correctly and guide the scheduling policy.
        if tp in self._offset_trackers:
            self._offset_trackers[tp].update_last_fetched_offset(offset)
            self._invalidate_blocking_cache()

        # Attempt to submit to the execution engine
        # self._try_submit_to_execution_engine() # This will be triggered by a separate mechanism

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

                item_to_submit = queue_to_dequeue_from.get_nowait()
                if queue_to_dequeue_from.empty():
                    self._cleanup_empty_queue(selected_tp, selected_key)
                else:
                    self._refresh_queue_head(
                        selected_tp, selected_key, queue_to_dequeue_from
                    )

            if item_to_submit:
                try:
                    await self._execution_engine.submit(item_to_submit)
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
                    if queue_to_dequeue_from:
                        if item_to_submit.tp not in self._virtual_partition_queues:
                            self._virtual_partition_queues[item_to_submit.tp] = {}
                        self._virtual_partition_queues[item_to_submit.tp][
                            item_to_submit.key
                        ] = queue_to_dequeue_from
                        await queue_to_dequeue_from.put(item_to_submit)
                        self._refresh_queue_head(
                            item_to_submit.tp,
                            item_to_submit.key,
                            queue_to_dequeue_from,
                        )
                    return
            else:
                return

    async def poll_completed_events(self) -> List[CompletionEvent]:
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

        while not self._completion_queue.empty():
            event = await self._completion_queue.get()
            completed_events.append(event)

            # Update OffsetTracker based on the completion event
            if event.tp in self._offset_trackers:
                offset_tracker = self._offset_trackers[event.tp]
                if event.tp not in self._shared_offset_trackers:
                    offset_tracker.mark_complete(event.offset)
                    self._invalidate_blocking_cache()

                # Decrement in-flight count if the work item was tracked
                if (
                    event.id in self._in_flight_work_items
                ):  # Now CompletionEvent has 'id'
                    completed_item = self._in_flight_work_items[event.id]

                    if self._ordering_mode == OrderingMode.KEY_HASH:
                        self._keys_in_flight.discard(
                            (completed_item.tp, completed_item.key)
                        )
                    elif self._ordering_mode == OrderingMode.PARTITION:
                        self._partitions_in_flight.discard(completed_item.tp)

                    self._cleanup_empty_queue(completed_item.tp, completed_item.key)

                    del self._in_flight_work_items[event.id]
                    self._current_in_flight_count -= 1
                    dispatch_time = self._dispatch_timestamps.pop(event.id, None)
                    if dispatch_time is not None and self._metrics_exporter is not None:
                        duration = max(0.0, time.perf_counter() - dispatch_time)
                        self._metrics_exporter.observe_completion(
                            event.tp, event.status, duration
                        )
                    # After processing a completion, try to submit more tasks
                    await self.schedule()
            else:
                # Log a warning if the topic-partition is not managed
                # This could happen if a revoke happened between submission and completion
                self._logger.warning(
                    "Completion event for unmanaged TopicPartition %s", event.tp
                )
            if event.id in self._dispatch_timestamps:
                self._dispatch_timestamps.pop(event.id, None)
        return completed_events

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
        queue_sizes: Dict[DtoTopicPartition, Dict[Any, int]] = {}
        for tp, virtual_partition_queues in self._virtual_partition_queues.items():
            queue_sizes[tp] = {}
            for key, queue in virtual_partition_queues.items():
                queue_sizes[tp][key] = queue.qsize()
        return queue_sizes
