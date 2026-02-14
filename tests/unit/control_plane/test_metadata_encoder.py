import pytest

from pyrallel_consumer.control_plane.metadata_encoder import MetadataEncoder


@pytest.fixture
def metadata_encoder():
    return MetadataEncoder()


def test_rle_encode_empty_offsets(metadata_encoder):
    assert metadata_encoder._rle_encode_offsets(set()) == ""


def test_rle_encode_single_offset(metadata_encoder):
    assert metadata_encoder._rle_encode_offsets({0}) == "0"


def test_rle_encode_contiguous_offsets(metadata_encoder):
    assert metadata_encoder._rle_encode_offsets({0, 1, 2, 3}) == "0-3"


def test_rle_encode_discontiguous_offsets(metadata_encoder):
    assert metadata_encoder._rle_encode_offsets({0, 2, 3, 5, 6, 7}) == "0,2-3,5-7"


def test_rle_encode_mixed_offsets(metadata_encoder):
    assert metadata_encoder._rle_encode_offsets({10, 11, 13, 15}) == "10-11,13,15"


def test_bitset_encode_empty_offsets(metadata_encoder):
    assert metadata_encoder._bitset_encode_offsets(set(), 0) == "0:"


def test_bitset_encode_single_offset(metadata_encoder):
    assert metadata_encoder._bitset_encode_offsets({0}, 0) == "0:1"
    assert metadata_encoder._bitset_encode_offsets({5}, 0) == "0:000001"


def test_bitset_encode_contiguous_offsets(metadata_encoder):
    assert metadata_encoder._bitset_encode_offsets({0, 1, 2, 3}, 0) == "0:1111"


def test_bitset_encode_discontiguous_offsets(metadata_encoder):
    assert metadata_encoder._bitset_encode_offsets({0, 2, 3, 5}, 0) == "0:101101"


def test_bitset_encode_with_different_base_offset(metadata_encoder):
    assert metadata_encoder._bitset_encode_offsets({10, 11, 13, 15}, 10) == "10:110101"


def test_encode_metadata_rle_shorter(metadata_encoder):
    offsets = {0, 1, 2, 3}
    base_offset = 0
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    assert encoded == "R0-3"


def test_encode_metadata_bitset_shorter(metadata_encoder):
    offsets = {0, 5, 10}
    base_offset = 0
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    assert encoded == "R0,5,10"


# --- Decode Tests ---


def test_rle_decode_empty_string(metadata_encoder):
    assert metadata_encoder._rle_decode_offsets("") == set()


def test_rle_decode_single_offset(metadata_encoder):
    assert metadata_encoder._rle_decode_offsets("0") == {0}


def test_rle_decode_contiguous_offsets(metadata_encoder):
    assert metadata_encoder._rle_decode_offsets("0-3") == {0, 1, 2, 3}


def test_rle_decode_discontiguous_offsets(metadata_encoder):
    assert metadata_encoder._rle_decode_offsets("0,2-3,5-7") == {0, 2, 3, 5, 6, 7}


def test_rle_decode_mixed_offsets(metadata_encoder):
    assert metadata_encoder._rle_decode_offsets("10-11,13,15") == {10, 11, 13, 15}


def test_bitset_decode_empty_string(metadata_encoder):
    assert metadata_encoder._bitset_decode_offsets("0:") == set()


def test_bitset_decode_single_offset(metadata_encoder):
    assert metadata_encoder._bitset_decode_offsets("0:1") == {0}
    assert metadata_encoder._bitset_decode_offsets("0:000001") == {5}


def test_bitset_decode_contiguous_offsets(metadata_encoder):
    assert metadata_encoder._bitset_decode_offsets("0:1111") == {0, 1, 2, 3}


def test_bitset_decode_discontiguous_offsets(metadata_encoder):
    assert metadata_encoder._bitset_decode_offsets("0:101101") == {0, 2, 3, 5}


def test_bitset_decode_with_different_base_offset(metadata_encoder):
    assert metadata_encoder._bitset_decode_offsets("10:110101") == {10, 11, 13, 15}


def test_decode_metadata_empty(metadata_encoder):
    assert metadata_encoder.decode_metadata("") == set()


def test_decode_metadata_rle(metadata_encoder):
    assert metadata_encoder.decode_metadata("R10-11,13,15") == {10, 11, 13, 15}


def test_decode_metadata_bitset(metadata_encoder):
    assert metadata_encoder.decode_metadata("B10:110101") == {10, 11, 13, 15}


def test_decode_metadata_unknown_type(metadata_encoder):
    assert metadata_encoder.decode_metadata("X10:110101") == set()


def test_roundtrip_encoding_decoding(metadata_encoder):
    offsets = {100, 101, 102, 105, 106, 110}
    base_offset = 99
    encoded = metadata_encoder.encode_metadata(offsets, base_offset)
    decoded = metadata_encoder.decode_metadata(encoded)
    assert decoded == offsets
