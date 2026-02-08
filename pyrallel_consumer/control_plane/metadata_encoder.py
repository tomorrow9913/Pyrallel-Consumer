from typing import Set


class MetadataEncoder:
    """
    MetadataEncoder는 오프셋 집합을 효율적으로 인코딩하는 기능을 제공합니다.

    Attributes:
        max_metadata_size (int): 인코딩된 메타데이터의 최대 크기 (바이트 단위)
    """

    def __init__(self, max_metadata_size: int = 4000):
        """
        초기화 메서드입니다.

        Args:
            max_metadata_size (int, optional): 인코딩된 메타데이터의 최대 크기 (바이트 단위). Defaults to 4000.
        """
        self.max_metadata_size = max_metadata_size

    def _rle_encode_offsets(self, offsets: Set[int]) -> str:
        """
        오프셋 집합을 run-length encoding (RLE) 방식으로 인코딩합니다.
        ex) {1,2,3,5,6,8} -> "1-3,5-6,8"

        Args:
            offsets (Set[int]): 인코딩할 오프셋 집합

        Returns:
            str: run-length encoded 오프셋 문자열
        """
        if not offsets:
            return ""

        sorted_offsets = sorted(list(offsets))

        encoded_parts = []
        if not sorted_offsets:
            return ""

        current_run_start = sorted_offsets[0]
        current_run_end = sorted_offsets[0]

        for i in range(1, len(sorted_offsets)):
            if sorted_offsets[i] == current_run_end + 1:
                current_run_end = sorted_offsets[i]
            else:
                if current_run_start == current_run_end:
                    encoded_parts.append(str(current_run_start))
                else:
                    encoded_parts.append(f"{current_run_start}-{current_run_end}")
                current_run_start = sorted_offsets[i]
                current_run_end = sorted_offsets[i]

        # Add the last run
        if current_run_start == current_run_end:
            encoded_parts.append(str(current_run_start))
        else:
            encoded_parts.append(f"{current_run_start}-{current_run_end}")

        return ",".join(encoded_parts)

    def _bitset_encode_offsets(self, offsets: Set[int], base_offset: int) -> str:
        """bitset으로 인코딩된 오프셋을 반환합니다.
        ex) {1,2,3,5} with base_offset 1 -> "1:11101"

        Args:
            offsets (Set[int]): 인코딩할 오프셋 집합
            base_offset (int): 기준 오프셋

        Returns:
            str: bitset 인코딩 문자열
        """
        if not offsets:
            return f"{base_offset}:"

        relative_offsets = sorted(
            [offset - base_offset for offset in offsets if offset >= base_offset]
        )
        if not relative_offsets:
            return f"{base_offset}:"

        max_relative_offset = relative_offsets[-1]

        # Determine a reasonable maximum length for the bitset to avoid excessively long strings
        # A common practice is to limit the range, e.g., 256 or 512 bits.
        # For simplicity, let's keep it somewhat arbitrary for now, focusing on correctness.
        # Max relative offset should ideally not be too large for bitset to be efficient.
        # If max_relative_offset is too big, bitset might be longer than RLE.
        # Max 4KB size limit means bitset itself should not exceed 4KB.
        # A bitset representation like "B_offset:bits" where bits are '0' or '1'
        # will have length approximately (len(str(base_offset)) + 1 + max_relative_offset + 1)
        # We need to ensure that max_relative_offset doesn't make this too long.

        # Simple heuristic: If max_relative_offset is very large, bitset is likely inefficient.
        # We could add a threshold here, e.g., if max_relative_offset > 500, consider it too big.

        bit_string_length = max_relative_offset + 1
        if (
            bit_string_length > self.max_metadata_size * 8
        ):  # Rough estimate, as it includes base_offset string
            # If the bitset would be extremely long, it's not efficient.
            # This is a simplification; a real implementation would truncate intelligently.
            return f"{base_offset}:"  # Return empty bitset if too large to be practical

        bit_array = ["0"] * (max_relative_offset + 1)
        for rel_offset in relative_offsets:
            bit_array[rel_offset] = "1"

        return f"{base_offset}:{''.join(bit_array)}"

    def encode_metadata(self, offsets: Set[int], base_offset: int) -> str:
        """
        오프셋 집합을 RLE 또는 Bitset 방식 중 짧은 것을 인코딩하여 반환합니다.

        Args:
            offsets (Set[int]): 인코딩할 오프셋 집합
            base_offset (int): 기준 오프셋

        Returns:
            str: 인코딩된 메타데이터 문자열
        """
        rle_encoded = self._rle_encode_offsets(offsets)
        bitset_encoded = self._bitset_encode_offsets(offsets, base_offset)

        # Prepend 'R' or 'B' for type identification
        rle_payload = f"R{rle_encoded}"
        bitset_payload = f"B{bitset_encoded}"

        # Truncation Logic (simplified for now - will be refined)
        # This part ensures that the output doesn't exceed max_metadata_size.
        # For now, it's a simple check and might not be fully compliant with
        # removing oldest offsets first.

        if (
            len(rle_payload) > self.max_metadata_size
            and len(bitset_payload) > self.max_metadata_size
        ):
            # Both are too large, return empty or raise error depending on policy
            return ""  # Or handle error
        elif len(rle_payload) > self.max_metadata_size:
            return bitset_payload  # RLE too large, use bitset
        elif len(bitset_payload) > self.max_metadata_size:
            return rle_payload  # Bitset too large, use RLE

        # Return the shorter of the two
        if len(rle_payload) <= len(bitset_payload):
            return rle_payload
        else:
            return bitset_payload
