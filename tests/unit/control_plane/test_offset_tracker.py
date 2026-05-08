from unittest.mock import patch

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
    # Given: inputs for `offset tracker initialization` are prepared.
    # When: the offset tracker code path is exercised.
    tracker = OffsetTracker(
        topic_partition=topic_partition, starting_offset=10, max_revoke_grace_ms=0
    )
    # Then: the expected `offset tracker initialization` behavior is asserted.
    assert tracker.topic_partition == topic_partition
    assert tracker.last_committed_offset == 9  # starting_offset - 1
    assert tracker.completed_offsets == set()
    assert tracker.in_flight_offsets == set()
    assert tracker.last_fetched_offset == 9


def test_mark_complete_single_offset(offset_tracker):
    # Given: inputs for `mark complete single offset` are prepared.
    # When: the offset tracker code path is exercised.
    offset_tracker.mark_complete(offset=0)
    # Then: the expected `mark complete single offset` behavior is asserted.
    assert offset_tracker.completed_offsets == {0}


def test_advance_high_water_mark_contiguous_offsets(offset_tracker):
    # Given: inputs for `advance high water mark contiguous offsets` are prepared.
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=1)
    # When: the offset tracker code path is exercised.
    offset_tracker.advance_high_water_mark()
    # Then: the expected `advance high water mark contiguous offsets` behavior is asserted.
    assert offset_tracker.last_committed_offset == 1
    assert offset_tracker.completed_offsets == set()  # Should be empty after committing


def test_get_committable_high_water_mark_clamps_by_min_inflight(offset_tracker):
    # Given: inputs for `get committable high water mark clamps by min...` are prepared.
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=1)
    offset_tracker.mark_complete(offset=2)

    # When: the offset tracker code path is exercised.
    # Then: the expected `get committable high water mark clamps by min...` behavior is asserted.
    assert offset_tracker.get_committable_high_water_mark() == 2
    assert offset_tracker.get_committable_high_water_mark(min_inflight_offset=2) == 1


def test_commit_through_updates_hwm_and_retains_future_offsets(offset_tracker):
    # Given: inputs for `commit through updates hwm and retains future...` are prepared.
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=1)
    offset_tracker.mark_complete(offset=3)

    # When: the offset tracker code path is exercised.
    offset_tracker.commit_through(1)

    # Then: the expected `commit through updates hwm and retains future...` behavior is asserted.
    assert offset_tracker.last_committed_offset == 1
    assert offset_tracker.completed_offsets == {3}


def test_advance_high_water_mark_with_gap(offset_tracker):
    # Given: inputs for `advance high water mark with gap` are prepared.
    offset_tracker.mark_complete(offset=0)
    offset_tracker.mark_complete(offset=2)  # Gap at 1
    # When: the offset tracker code path is exercised.
    offset_tracker.advance_high_water_mark()
    # Then: the expected `advance high water mark with gap` behavior is asserted.
    assert offset_tracker.last_committed_offset == 0  # Should only commit up to 0
    assert offset_tracker.completed_offsets == {2}  # Only 2 should remain


def test_mark_complete_offset_already_committed(offset_tracker):
    # Given: inputs for `mark complete offset already committed` are prepared.
    offset_tracker.mark_complete(offset=0)
    offset_tracker.advance_high_water_mark()  # Commits 0
    offset_tracker.mark_complete(offset=0)  # Mark complete again
    # When: the offset tracker code path is exercised.
    # Then: the expected `mark complete offset already committed` behavior is asserted.
    assert (
        offset_tracker.completed_offsets == set()
    )  # Should be empty as 0 is committed


def test_is_completed_uncommitted_identifies_restored_sparse_offset(topic_partition):
    # Given: inputs for `is completed uncommitted identifies restored...` are prepared.
    tracker = OffsetTracker(
        topic_partition=topic_partition,
        starting_offset=4,
        max_revoke_grace_ms=0,
        initial_completed_offsets={4, 6, 7},
    )
    tracker.rehydrate_assignment_state(
        last_committed_offset=3,
        last_fetched_offset=7,
    )

    # When: the offset tracker code path is exercised.
    # Then: the expected `is completed uncommitted identifies restored...` behavior is asserted.
    assert tracker.is_completed_uncommitted(3) is False
    assert tracker.is_completed_uncommitted(4) is True
    assert tracker.is_completed_uncommitted(5) is False
    assert tracker.is_completed_uncommitted(8) is False


