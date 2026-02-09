import asyncio
import uuid
from typing import Any, Dict, List, Optional

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import CompletionEvent, OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem
from pyrallel_consumer.execution_plane.base import (
    BaseExecutionEngine,  # Assuming this path for now
)


class WorkManager:
    """
    병렬 처리를 위한 메시지 스케줄링 및 Work-in-Flight 관리를 담당합니다.
    """

    def __init__(
        self, execution_engine: BaseExecutionEngine, max_in_flight_messages: int = 1000
    ):
        self._execution_engine = execution_engine
        self._offset_trackers: Dict[DtoTopicPartition, OffsetTracker] = {}
        self._virtual_partition_queues: Dict[
            DtoTopicPartition, Dict[Any, asyncio.Queue[WorkItem]]
        ] = {}
        self._completion_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        # Track work items by their unique ID
        self._in_flight_work_items: Dict[str, WorkItem] = {}

        self._max_in_flight_messages = max_in_flight_messages
        self._current_in_flight_count = 0
        self._rebalancing = False  # New flag to indicate rebalancing state

    def on_assign(self, assigned_tps: List[DtoTopicPartition]) -> None:
        """
        새로운 파티션이 할당되었을 때 호출됩니다.
        해당 파티션에 대한 OffsetTracker와 가상 파티션 큐를 초기화합니다.

        Args:
            assigned_tps (List[DtoTopicPartition]): 할당된 토픽 파티션

        Returns:
            None
        """
        for tp in assigned_tps:
            # Assuming starting_offset is 0 for initial assignment, and max_revoke_grace_ms default to 500
            self._offset_trackers[tp] = OffsetTracker(
                topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
            )
            self._virtual_partition_queues[tp] = {}
        self._rebalancing = False  # Rebalancing ends when partitions are assigned

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
            self._offset_trackers.pop(tp, None)
            self._virtual_partition_queues.pop(tp, None)
        self._rebalancing = True  # Rebalancing starts when partitions are revoked

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

        virtual_partition_queue = self._virtual_partition_queues[tp][key]

        # Create a WorkItem with a unique ID and put it into the queue
        work_item_id = str(uuid.uuid4())
        work_item = WorkItem(
            id=work_item_id, tp=tp, offset=offset, epoch=epoch, key=key, payload=payload
        )
        await virtual_partition_queue.put(work_item)
        self._in_flight_work_items[work_item_id] = work_item  # Track all work items

        # Attempt to submit to the execution engine
        # self._try_submit_to_execution_engine() # This will be triggered by a separate mechanism

    async def _try_submit_to_execution_engine(self) -> None:
        """
        Submits WorkItems to the ExecutionEngine based on current in-flight count and blocking offsets.
        Using "Lowest blocking offset first" scheduling policy to determine which WorkItem to submit next.
        현재 대기 중인 WorkItem을 ExecutionEngine으로 제출할 수 있는지 확인하고 제출합니다.
        "Lowest blocking offset 우선" 스케줄링 정책을 사용합니다.

        Returns:
            None
        """
        if self._current_in_flight_count >= self._max_in_flight_messages:
            return
        if self._rebalancing:
            return  # Do not submit messages during rebalancing

        blocking_offsets = self.get_blocking_offsets()

        # Variables to store the *best* candidate found after scanning all queues
        best_candidate_item: Optional[WorkItem] = None
        best_candidate_queue: Optional[asyncio.Queue[WorkItem]] = None

        current_lowest_blocking_offset: Optional[int] = (
            None  # Tracks the offset of the best blocking candidate found so far
        )

        # In a single pass, find the lowest blocking item OR the first non-blocking item if no blocking items are found
        first_non_blocking_item_seen: Optional[WorkItem] = None
        first_non_blocking_queue_seen: Optional[asyncio.Queue[WorkItem]] = None

        for tp, virtual_partition_queues in self._virtual_partition_queues.items():
            for key, queue in virtual_partition_queues.items():
                if queue.empty():
                    continue

                # Simulate peek: get and put back immediately
                try:
                    peek_item = await queue.get()
                    await queue.put(peek_item)

                # Should not happen given the queue.empty() check, but for robustness
                except asyncio.QueueEmpty:
                    continue

                is_blocking_for_this_tp = blocking_offsets.get(tp) is not None

                is_target_blocking_offset = (
                    is_blocking_for_this_tp
                    and blocking_offsets[tp] is not None
                    and peek_item.offset == blocking_offsets[tp].start  # type: ignore
                )

                if is_target_blocking_offset:
                    if (
                        current_lowest_blocking_offset is None
                        or peek_item.offset < current_lowest_blocking_offset
                    ):
                        best_candidate_item = peek_item
                        best_candidate_queue = queue
                        current_lowest_blocking_offset = peek_item.offset
                # Only capture the first non-blocking item seen IF we haven't found any blocking candidates YET.
                # This ensures that if a blocking item is later found, it takes precedence.
                elif (
                    best_candidate_item is None and first_non_blocking_item_seen is None
                ):
                    first_non_blocking_item_seen = peek_item
                    first_non_blocking_queue_seen = queue

        # Now, actually dequeue and submit the selected item
        item_to_submit: Optional[WorkItem] = None
        queue_to_dequeue_from: Optional[asyncio.Queue[WorkItem]] = None

        if (
            best_candidate_item and best_candidate_queue
        ):  # A blocking item was found and selected
            item_to_submit = await best_candidate_queue.get()
            queue_to_dequeue_from = best_candidate_queue
        elif (
            first_non_blocking_item_seen and first_non_blocking_queue_seen
        ):  # No blocking item, fall back to first non-blocking
            item_to_submit = await first_non_blocking_queue_seen.get()
            queue_to_dequeue_from = first_non_blocking_queue_seen

        if item_to_submit:
            try:
                await self._execution_engine.submit(item_to_submit)
                self._current_in_flight_count += 1
                await self._try_submit_to_execution_engine()  # Try to submit more
            except Exception as e:
                print("Error submitting work item %s: %s" % (item_to_submit.id, e))
                if queue_to_dequeue_from:
                    await queue_to_dequeue_from.put(
                        item_to_submit
                    )  # Re-queue on failure
        # Else: No item to submit, do nothing.

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
                offset_tracker.mark_complete(event.offset)

                # Decrement in-flight count if the work item was tracked
                if (
                    event.id in self._in_flight_work_items
                ):  # Now CompletionEvent has 'id'
                    del self._in_flight_work_items[event.id]
                    self._current_in_flight_count -= 1
                    # After processing a completion, try to submit more tasks
                    await self._try_submit_to_execution_engine()
            else:
                # Log a warning if the topic-partition is not managed
                # This could happen if a revoke happened between submission and completion
                print(
                    "Warning: Completion event for unmanaged TopicPartition %s"
                    % event.tp
                )
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
