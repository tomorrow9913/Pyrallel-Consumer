import time
from typing import Dict, Set

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
    ):
        """
        초기화 메서드입니다.

        Args:
            topic_partition (TopicPartition): 토픽 파티션 정보
            starting_offset (int): 시작 오프셋
            max_revoke_grace_ms (int): 최대 리보크 유예 시간 (밀리초)
        """
        self.topic_partition = topic_partition
        self.last_committed_offset = starting_offset - 1
        self.completed_offsets: SortedSet[int] = SortedSet()
        self.in_flight_offsets: Set[int] = set()
        self.last_fetched_offset = starting_offset - 1
        self.max_revoke_grace_ms = max_revoke_grace_ms
        self.epoch = 0  # Initial epoch
        # To track when an offset first became blocking
        self._blocking_offset_timestamps: Dict[int, float] = {}

    def increment_epoch(self) -> None:
        """현재 epoch을 1 증가시킵니다."""
        self.epoch += 1

    def get_current_epoch(self) -> int:
        """현재 epoch을 반환합니다."""
        return self.epoch

    @property
    def in_flight_count(self) -> int:
        """
        Returns the number of messages currently in flight.
        현재 처리 중인 메시지 수를 반환합니다.

        Returns:
            int: 현재 처리 중인 메시지 수
        """
        return len(self.in_flight_offsets)

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
            self.completed_offsets.add(offset)
        if offset in self.in_flight_offsets:
            self.in_flight_offsets.remove(offset)

    def advance_high_water_mark(self) -> None:
        """
        Advances the last committed offset based on completed offsets.
        완료된 오프셋을 기준으로 마지막 커밋된 오프셋을 갱신합니다.

        Returns:
            None
        """
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

    def get_gaps(self) -> list[OffsetRange]:
        """
        완료되지 않은 오프셋 범위를 반환합니다.

        Returns:
            list[OffsetRange]: 완료되지 않은 오프셋 범위 리스트
        """
        gaps: list[OffsetRange] = []

        start_check_offset = self.last_committed_offset + 1
        end_check_offset = self.last_fetched_offset

        # If there are no offsets to check (e.g., last_committed_offset is already last_fetched_offset)
        if start_check_offset > end_check_offset:
            # Clear any stale blocking offset timestamps
            self._blocking_offset_timestamps.clear()
            return []

        # Collect all uncompleted offsets within the range [start_check_offset, end_check_offset]
        uncompleted_offsets = SortedSet()

        # Iterate through all possible offsets in the range
        for offset in range(start_check_offset, end_check_offset + 1):
            if offset not in self.completed_offsets:
                uncompleted_offsets.add(offset)

        if not uncompleted_offsets:
            # Clear any stale blocking offset timestamps
            self._blocking_offset_timestamps.clear()
            return []

        # Form contiguous OffsetRange objects from uncompleted_offsets
        current_gap_start = uncompleted_offsets[0]
        current_gap_end = uncompleted_offsets[0]

        for i in range(1, len(uncompleted_offsets)):
            if uncompleted_offsets[i] == current_gap_end + 1:
                current_gap_end = uncompleted_offsets[i]
            else:
                gaps.append(OffsetRange(start=current_gap_start, end=current_gap_end))
                current_gap_start = uncompleted_offsets[i]
                current_gap_end = uncompleted_offsets[i]

        gaps.append(OffsetRange(start=current_gap_start, end=current_gap_end))

        # Update blocking offset timestamps
        current_blocking_offsets_set = set()
        for gap in gaps:
            for offset in range(gap.start, gap.end + 1):
                current_blocking_offsets_set.add(offset)

        # Add new blocking offsets
        for offset in current_blocking_offsets_set:
            if offset not in self._blocking_offset_timestamps:
                self._blocking_offset_timestamps[offset] = time.time()

        # Remove no longer blocking offsets
        offsets_to_remove = [
            offset
            for offset in self._blocking_offset_timestamps
            if offset not in current_blocking_offsets_set
        ]
        for offset in offsets_to_remove:
            del self._blocking_offset_timestamps[offset]

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
