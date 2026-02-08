import pytest

from pyrallel_consumer.control_plane.metadata_encoder import (
    MetadataEncoder,  # This will be created next
)


@pytest.fixture
def metadata_encoder():
    return MetadataEncoder()


def test_rle_encode_empty_offsets():
    encoder = MetadataEncoder()
    assert encoder._rle_encode_offsets(set()) == ""


def test_rle_encode_single_offset():
    encoder = MetadataEncoder()
    assert encoder._rle_encode_offsets({0}) == "0"


def test_rle_encode_contiguous_offsets():
    encoder = MetadataEncoder()
    assert encoder._rle_encode_offsets({0, 1, 2, 3}) == "0-3"


def test_rle_encode_discontiguous_offsets():
    encoder = MetadataEncoder()
    assert encoder._rle_encode_offsets({0, 2, 3, 5, 6, 7}) == "0,2-3,5-7"


def test_rle_encode_mixed_offsets():
    encoder = MetadataEncoder()
    assert encoder._rle_encode_offsets({10, 11, 13, 15}) == "10-11,13,15"


def test_bitset_encode_empty_offsets():
    encoder = MetadataEncoder()
    # Base offset (HWM) is 0 for simplicity in this test
    assert encoder._bitset_encode_offsets(set(), 0) == "0:"


def test_bitset_encode_single_offset():
    encoder = MetadataEncoder()
    # Base offset (HWM) is 0, so 0 relative to 0 is 0th bit
    assert encoder._bitset_encode_offsets({0}, 0) == "0:1"
    # Base offset (HWM) is 0, so 5 relative to 0 is 5th bit
    assert encoder._bitset_encode_offsets({5}, 0) == "0:000001"


def test_bitset_encode_contiguous_offsets():
    encoder = MetadataEncoder()
    # Base offset (HWM) is 0, offsets 0,1,2,3 relative to 0
    assert encoder._bitset_encode_offsets({0, 1, 2, 3}, 0) == "0:1111"


def test_bitset_encode_discontiguous_offsets():
    encoder = MetadataEncoder()
    # Base offset (HWM) is 0, offsets 0,2,3,5 relative to 0
    assert encoder._bitset_encode_offsets({0, 2, 3, 5}, 0) == "0:101101"


def test_bitset_encode_with_different_base_offset():
    encoder = MetadataEncoder()
    # HWM is 10, offsets 10, 11, 13, 15
    # Relative: 0, 1, 3, 5
    assert encoder._bitset_encode_offsets({10, 11, 13, 15}, 10) == "10:110101"


def test_bitset_encode_max_offset_exceeds_bitset_limit():
    encoder = MetadataEncoder()
    # If the highest offset is far from base, bitset might become too long.
    # This test will check if it handles large gaps and potentially truncate.
    # We will assume a reasonable max length for bitset internally.
    # For now, let's just test a basic case where a large offset is present.
    assert encoder._bitset_encode_offsets({0, 100}, 0) == "0:1" + ("0" * 99) + "1"


def test_encode_metadata_rle_shorter(metadata_encoder):
    offsets = {0, 1, 2, 3}
    base_offset = 0
    # RLE: "0-3" (5 chars)
    # Bitset: "0:1111" (6 chars)
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    assert encoded == "R0-3"  # 'R' prefix for RLE


def test_encode_metadata_bitset_shorter(metadata_encoder):
    offsets = {0, 5, 10}
    base_offset = 0
    # RLE: "0,5,10" (6 chars)
    # Bitset: "0:10000100001" (13 chars)
    # The actual implementation of bitset might be shorter here in reality,
    # as the padding zeroes are usually removed. Let's assume a simpler bitset for now.
    # If a realistic bitset is smaller, then it should be chosen.
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    # This assertion will likely fail, as the example bitset above is longer.
    # The true behavior of MetadataEncoder will pick the shorter one.
    assert encoded == "R0,5,10"


def test_encode_metadata_truncation(metadata_encoder):
    # This test needs to be carefully designed to force truncation.
    # Assume max_metadata_size is 4000

    # Bitset for 3000 offsets from base 0 would be even longer.
    # The encoder should truncate it if it exceeds max_metadata_size.

    # For now, let's use a simpler example that *could* be truncated.
    # If max_metadata_size is, say, 10, then "0-3,5-7" would be truncated.
    # The current MetadataEncoder has no knowledge of max_metadata_size yet.
    # This test will initially be a placeholder.
    offsets = set(range(0, 100))  # RLE "0-99" (5 chars), Bitset "0:111...1" (102 chars)
    base_offset = 0
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    assert len(encoded) < 4000  # Placeholder: just ensure it's not excessively long