def test_mark_complete_in_flight_offset(offset_tracker):
    # Simulate offset 5 being in-flight by fetching up to offset 5
    # Given: inputs for `mark complete in flight offset` are prepared.
    # When: the offset tracker code path is exercised.
    offset_tracker.update_last_fetched_offset(5)
    # Offset 5 is now in-flight (in range but not completed)
    # Then: the expected `mark complete in flight offset` behavior is asserted.
    assert 5 in offset_tracker.in_flight_offsets
    offset_tracker.mark_complete(offset=5)
    assert 5 not in offset_tracker.in_flight_offsets
    assert 5 in offset_tracker.completed_offsets


def test_get_gaps_no_gaps(offset_tracker):
    # Given: inputs for `get gaps no gaps` are prepared.
    offset_tracker.update_last_fetched_offset(2)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(1)
    offset_tracker.mark_complete(2)  # Mark offset 2 as complete
    offset_tracker.advance_high_water_mark()  # This should commit 0, 1, 2
    # When: the offset tracker code path is exercised.
    gaps = offset_tracker.get_gaps()
    # Then: the expected `get gaps no gaps` behavior is asserted.
    assert gaps == []


def test_get_gaps_with_single_gap(offset_tracker):
    # Given: inputs for `get gaps with single gap` are prepared.
    offset_tracker.update_last_fetched_offset(5)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(2)
    offset_tracker.advance_high_water_mark()
    gaps = offset_tracker.get_gaps()
    # When: the offset tracker code path is exercised.
    # Then: the expected `get gaps with single gap` behavior is asserted.
    assert len(gaps) == 2
    assert gaps[0] == OffsetRange(start=1, end=1)
    assert gaps[1] == OffsetRange(start=3, end=5)


def test_get_first_gap_head_updates_incrementally(offset_tracker):
    # Given: inputs for `get first gap head updates incrementally` are prepared.
    offset_tracker.update_last_fetched_offset(5)
    # When: the offset tracker code path is exercised.
    # Then: the expected `get first gap head updates incrementally` behavior is asserted.
    assert offset_tracker.get_first_gap_head() == 0

    offset_tracker.mark_complete(0)
    offset_tracker.advance_high_water_mark()
    assert offset_tracker.get_first_gap_head() == 1

    offset_tracker.mark_complete(2)
    assert offset_tracker.get_first_gap_head() == 1

    offset_tracker.mark_complete(1)
    assert offset_tracker.get_first_gap_head() == 3

    offset_tracker.mark_complete(3)
    offset_tracker.mark_complete(4)
    offset_tracker.mark_complete(5)
    offset_tracker.advance_high_water_mark()
    assert offset_tracker.get_first_gap_head() is None


def test_get_gaps_with_multiple_gaps(offset_tracker):
    # Given: inputs for `get gaps with multiple gaps` are prepared.
    offset_tracker.update_last_fetched_offset(10)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(2)
    offset_tracker.mark_complete(3)
    offset_tracker.mark_complete(5)
    offset_tracker.mark_complete(8)
    offset_tracker.advance_high_water_mark()
    gaps = offset_tracker.get_gaps()
    # When: the offset tracker code path is exercised.
    # Then: the expected `get gaps with multiple gaps` behavior is asserted.
    assert len(gaps) == 4
    assert gaps[0] == OffsetRange(start=1, end=1)
    assert gaps[1] == OffsetRange(start=4, end=4)
    assert gaps[2] == OffsetRange(start=6, end=7)
    assert gaps[3] == OffsetRange(start=9, end=10)


def test_get_gaps_after_all_committed(offset_tracker):
    # Given: inputs for `get gaps after all committed` are prepared.
    offset_tracker.update_last_fetched_offset(2)
    offset_tracker.mark_complete(0)
    offset_tracker.mark_complete(1)
    offset_tracker.advance_high_water_mark()
    offset_tracker.mark_complete(2)
    offset_tracker.advance_high_water_mark()
    # When: the offset tracker code path is exercised.
    gaps = offset_tracker.get_gaps()
    # Then: the expected `get gaps after all committed` behavior is asserted.
    assert gaps == []


