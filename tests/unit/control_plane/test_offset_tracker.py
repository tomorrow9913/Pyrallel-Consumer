import pytest

from pyrallel_consumer.control_plane.offset_tracker import OffsetTracker
from pyrallel_consumer.dto import OffsetRange, TopicPartition


@pytest.fixture
def topic_partition():
    return TopicPartition(topic="test-topic", partition=0)


@pytest.fixture
def offset_tracker(topic_partition):
    return OffsetTracker(
        topic_partition=topic_partition, starting_offset=0, max_revoke_grace_ms=0
    )


def test_offset_tracker_initialization(topic_partition):
    tracker = OffsetTracker(
        topic_partition=topic_partition, starting_offset=10, max_revoke_grace_ms=0
    )
    assert tracker.topic_partition == topic_partition
    assert tracker.last_committed_offset == 9  # starting_offset - 1
    assert tracker.completed_offsets == set()
    assert tracker.in_flight_offsets == set()
    assert tracker.last_fetched_offset == 9


def test_mark_complete_single_offset(offset_tracker):
    offset_tracker.mark_complete(offset=0)
    assert offset_tracker.completed_offsets == {0}


def test_advance_high_water_mark_contiguous_offsets(offset_tracker):
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=1)
    offset_tracker.advance_high_water_mark()
    assert offset_tracker.last_committed_offset == 1
    assert offset_tracker.completed_offsets == set()  # Should be empty after committing


def test_advance_high_water_mark_with_gap(offset_tracker):
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=2)  # Gap at 1
    offset_tracker.advance_high_water_mark()
    assert offset_tracker.last_committed_offset == 0  # Should only commit up to 0
    assert offset_tracker.completed_offsets == {2}  # Only 2 should remain


def test_mark_complete_offset_already_committed(offset_tracker):
    offset_tracker.mark_complete(offset=0)
    offset_tracker.advance_high_water_mark()  # Commits 0
    offset_tracker.mark_complete(offset=0)  # Mark complete again
    assert (
        offset_tracker.completed_offsets == set()
    )  # Should be empty as 0 is committed


def test_mark_complete_in_flight_offset(offset_tracker):
    # Simulate an offset being in flight, then completed
    offset_tracker.in_flight_offsets.add(5)
    offset_tracker.mark_complete(offset=5)
    assert 5 not in offset_tracker.in_flight_offsets
    assert 5 in offset_tracker.completed_offsets


def test_get_gaps_no_gaps(offset_tracker):
    offset_tracker.update_last_fetched_offset(2)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(1)
    offset_tracker.mark_complete(2)  # Mark offset 2 as complete
    offset_tracker.advance_high_water_mark()  # This should commit 0, 1, 2
    gaps = offset_tracker.get_gaps()
    assert gaps == []


def test_get_gaps_with_single_gap(offset_tracker):
    offset_tracker.update_last_fetched_offset(5)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(2)
    offset_tracker.advance_high_water_mark()
    gaps = offset_tracker.get_gaps()
    assert len(gaps) == 2
    assert gaps[0] == OffsetRange(start=1, end=1)
    assert gaps[1] == OffsetRange(start=3, end=5)


def test_get_gaps_with_multiple_gaps(offset_tracker):
    offset_tracker.update_last_fetched_offset(10)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(2)
    offset_tracker.mark_complete(3)
    offset_tracker.mark_complete(5)
    offset_tracker.mark_complete(8)
    offset_tracker.advance_high_water_mark()
    gaps = offset_tracker.get_gaps()
    assert len(gaps) == 4
    assert gaps[0] == OffsetRange(start=1, end=1)
    assert gaps[1] == OffsetRange(start=4, end=4)
    assert gaps[2] == OffsetRange(start=6, end=7)
    assert gaps[3] == OffsetRange(start=9, end=10)


def test_get_gaps_after_all_committed(offset_tracker):
    offset_tracker.update_last_fetched_offset(2)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(1)
    offset_tracker.advance_high_water_mark()
    offset_tracker.mark_complete(2)
    offset_tracker.advance_high_water_mark()
    gaps = offset_tracker.get_gaps()
    assert gaps == []


def test_update_last_fetched_offset(offset_tracker):
    offset_tracker.update_last_fetched_offset(5)
    assert offset_tracker.last_fetched_offset == 5
    offset_tracker.update_last_fetched_offset(3)  # Should not go backwards
    assert offset_tracker.last_fetched_offset == 5


def test_get_gaps_initial_empty(offset_tracker):
    # No offsets fetched, no completed. Should be no gaps.
    gaps = offset_tracker.get_gaps()
    assert gaps == []


def test_get_gaps_only_fetched_no_completed(offset_tracker):
    # Offsets fetched, but none completed. Entire range should be a gap.
    offset_tracker.update_last_fetched_offset(5)
    gaps = offset_tracker.get_gaps()
    assert len(gaps) == 1
    assert gaps[0] == OffsetRange(start=0, end=5)


def test_get_gaps_complex_scenario_with_trailing_gap(offset_tracker):
    offset_tracker.update_last_fetched_offset(10)
    offset_tracker.mark_complete(1)
    offset_tracker.mark_complete(3)
    offset_tracker.mark_complete(4)
    offset_tracker.advance_high_water_mark()  # last_committed_offset remains -1 because 0 is not completed

    gaps = offset_tracker.get_gaps()
    assert len(gaps) == 3  # Corrected from 4
    assert gaps[0] == OffsetRange(start=0, end=0)  # Initial gap for 0
    assert gaps[1] == OffsetRange(start=2, end=2)  # Gap between 1 and 3
    assert gaps[2] == OffsetRange(
        start=5, end=10
    )  # Trailing gap after 4 to last fetched
