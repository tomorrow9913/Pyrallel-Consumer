from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

WORK_ITEM_POISON_KEY_UNSET = object()


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


class OrderingMode(Enum):
    """Ordering guarantees supported by the consumer."""

    KEY_HASH = "key_hash"
    PARTITION = "partition"
    UNORDERED = "unordered"


class ExecutionMode(Enum):
    ASYNC = "async"
    PROCESS = "process"


class DLQPayloadMode(str, Enum):
    FULL = "full"
    METADATA_ONLY = "metadata_only"


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
        attempt (int): 시도 횟수 (1-based)
    """

    id: str

    tp: TopicPartition
    offset: int
    epoch: int
    status: CompletionStatus
    error: Optional[str]
    attempt: int


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
        requeue_attempts (int): process worker 재큐 시도 횟수
        poison_key (Any): poison-message circuit 식별용 원본 메시지 키
    """

    id: str
    tp: TopicPartition
    offset: int
    epoch: int
    key: Any  # Message key for virtual partitioning
    payload: Any  # The actual message payload
    requeue_attempts: int = 0
    poison_key: Any = WORK_ITEM_POISON_KEY_UNSET


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
class ProcessBatchMetrics:
    """
    ProcessExecutionEngine micro-batch runtime metrics snapshot.

    Attributes:
        size_flush_count (int): Number of size-triggered flushes
        timer_flush_count (int): Number of timer-triggered flushes
        close_flush_count (int): Number of close-triggered flushes
        total_flushed_items (int): Total items flushed across all batches
        last_flush_size (int): Size of the most recent flushed batch
        last_flush_wait_seconds (float): Wait time of the most recent flushed batch
        buffered_items (int): Number of currently buffered items
        buffered_age_seconds (float): Age of current buffer since first item
        demand_flush_count (int): Number of demand-triggered flushes
        last_main_to_worker_ipc_seconds (float): Most recent main-to-worker IPC time
        avg_main_to_worker_ipc_seconds (float): Average main-to-worker IPC time
        last_worker_exec_seconds (float): Most recent worker execution time
        avg_worker_exec_seconds (float): Average worker execution time
        last_worker_to_main_ipc_seconds (float): Most recent worker-to-main IPC time
        avg_worker_to_main_ipc_seconds (float): Average worker-to-main IPC time
        transport_mode (str): Active process transport mode
        support_state (str): Support boundary classification for the active transport
        timer_flush_supported (bool): Whether timer-based flushing is supported
        demand_flush_supported (bool): Whether demand-based flushing is supported
        recycle_supported (bool): Whether recycle settings are supported
    """

    size_flush_count: int
    timer_flush_count: int
    close_flush_count: int
    total_flushed_items: int
    last_flush_size: int
    last_flush_wait_seconds: float
    buffered_items: int
    buffered_age_seconds: float
    demand_flush_count: int = 0
    last_main_to_worker_ipc_seconds: float = 0.0
    avg_main_to_worker_ipc_seconds: float = 0.0
    last_worker_exec_seconds: float = 0.0
    avg_worker_exec_seconds: float = 0.0
    last_worker_to_main_ipc_seconds: float = 0.0
    avg_worker_to_main_ipc_seconds: float = 0.0
    transport_mode: str = "shared_queue"
    support_state: str = "full"
    timer_flush_supported: bool = True
    demand_flush_supported: bool = True
    recycle_supported: bool = True


class ResourceSignalStatus(str, Enum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    FIRST_SAMPLE_PENDING = "first_sample_pending"


@dataclass(frozen=True)
class ResourceSignalSnapshot:
    """
    Host resource signal snapshot for future adaptive tuning decisions.

    Non-available states are intentionally fail-open: they must not constrain
    concurrency or backpressure decisions.
    """

    status: ResourceSignalStatus
    cpu_utilization: Optional[float] = None
    memory_utilization: Optional[float] = None
    sampled_at_monotonic_seconds: Optional[float] = None
    stale_after_seconds: Optional[float] = None

    @property
    def is_actionable_for_tuning(self) -> bool:
        return self.status == ResourceSignalStatus.AVAILABLE


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
        adaptive_backpressure: Optional[AdaptiveBackpressureSnapshot]: 백프레셔/적응형
            backpressure 제어 상태 스냅샷
        adaptive_concurrency: Optional[AdaptiveConcurrencyRuntimeSnapshot]:
            적응형 동시성 제어 상태 스냅샷
        process_batch_metrics (Optional[ProcessBatchMetrics]): process 모드 배치 메트릭
        resource_signal: Optional[ResourceSignalSnapshot]: 리소스 signal 상태
    """

    total_in_flight: int
    is_paused: bool
    partitions: list[PartitionMetrics]
    process_batch_metrics: Optional[ProcessBatchMetrics] = None
    resource_signal: Optional[ResourceSignalSnapshot] = None
    adaptive_backpressure: Optional[AdaptiveBackpressureSnapshot] = None
    adaptive_concurrency: Optional[AdaptiveConcurrencyRuntimeSnapshot] = None


@dataclass(frozen=True)
class QueueRuntimeSnapshot:
    total_in_flight: int
    total_queued: int
    max_in_flight: int
    is_paused: bool
    is_rebalancing: bool
    ordering_mode: OrderingMode
    configured_max_in_flight: Optional[int] = None


@dataclass(frozen=True)
class AdaptiveConcurrencyRuntimeSnapshot:
    configured_max_in_flight: int
    effective_max_in_flight: int
    min_in_flight: int
    scale_up_step: int
    scale_down_step: int
    cooldown_ms: int


@dataclass(frozen=True)
class AdaptiveBackpressureSnapshot:
    configured_max_in_flight: int
    effective_max_in_flight: int
    min_in_flight: int
    scale_up_step: int
    scale_down_step: int
    cooldown_ms: int
    lag_scale_up_threshold: int
    low_latency_threshold_ms: float
    high_latency_threshold_ms: float
    last_decision: str
    avg_completion_latency_seconds: Optional[float]


@dataclass(frozen=True)
class RetryPolicySnapshot:
    max_retries: int
    retry_backoff_ms: int
    exponential_backoff: bool
    max_retry_backoff_ms: int
    retry_jitter_ms: int


@dataclass(frozen=True)
class DlqRuntimeSnapshot:
    enabled: bool
    topic: str
    payload_mode: DLQPayloadMode
    message_cache_size_bytes: int
    message_cache_entry_count: int


@dataclass(frozen=True)
class PoisonMessageRuntimeSnapshot:
    enabled: bool
    failure_threshold: int
    cooldown_ms: int
    open_circuit_count: int


@dataclass(frozen=True)
class PartitionRuntimeSnapshot:
    tp: TopicPartition
    current_epoch: int
    last_committed_offset: int
    last_fetched_offset: int
    true_lag: int
    gaps: list[OffsetRange]
    blocking_offset: Optional[int]
    blocking_duration_sec: Optional[float]
    queued_count: int
    in_flight_count: int
    min_in_flight_offset: Optional[int]


@dataclass(frozen=True)
class RuntimeSnapshot:
    queue: QueueRuntimeSnapshot
    retry: RetryPolicySnapshot
    dlq: DlqRuntimeSnapshot
    partitions: list[PartitionRuntimeSnapshot]
    adaptive_backpressure: Optional[AdaptiveBackpressureSnapshot] = None
    adaptive_concurrency: Optional[AdaptiveConcurrencyRuntimeSnapshot] = None
    process_batch_metrics: Optional[ProcessBatchMetrics] = None
    poison_message: Optional[PoisonMessageRuntimeSnapshot] = None
