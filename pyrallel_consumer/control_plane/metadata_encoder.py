import logging
from typing import Set

logger = logging.getLogger(__name__)


class MetadataEncoder:
    """
    MetadataEncoder는 오프셋 집합을 효율적으로 인코딩하고 디코딩하는 기능을 제공합니다.

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
        ex) {1,2,3,5} with base_offset 1 -> "1:17" (hex)

        Args:
            offsets (Set[int]): 인코딩할 오프셋 집합
            base_offset (int): 기준 오프셋

        Returns:
            str: bitset 인코딩 문자열 (hex payload)
        """
        if not offsets:
            return f"{base_offset}:"

        relative_offsets = sorted(
            [offset - base_offset for offset in offsets if offset >= base_offset]
        )
        if not relative_offsets:
            return f"{base_offset}:"

        max_relative_offset = relative_offsets[-1]
        bit_length = max_relative_offset + 1
        byte_length = (bit_length + 7) // 8
        if byte_length > self.max_metadata_size:
            return ""

        bit_bytes = bytearray(byte_length)
        for rel_offset in relative_offsets:
            byte_index = rel_offset // 8
            bit_index = rel_offset % 8
            bit_bytes[byte_index] |= 1 << bit_index

        bit_hex = bit_bytes.hex()
        return f"{base_offset}:{bit_hex}"

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

        rle_payload = f"R{rle_encoded}"
        bitset_payload = f"B{bitset_encoded}" if bitset_encoded != "" else None

        if len(rle_payload) > self.max_metadata_size and (
            bitset_payload is None or len(bitset_payload) > self.max_metadata_size
        ):
            return "O"
        if len(rle_payload) > self.max_metadata_size:
            return bitset_payload or "O"
        if bitset_payload is None:
            return rle_payload
        if len(bitset_payload) > self.max_metadata_size:
            return rle_payload

        if len(rle_payload) <= len(bitset_payload):
            return rle_payload
        return bitset_payload

    def _rle_decode_offsets(self, encoded_str: str) -> Set[int]:
        """
        Run-length encoded 문자열을 오프셋 집합으로 디코딩합니다.
        ex) "1-3,5-6,8" -> {1,2,3,5,6,8}
        """
        offsets: Set[int] = set()
        if not encoded_str:
            return offsets

        parts = encoded_str.split(",")
        for part in parts:
            if "-" in part:
                start, end = map(int, part.split("-"))
                offsets.update(range(start, end + 1))
            else:
                offsets.add(int(part))
        return offsets

    def _bitset_decode_offsets(self, encoded_str: str) -> Set[int]:
        """
        Bitset으로 인코딩된 문자열을 오프셋 집합으로 디코딩합니다.
        ex) "1:17" -> {1,2,3,5}
        """
        offsets: Set[int] = set()
        if ":" not in encoded_str:
            return offsets

        base_offset_str, bit_hex = encoded_str.split(":", 1)
        try:
            base_offset = int(base_offset_str)
            bit_bytes = bytes.fromhex(bit_hex) if bit_hex else b""
        except ValueError as exc:
            logger.warning("Failed to decode metadata: %s", exc)
            return offsets

        for byte_index, byte in enumerate(bit_bytes):
            if byte == 0:
                continue
            for bit_index in range(8):
                if byte & (1 << bit_index):
                    offsets.add(base_offset + byte_index * 8 + bit_index)
        return offsets

    def decode_metadata(self, metadata: str) -> Set[int]:
        """
        인코딩된 메타데이터 문자열을 오프셋 집합으로 디코딩합니다.
        """
        if not metadata:
            return set()

        try:
            encoding_type = metadata[0]
            encoded_payload = metadata[1:]

            if encoding_type == "R":
                return self._rle_decode_offsets(encoded_payload)
            if encoding_type == "B":
                return self._bitset_decode_offsets(encoded_payload)
            if encoding_type == "O":
                return set()
            return set()
        except Exception as exc:
            logger.warning("Failed to decode metadata: %s", exc)
            return set()
