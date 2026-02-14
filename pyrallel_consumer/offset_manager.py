import asyncio
from collections import defaultdict

from sortedcontainers import SortedSet


class OffsetTracker:
    def __init__(self):
        self._in_flight = defaultdict(SortedSet)
        self._lock = asyncio.Lock()

    async def add(self, partition: int, offset: int):
        async with self._lock:
            self._in_flight[partition].add(offset)

    async def remove(self, partition: int, offset: int):
        async with self._lock:
            self._in_flight[partition].discard(offset)
            if not self._in_flight[partition]:
                del self._in_flight[partition]

    async def get_safe_offsets(self, active_partitions: dict[int, int]) -> list:
        """파티션별 안전 지점 계산"""
        parts_to_commit = []
        async with self._lock:
            for p_id, p_max in active_partitions.items():
                offsets = self._in_flight.get(p_id)
                safe_ptr = (offsets[0] - 1) if offsets else p_max
                if safe_ptr >= 0:
                    parts_to_commit.append((p_id, safe_ptr + 1))
        return parts_to_commit

    async def get_total_in_flight_count(self) -> int:
        """현재 처리 중인 (in-flight) 메시지의 총 개수를 반환합니다."""
        async with self._lock:
            return sum(len(offsets) for offsets in self._in_flight.values())
