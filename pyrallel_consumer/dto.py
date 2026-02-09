from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional


# --- Completion ---
class CompletionStatus(Enum):
    """_summary
    완료 상태를 나타내는 열거형입니다.

    Attributes:
        SUCCESS (str): 성공 상태
        FAILURE (str): 실패 상태
    """

    SUCCESS = "success"
    FAILURE = "failure"


@dataclass(frozen=True)
class TopicPartition:
    """
    토픽 파티션에 대한 정보입니다

    Attributes:
        topic (str): 토픽 이름
        partition (int): 파티션 번호
    """

    topic: str
    partition: int


@dataclass(frozen=True)
class CompletionEvent:
    """
    완료 이벤트에 대한 정보입니다

    Attributes:
        id (str): 완료된 작업 항목의 고유 ID
        tp (TopicPartition): 토픽 파티션 정보
        offset (int): 완료된 오프셋
        epoch (int): 처리 에포크
        status (CompletionStatus): 완료 상태
        error (Optional[str]): 오류 메시지 (실패 시)
    """

    id: str

    tp: TopicPartition
    offset: int
    epoch: int
    status: CompletionStatus
    error: Optional[str]


@dataclass(frozen=True)
class WorkItem:
    """
    WorkManager에서 관리하는 단일 작업 항목입니다.

    Attributes:
        id (str): 작업 항목의 고유 ID
        tp (TopicPartition): 토픽 파티션 정보
        offset (int): 작업 항목의 오프셋
        epoch (int): 처리 에포크(작업 항목이 속한 시점의 파티션 소유권 세대 번호)
        key (Any): 가상 파티셔닝을 위한 메시지 키
        payload (Any): 실제 메시지 페이로드
    """

    id: str
    tp: TopicPartition
    offset: int
    epoch: int
    key: Any  # Message key for virtual partitioning
    payload: Any  # The actual message payload


# --- Process Execution ---
@dataclass(frozen=True)
class ProcessTask:
    """
    프로세스 작업에 대한 정보입니다

    Attributes:
        topic (str): 토픽 이름
        partition (int): 파티션 번호
        offsets (list[int]): 마이크로 배치 오프셋들
        payload (bytes): orjson.dumps(batch)
        epoch (int): 처리 에포크
        context (dict[str, str]): tracing / logging
    """

    topic: str
    partition: int
    offsets: list[int]  # micro-batch offsets
    payload: bytes  # orjson.dumps(batch)
    epoch: int
    context: dict[str, str]  # tracing / logging


# --- Offset/Metadata Management ---
@dataclass(frozen=True)
class OffsetRange:
    """
    오프셋 범위에 대한 정보입니다

    Attributes:
        start (int): 시작 오프셋
        end (int): 종료 오프셋
    """

    start: int
    end: int


@dataclass(frozen=True)
class EngineMetrics:
    """
    ExecutionEngine에서 노출하는 메트릭에 대한 정보입니다.

    Attributes:
        in_flight_count (int): 현재 처리 중인 메시지 수
    """

    in_flight_count: int
    # Potentially other metrics like queue_sizes, error_rates, etc.


@dataclass(frozen=True)
class PartitionMetrics:
    """
    개별 파티션에 대한 메트릭 정보입니다.

    Attributes:
        tp (TopicPartition): 토픽 파티션 정보
        true_lag (int): 실제 지연 (Last Fetched - Last Committed)
        gap_count (int): 커밋되지 않은 완료된 오프셋 그룹(Gap)의 수
        blocking_offset (Optional[int]): 현재 커밋을 막고 있는 가장 낮은 오프셋
        blocking_duration_sec (Optional[float]): Blocking Offset이 지속된 시간 (초)
        queued_count (int): 가상 파티션 큐에 대기 중인 메시지 수
    """

    tp: TopicPartition
    true_lag: int
    gap_count: int
    blocking_offset: Optional[int]
    blocking_duration_sec: Optional[float]
    queued_count: int


@dataclass(frozen=True)
class SystemMetrics:
    """
    시스템 전체에 대한 메트릭 정보입니다.

    Attributes:
        total_in_flight (int): 시스템 전체에서 처리 중인 메시지 수
        is_paused (bool): 백프레셔로 인한 컨슈머 일시 정지 여부
        partitions (List[PartitionMetrics]): 각 파티션별 메트릭 목록
    """

    total_in_flight: int
    is_paused: bool
    partitions: list[PartitionMetrics]
