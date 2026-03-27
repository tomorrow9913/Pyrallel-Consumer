from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import Any, Callable, Optional

from pyrallel_consumer.dto import OffsetRange
from pyrallel_consumer.dto import TopicPartition as DtoTopicPartition
from pyrallel_consumer.dto import WorkItem

QueueKey = tuple[DtoTopicPartition, Any]


class RunnableQueueKeys:
    def __init__(self) -> None:
        self._ordered: OrderedDict[QueueKey, None] = OrderedDict()

    def append(self, queue_key: QueueKey) -> None:
        self._ordered[queue_key] = None

    def discard(self, queue_key: QueueKey) -> None:
        self._ordered.pop(queue_key, None)

    def popleft(self) -> QueueKey:
        queue_key, _ = self._ordered.popitem(last=False)
        return queue_key

    def clear(self) -> None:
        self._ordered.clear()

    def extend(self, queue_keys: list[QueueKey]) -> None:
        for queue_key in queue_keys:
            self._ordered[queue_key] = None

    def count(self, queue_key: QueueKey) -> int:
        return 1 if queue_key in self._ordered else 0

    def __contains__(self, queue_key: object) -> bool:
        return queue_key in self._ordered

    def __len__(self) -> int:
        return len(self._ordered)

    def __iter__(self):
        return iter(self._ordered)


