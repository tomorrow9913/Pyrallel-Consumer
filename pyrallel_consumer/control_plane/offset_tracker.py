import logging
import time
from typing import Dict, Optional, Set

from cachetools import LRUCache  # type: ignore[import-untyped]
from sortedcontainers import SortedSet

from pyrallel_consumer.dto import OffsetRange, TopicPartition


class OffsetTracker:
    """
    특정 토픽 파티션에 대한 오프셋 추적기입니다.

    Attributes:
        topic_partition (TopicPartition): 토픽 파티션 정보
        last_committed_offset (int): 마지막으로 커밋된 오프셋
        completed_offsets (SortedSet[int]): 완료된 오프셋들의 정렬된 집합
        in_flight_offsets (Set[int]): 현재 처리 중인 오프셋들의 집합
        last_fetched_offset (int): 마지막으로 가져온 오프셋
        epoch (int): 현재 파티션 소유권의 세대 번호
    """

    def __init__(
        self,
        topic_partition: TopicPartition,
        starting_offset: int,
        max_revoke_grace_ms: int,
        initial_completed_offsets: Optional[Set[int]] = None,
    ):
        """
        초기화 메서드입니다.

        Args:
            topic_partition (TopicPartition): 토픽 파티션 정보
            starting_offset (int): 시작 오프셋
            max_revoke_grace_ms (int): 최대 리보크 유예 시간 (밀리초)
            initial_completed_offsets (Set[int], optional): 초기 완료된 오프셋 집합. Defaults to None.
        """
        self.topic_partition = topic_partition
        self.last_committed_offset = starting_offset - 1
        self.completed_offsets: SortedSet = SortedSet(
            initial_completed_offsets
            if initial_completed_offsets is not None
            else set()
        )
        self.last_fetched_offset = starting_offset - 1
        self.max_revoke_grace_ms = max_revoke_grace_ms
        self.epoch = 0  # Initial epoch
        # To track when an offset first became blocking
        self._blocking_offset_timestamps: Dict[int, float] = {}
        self._logger = logging.getLogger(__name__)
        self._state_version = 0
        self._gaps_cache: LRUCache = LRUCache(maxsize=1)
        self._gaps_cache_key: Optional[tuple[int, int, int]] = None
        self._last_gap_key: Optional[tuple[int, int, int]] = None
        self._repeat_gap_count: int = 0

    def _bump_version(self) -> None:
        self._state_version += 1
        self._gaps_cache.clear()
        self._gaps_cache_key = None

    @property
    def in_flight_offsets(self) -> Set[int]:
        """

        현재 처리 중인 오프셋들의 집합을 반환합니다.
        Returns:
            Set[int]: 현재 처리 중인 오프셋들의 집합
        """
        return self.completed_offsets.symmetric_difference(
            set(range(self.last_committed_offset + 1, self.last_fetched_offset + 1))
        )

    def increment_epoch(self) -> None:
        """현재 epoch을 1 증가시킵니다."""
        self.epoch += 1

    def get_current_epoch(self) -> int:
        """현재 epoch을 반환합니다."""
        return self.epoch

    def mark_complete(self, offset: int) -> None:
        """
        marks the given offset as complete.
        오프셋을 완료로 표시합니다.

        Args:
            offset (int): 완료로 표시할 오프셋

        Returns:
            None
        """
        if offset > self.last_committed_offset:
            before_size = len(self.completed_offsets)
            self.completed_offsets.add(offset)
            if len(self.completed_offsets) != before_size:
                self._bump_version()

    def advance_high_water_mark(self) -> None:
        """
        Advances the last committed offset based on completed offsets.
        완료된 오프셋을 기준으로 마지막 커밋된 오프셋을 갱신합니다.

        Returns:
            None
        """
        old_hwm = self.last_committed_offset
        new_hwm = self.last_committed_offset
        # SortedSet allows efficient iteration in sorted order
        for offset in list(
            self.completed_offsets
        ):  # Iterate over a copy to allow modification
            if offset == new_hwm + 1:
                new_hwm = offset
            else:
                # Found a gap, stop advancing HWM
                break

        # Remove committed offsets from the set
        if new_hwm > self.last_committed_offset:
            for i in range(self.last_committed_offset + 1, new_hwm + 1):
                if (
                    i in self.completed_offsets
                ):  # Ensure the offset is still in the set before removing
                    self.completed_offsets.remove(i)
            self.last_committed_offset = new_hwm
            self._bump_version()
            if self._logger.isEnabledFor(logging.DEBUG):
                self._logger.debug(
                    "HWM advanced for %s: %d -> %d",
                    self.topic_partition,
                    old_hwm,
                    new_hwm,
                )

    def get_gaps(self) -> list[OffsetRange]:
        """
        완료되지 않은 오프셋 범위를 반환합니다.

        Returns:
            list[OffsetRange]: 완료되지 않은 오프셋 범위 리스트
        """
        cache_key = (
            self.last_committed_offset,
            self.last_fetched_offset,
            self._state_version,
        )
        cached = self._gaps_cache.get(cache_key)
        if cached is not None:
            return cached

        gaps: list[OffsetRange] = []

        start_check_offset = self.last_committed_offset + 1
        end_check_offset = self.last_fetched_offset

        if start_check_offset > end_check_offset:
            self._blocking_offset_timestamps.clear()
            return []

        current = start_check_offset
        for completed in self.completed_offsets.irange(
            start_check_offset, end_check_offset
        ):
            if completed > current:
                gaps.append(OffsetRange(start=current, end=completed - 1))
            current = completed + 1

        if current <= end_check_offset:
            gaps.append(OffsetRange(start=current, end=end_check_offset))

        if not gaps:
            self._blocking_offset_timestamps.clear()
            return []

        if cache_key == self._last_gap_key:
            self._repeat_gap_count += 1
            if self._repeat_gap_count % 20000 == 0 and self._logger.isEnabledFor(
                logging.WARNING
            ):
                self._logger.warning(
                    "Repeated gap pattern %s count=%d", gaps, self._repeat_gap_count
                )
        else:
            self._last_gap_key = cache_key
            self._repeat_gap_count = 0

        current_blocking_offsets_set: Set[int] = set()
        for gap in gaps:
            current_blocking_offsets_set.update(range(gap.start, gap.end + 1))

        for offset in current_blocking_offsets_set:
            if offset not in self._blocking_offset_timestamps:
                self._blocking_offset_timestamps[offset] = time.time()

        offsets_to_remove = [
            offset
            for offset in self._blocking_offset_timestamps
            if offset not in current_blocking_offsets_set
        ]
        for offset in offsets_to_remove:
            del self._blocking_offset_timestamps[offset]

        self._gaps_cache.clear()
        self._gaps_cache[cache_key] = gaps
        return gaps

    def update_last_fetched_offset(self, offset: int) -> None:
        """
        Updates the last fetched offset.
        마지막으로 가져온 오프셋을 갱신합니다.

        Args:
            offset (int): 갱신할 오프셋
        Returns:
            None
        """
        if offset > self.last_fetched_offset:
            self.last_fetched_offset = offset
            self._bump_version()

    def get_blocking_offset_durations(self) -> Dict[int, float]:
        """
        Returns the duration (in seconds) that each blocking offset has been blocking.
        각 블로킹 오프셋이 블로킹된 시간을 초 단위로 반환합니다.

        Returns:
            Dict[int, float]: 오프셋과 해당 블로킹 시간(초) 딕셔너리
        """
        durations: Dict[int, float] = {}
        current_time = time.time()
        for offset, start_time in self._blocking_offset_timestamps.items():
            durations[offset] = current_time - start_time
        return durations
