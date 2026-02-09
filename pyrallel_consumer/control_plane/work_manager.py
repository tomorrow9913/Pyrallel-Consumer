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

    def on_assign(self, assigned_tps: List[DtoTopicPartition]):
        """
        새로운 파티션이 할당되었을 때 호출됩니다.
        해당 파티션에 대한 OffsetTracker와 가상 파티션 큐를 초기화합니다.
        """
        for tp in assigned_tps:
            # Assuming starting_offset is 0 for initial assignment, and max_revoke_grace_ms default to 500
            self._offset_trackers[tp] = OffsetTracker(
                topic_partition=tp, starting_offset=0, max_revoke_grace_ms=500
            )
            self._virtual_partition_queues[tp] = {}

    def on_revoke(self, revoked_tps: List[DtoTopicPartition]):
        """
        파티션이 해지되었을 때 호출됩니다.
        해당 파티션에 대한 OffsetTracker와 가상 파티션 큐를 제거합니다.
        """
        for tp in revoked_tps:
            self._offset_trackers.pop(tp, None)
            self._virtual_partition_queues.pop(tp, None)

    async def submit_message(
        self, tp: DtoTopicPartition, offset: int, epoch: int, key: Any, payload: Any
    ):
        """
        BrokerPoller로부터 메시지를 받아 WorkManager의 가상 파티션 큐에 추가합니다.
        실제 ExecutionEngine으로의 제출은 _try_submit_to_execution_engine에서 담당합니다.
        """
        if tp not in self._virtual_partition_queues:
            raise ValueError(f"TopicPartition {tp} is not assigned to WorkManager.")

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
        await self._try_submit_to_execution_engine()

    async def _try_submit_to_execution_engine(self):
        """
        현재 대기 중인 WorkItem을 ExecutionEngine으로 제출할 수 있는지 확인하고 제출합니다.
        가장 먼저 들어온 메시지부터 처리하는 간단한 스케줄링 정책을 사용합니다.
        나중에 Blocking Offset을 고려한 스케줄링 로직이 추가될 예정입니다.
        """
        if self._current_in_flight_count >= self._max_in_flight_messages:
            return

        for tp, virtual_partition_queues in self._virtual_partition_queues.items():
            for key, queue in virtual_partition_queues.items():
                if not queue.empty():
                    work_item = await queue.get()
                    try:
                        await self._execution_engine.submit(work_item)
                        self._current_in_flight_count += 1
                        # Since we submitted one, we should try to submit more if capacity allows
                        await self._try_submit_to_execution_engine()
                        return
                    except Exception as e:
                        # Log error and potentially re-queue or handle the work_item
                        print(f"Error submitting work item {work_item.id}: {e}")
                        # For now, put it back to the queue.
                        await queue.put(work_item)
                        return

    async def poll_completed_events(self) -> List[CompletionEvent]:
        """
        ExecutionEngine으로부터 완료 이벤트를 폴링하고 OffsetTracker를 업데이트합니다.
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
                    f"Warning: Completion event for unmanaged TopicPartition {event.tp}"
                )
        return completed_events

    def get_blocking_offsets(self) -> Dict[DtoTopicPartition, Optional[OffsetRange]]:
        """
        현재 HWM 진행을 막고 있는 Blocking Offset 정보를 반환합니다.
        가장 낮은 오프셋부터 시작하는 Gap이 있다면 해당 Gap의 시작이 Blocking Offset입니다.
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
        현재 WorkManager가 관리하는 모든 파티션의 인플라이트 메시지 총 수를 반환합니다.
        """
        return self._current_in_flight_count  # Updated to use WorkManager's count