class WorkQueueTopology:
    def __init__(self) -> None:
        self.virtual_partition_queues: dict[
            DtoTopicPartition, dict[Any, asyncio.Queue[WorkItem]]
        ] = {}
        self.runnable_queue_keys = RunnableQueueKeys()
        self.active_runnable_queue_keys: set[QueueKey] = set()
        self.head_offsets: dict[QueueKey, int] = {}
        self.head_queue_keys_by_offset: dict[
            tuple[DtoTopicPartition, int], QueueKey
        ] = {}

    @staticmethod
    def peek_queue(queue: asyncio.Queue[WorkItem]) -> WorkItem:
        internal = getattr(queue, "_queue")  # type: ignore[attr-defined]
        return internal[0]

    @staticmethod
    def enqueue_work_items(
        queue: asyncio.Queue[WorkItem],
        work_items: list[WorkItem],
    ) -> None:
        if not work_items:
            return
        if len(work_items) == 1:
            queue.put_nowait(work_items[0])
            return

        internal_queue = getattr(queue, "_queue", None)
        finished = getattr(queue, "_finished", None)
        wakeup_next = getattr(queue, "_wakeup_next", None)
        getters = getattr(queue, "_getters", None)
        if (
            internal_queue is None
            or finished is None
            or getters is None
            or not callable(wakeup_next)
            or getattr(queue, "_is_shutdown", False)
        ):
            for work_item in work_items:
                queue.put_nowait(work_item)
            return

        internal_queue.extend(work_items)
        queue._unfinished_tasks += len(work_items)  # type: ignore[attr-defined]
        finished.clear()
        wakeup_next(getters)

    def assign(self, tp: DtoTopicPartition) -> None:
        self.virtual_partition_queues[tp] = {}

    def revoke(self, tp: DtoTopicPartition) -> int:
        removed_count = 0
        for queue in self.virtual_partition_queues.get(tp, {}).values():
            removed_count += queue.qsize()
        for key in list(self.virtual_partition_queues.get(tp, {}).keys()):
            self.deactivate_queue_key((tp, key))
        self.virtual_partition_queues.pop(tp, None)
        return removed_count

    def get_queue(
        self,
        tp: DtoTopicPartition,
        key: Any,
    ) -> Optional[asyncio.Queue[WorkItem]]:
        return self.virtual_partition_queues.get(tp, {}).get(key)

    def ensure_queue(
        self,
        tp: DtoTopicPartition,
        key: Any,
    ) -> asyncio.Queue[WorkItem]:
        queue = self.get_queue(tp, key)
        if queue is None:
            queue = asyncio.Queue()
            self.virtual_partition_queues[tp][key] = queue
        return queue

    def cleanup_empty_queue(self, tp: DtoTopicPartition, key: Any) -> None:
        queues = self.virtual_partition_queues.get(tp)
        if not queues:
            return

        queue = queues.get(key)
        if queue and queue.empty():
            self.deactivate_queue_key((tp, key))
            queues.pop(key, None)

    def deactivate_queue_key(self, queue_key: QueueKey) -> None:
        self.active_runnable_queue_keys.discard(queue_key)
        head_offset = self.head_offsets.pop(queue_key, None)
        if head_offset is not None:
            self.head_queue_keys_by_offset.pop((queue_key[0], head_offset), None)

        self.runnable_queue_keys.discard(queue_key)

    def activate_queue_key(self, queue_key: QueueKey) -> None:
        if queue_key not in self.active_runnable_queue_keys:
            self.runnable_queue_keys.append(queue_key)
            self.active_runnable_queue_keys.add(queue_key)

    def refresh_queue_head(
        self,
        tp: DtoTopicPartition,
        key: Any,
        queue: asyncio.Queue[WorkItem],
    ) -> None:
        queue_key = (tp, key)
        self.deactivate_queue_key(queue_key)
        if queue.empty():
            return

        head_offset = self.peek_queue(queue).offset
        self.head_offsets[queue_key] = head_offset
        self.head_queue_keys_by_offset[(tp, head_offset)] = queue_key
        self.activate_queue_key(queue_key)

    def enqueue_batch(
        self,
        tp: DtoTopicPartition,
        key: Any,
        work_items: list[WorkItem],
    ) -> None:
        queue = self.ensure_queue(tp, key)
        was_empty = queue.empty()
        self.enqueue_work_items(queue, work_items)
        if was_empty and not queue.empty():
            self.refresh_queue_head(tp, key, queue)

    def pick_blocking_queue_key(
        self,
        blocking_offsets: dict[DtoTopicPartition, Optional[OffsetRange]],
        is_queue_eligible: Callable[[QueueKey], bool],
    ) -> Optional[QueueKey]:
        best_queue_key: Optional[QueueKey] = None
        best_offset: Optional[int] = None

        for tp, gap in blocking_offsets.items():
            if gap is None:
                continue
            queue_key = self.head_queue_keys_by_offset.get((tp, gap.start))
            if queue_key is None:
                continue
            if queue_key not in self.active_runnable_queue_keys:
                continue
            if not is_queue_eligible(queue_key):
                continue
            if best_offset is None or gap.start < best_offset:
                best_queue_key = queue_key
                best_offset = gap.start

        if best_queue_key is not None:
            self.active_runnable_queue_keys.discard(best_queue_key)
            self.runnable_queue_keys.discard(best_queue_key)
        return best_queue_key

    def pick_next_runnable_queue_key(
        self,
        is_queue_eligible: Callable[[QueueKey], bool],
    ) -> Optional[QueueKey]:
        attempts = len(self.runnable_queue_keys)
        for _ in range(attempts):
            queue_key = self.runnable_queue_keys.popleft()

            if queue_key not in self.active_runnable_queue_keys:
                continue

            tp, key = queue_key
            queue = self.virtual_partition_queues.get(tp, {}).get(key)
            if queue is None or queue.empty():
                self.deactivate_queue_key(queue_key)
                continue

            if not is_queue_eligible(queue_key):
                self.runnable_queue_keys.append(queue_key)
                continue

            self.active_runnable_queue_keys.discard(queue_key)
            return queue_key

        return None

    def pop_next_queue_key(
        self,
        blocking_offsets: dict[DtoTopicPartition, Optional[OffsetRange]],
        is_queue_eligible: Callable[[QueueKey], bool],
    ) -> Optional[QueueKey]:
        queue_key = self.pick_blocking_queue_key(blocking_offsets, is_queue_eligible)
        if queue_key is not None:
            return queue_key
        return self.pick_next_runnable_queue_key(is_queue_eligible)

    def dequeue_submitted_item(
        self,
        tp: DtoTopicPartition,
        key: Any,
    ) -> Optional[WorkItem]:
        queue = self.get_queue(tp, key)
        if queue is None or queue.empty():
            self.deactivate_queue_key((tp, key))
            return None

        work_item = queue.get_nowait()
        if queue.empty():
            self.cleanup_empty_queue(tp, key)
        else:
            self.refresh_queue_head(tp, key, queue)
        return work_item

    def get_virtual_queue_sizes(self) -> dict[DtoTopicPartition, dict[Any, int]]:
        queue_sizes: dict[DtoTopicPartition, dict[Any, int]] = {}
        for tp, queue_map in self.virtual_partition_queues.items():
            queue_sizes[tp] = {key: queue.qsize() for key, queue in queue_map.items()}
        return queue_sizes