def test_update_last_fetched_offset(offset_tracker):
    # Given: inputs for `update last fetched offset` are prepared.
    # When: the offset tracker code path is exercised.
    offset_tracker.update_last_fetched_offset(5)
    # Then: the expected `update last fetched offset` behavior is asserted.
    assert offset_tracker.last_fetched_offset == 5
    offset_tracker.update_last_fetched_offset(3)  # Should not go backwards
    assert offset_tracker.last_fetched_offset == 5


def test_get_gaps_initial_empty(offset_tracker):
    # No offsets fetched, no completed. Should be no gaps.
    # Given: inputs for `get gaps initial empty` are prepared.
    # When: the offset tracker code path is exercised.
    gaps = offset_tracker.get_gaps()
    # Then: the expected `get gaps initial empty` behavior is asserted.
    assert gaps == []


def test_get_gaps_only_fetched_no_completed(offset_tracker):
    # Offsets fetched, but none completed. Entire range should be a gap.
    # Given: inputs for `get gaps only fetched no completed` are prepared.
    offset_tracker.update_last_fetched_offset(5)
    gaps = offset_tracker.get_gaps()
    # When: the offset tracker code path is exercised.
    # Then: the expected `get gaps only fetched no completed` behavior is asserted.
    assert len(gaps) == 1
    assert gaps[0] == OffsetRange(start=0, end=5)


def test_get_gaps_complex_scenario_with_trailing_gap(offset_tracker):
    # Given: inputs for `get gaps complex scenario with trailing gap` are prepared.
    offset_tracker.update_last_fetched_offset(10)
    offset_tracker.mark_complete(1)
    offset_tracker.mark_complete(3)
    offset_tracker.mark_complete(4)
    offset_tracker.advance_high_water_mark()  # last_committed_offset remains -1 because 0 is not completed

    gaps = offset_tracker.get_gaps()
    # When: the offset tracker code path is exercised.
    # Then: the expected `get gaps complex scenario with trailing gap` behavior is asserted.
    assert len(gaps) == 3  # Corrected from 4
    assert gaps[0] == OffsetRange(start=0, end=0)  # Initial gap for 0
    assert gaps[1] == OffsetRange(start=2, end=2)  # Gap between 1 and 3
    assert gaps[2] == OffsetRange(
        start=5, end=10
    )  # Trailing gap after 4 to last fetched


def test_get_blocking_offset_durations(offset_tracker):
    # Given: inputs for `get blocking offset durations` are prepared.
    # When: the offset tracker code path is exercised.
    # Then: the expected `get blocking offset durations` behavior is asserted.
    with patch("time.time") as mock_time:
        mock_time.return_value = 1000.0
        offset_tracker.update_last_fetched_offset(5)
        offset_tracker.mark_complete(0)
        offset_tracker.mark_complete(2)
        offset_tracker.advance_high_water_mark()

        offset_tracker.get_gaps()
        mock_time.return_value = 1010.0

        durations = offset_tracker.get_blocking_offset_durations()
        assert set(durations) == {1, 3}
        assert pytest.approx(durations[1]) == 10.0
        assert pytest.approx(durations[3]) == 10.0

        offset_tracker.mark_complete(1)
        offset_tracker.advance_high_water_mark()

        mock_time.return_value = 1015.0

        offset_tracker.get_gaps()

        durations = offset_tracker.get_blocking_offset_durations()
        assert set(durations) == {3}
        assert pytest.approx(durations[3]) == 15.0


def test_get_blocking_offset_durations_tracks_gap_heads_only(offset_tracker):
    # Given: inputs for `get blocking offset durations tracks gap head...` are prepared.
    # When: the offset tracker code path is exercised.
    # Then: the expected `get blocking offset durations tracks gap head...` behavior is asserted.
    with patch("time.time") as mock_time:
        mock_time.return_value = 2000.0
        offset_tracker.update_last_fetched_offset(1000)

        offset_tracker.get_gaps()
        mock_time.return_value = 2010.0

        assert set(offset_tracker.get_blocking_offset_durations()) == {0}
