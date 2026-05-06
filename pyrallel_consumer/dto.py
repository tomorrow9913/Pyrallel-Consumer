from __future__ import annotations

from dataclasses import dataclass, field
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
    """Enumerate modes used by runtime data transfer."""

    ASYNC = "async"
    PROCESS = "process"


class DLQPayloadMode(str, Enum):
    """Enumerate modes used by runtime data transfer."""

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


@dataclass(frozen=True)
class RouteBatch:
    """Internal process transport route batch payload."""

    batch_id: str
    route_identity: tuple[Any, ...]
    worker_index: Optional[int]
    items: list[WorkItem]


@dataclass(frozen=True)
class BatchCompletion:
    """Internal process transport batch completion payload."""

    batch_id: str
    route_identity: tuple[Any, ...]
    results: list[CompletionEvent]


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
        transport_mode (str): Deprecated compatibility field; always worker_pipes.
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
    transport_mode: str = "worker_pipes"
    support_state: str = "bounded"
    timer_flush_supported: bool = False
    demand_flush_supported: bool = False
    recycle_supported: bool = False
    items_per_input_ipc: Optional[float] = None
    items_per_completion_ipc: Optional[float] = None
    route_batch_count: int = 0
    route_batch_item_count: int = 0
    route_batch_size_avg: Optional[float] = None
    route_batch_size_max: Optional[int] = None
    completion_item_payload_count: int = 0
    completion_batch_payload_count: int = 0


@dataclass(frozen=True)
class ProcessRuntimeDiagnostics:
    """
    Process-engine-specific runtime diagnostics envelope.
    """

    batch_metrics: ProcessBatchMetrics


@dataclass(frozen=True)
class EngineWorkerDiagnostics:
    """Engine-owned worker or lane capacity diagnostics."""

    total: int
    executing: int
    admitted: Optional[int] = None
    top_k_loads: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class EngineRuntimeDiagnostics:
    """
    Engine-agnostic runtime diagnostics envelope.
    """

    engine_type: str
    process: Optional[ProcessRuntimeDiagnostics] = None
    workers: Optional[EngineWorkerDiagnostics] = None


class PipelineStage(str, Enum):
    """Bounded pipeline stage names for the stable diagnostics sidecar.

    Slice 2A/2B WorkManager diagnostics observe only WorkManager-owned stages.
    Future-owned stages remain present for a stable sidecar shape but are
    marked with NOT_IMPLEMENTED support state until their owning slice populates
    them.
    """

    ACQUIRED = "acquired"
    BUFFERED = "buffered"
    QUEUED = "queued"
    DISPATCHED = "dispatched"
    EXECUTING = "executing"
    COMPLETED_UNSETTLED = "completed_unsettled"
    FAILED = "failed"
    DLQ = "dlq"


class PipelineBlockedReason(str, Enum):
    """Bounded logical blocked reasons for queued work diagnostics."""

    ORDERING_LOCK = "ordering_lock"
    ROUTE_LOCK = "route_lock"
    RETRY_DELAY = "retry_delay"
    FRONTIER_DEFERRED = "frontier_deferred"
    POISON_GUARD = "poison_guard"
    REBALANCING = "rebalancing"
    SHUTDOWN = "shutdown"


class PipelineDispatchCapacityReason(str, Enum):
    """Bounded dispatch-capacity reasons for eligible work diagnostics."""

    MAX_IN_FLIGHT = "max_in_flight"
    ADAPTIVE_LIMIT = "adaptive_limit"


class PipelineAdmissionReason(str, Enum):
    """Bounded execution admission reasons for eligible work diagnostics."""

    ENGINE_CAPACITY = "engine_capacity"
    WORKER_PIPE_FULL = "worker_pipe_full"
    WORKER_STARTING = "worker_starting"
    WORKER_DRAINING = "worker_draining"


class PipelineSettlementBlockerReason(str, Enum):
    """Bounded terminal settlement blocker reasons."""

    COMMIT_PENDING = "commit_pending"
    ACK_PENDING = "ack_pending"
    DELETE_PENDING = "delete_pending"
    ARCHIVE_PENDING = "archive_pending"
    DLQ_PUBLISH_PENDING = "dlq_publish_pending"
    ORDERED_CURSOR_GAP = "ordered_cursor_gap"
    UNKNOWN = "unknown"


class PipelineDiagnosticsSupportState(str, Enum):
    """Machine-readable support state for partial pipeline diagnostics."""

    SUPPORTED = "supported"
    UNAVAILABLE = "unavailable"
    NOT_IMPLEMENTED = "not_implemented"


class PipelineDiagnosticsScope(str, Enum):
    """Machine-readable scope for the stable diagnostics sidecar."""

    WORK_MANAGER_ONLY = "work_manager_only"
    COMBINED = "combined"
    COMBINED_INTERNAL = "combined"

    @classmethod
    def _missing_(cls, value: object) -> "PipelineDiagnosticsScope | None":
        """Normalize legacy serialized scope names to the canonical value."""
        if value == "combined_internal":
            return cls.COMBINED
        return None


class PipelineDiagnosticsSection(str, Enum):
    """Machine-readable sections of the stable diagnostics sidecar."""

    STAGES = "stages"
    BLOCKED = "blocked"
    SUBQUEUES = "subqueues"
    DISPATCH_CAPACITY = "dispatch_capacity"
    ADMISSION = "admission"
    WORKERS = "workers"
    SETTLEMENT = "settlement"
    POLL = "poll"


@dataclass(frozen=True)
class PipelinePollDiagnostics:
    """BrokerPoller-owned poll/acquire counters for pipeline diagnostics."""

    records_total: int = 0
    nonempty_polls_total: int = 0
    empty_polls_total: int = 0
    error_polls_total: int = 0
    completed_offset_skips_total: int = 0
    broker_kind: str = "kafka"
    support_state: (
        PipelineDiagnosticsSupportState
    ) = PipelineDiagnosticsSupportState.NOT_IMPLEMENTED


@dataclass(frozen=True)
class PipelineCount:
    """Count and optional age for a bounded pipeline diagnostic bucket."""

    count: int
    oldest_age_ms: Optional[int] = None


@dataclass(frozen=True)
class PipelineDispatchCapacityDiagnostics:
    """Snapshot of WorkManager dispatch-capacity pressure."""

    blocked_items: int
    reason: Optional[PipelineDispatchCapacityReason] = None
    oldest_age_ms: Optional[int] = None


@dataclass(frozen=True)
class PipelineAdmissionDiagnostics:
    """Snapshot of execution-engine admission pressure.

    In Slice 2A/2B this field is an unavailable placeholder: WorkManager does
    not inspect execution-engine private admission state, so `blocked_items=0`
    with `reason=None` must not be interpreted as observed engine capacity.
    """

    blocked_items: int
    reason: Optional[PipelineAdmissionReason] = None
    oldest_age_ms: Optional[int] = None
    support_state: (
        PipelineDiagnosticsSupportState
    ) = PipelineDiagnosticsSupportState.NOT_IMPLEMENTED


@dataclass(frozen=True)
class PipelineWorkerDiagnostics:
    """Snapshot of engine-owned worker or lane capacity state."""

    total: int
    executing: int
    admitted: Optional[int] = None
    top_k_loads: list[int] = field(default_factory=list)
    support_state: (
        PipelineDiagnosticsSupportState
    ) = PipelineDiagnosticsSupportState.NOT_IMPLEMENTED


@dataclass(frozen=True)
class PipelineSettlementDiagnostics:
    """Snapshot of broker-owned terminal settlement pressure."""

    completed_unsettled: int
    oldest_age_ms: Optional[int] = None
    blocker_reason: Optional[PipelineSettlementBlockerReason] = None
    support_state: (
        PipelineDiagnosticsSupportState
    ) = PipelineDiagnosticsSupportState.NOT_IMPLEMENTED


@dataclass(frozen=True)
class PipelineSubqueueDiagnostics:
    """Snapshot of WorkManager scheduling-unit queue topology."""

    total: int
    queued: int
    queued_items: int
    eligible_subqueues: int
    eligible_items: int
    blocked_subqueues: int
    blocked_items: int
    top_k_depths: list[int]


@dataclass(frozen=True)
class WorkManagerPipelineDiagnostics:
    """Stable broker-neutral pipeline diagnostics sidecar snapshot.

    The public sidecar keeps RuntimeSnapshot v1 unchanged while exposing bounded
    queue, eligibility, blocked, capacity, settlement, poll, and worker diagnostic
    projections. Unsupported sections use explicit support-state metadata instead
    of fake observed values.
    """

    stage_counts: dict[PipelineStage, PipelineCount]
    blocked_counts: dict[PipelineBlockedReason, PipelineCount]
    dispatch_capacity: PipelineDispatchCapacityDiagnostics
    admission: PipelineAdmissionDiagnostics
    workers: PipelineWorkerDiagnostics
    subqueues: PipelineSubqueueDiagnostics
    stage_support: dict[PipelineStage, PipelineDiagnosticsSupportState]
    section_support: dict[PipelineDiagnosticsSection, PipelineDiagnosticsSupportState]
    scope: PipelineDiagnosticsScope = PipelineDiagnosticsScope.WORK_MANAGER_ONLY
    settlement: PipelineSettlementDiagnostics = field(
        default_factory=lambda: PipelineSettlementDiagnostics(completed_unsettled=0)
    )
    poll: PipelinePollDiagnostics = field(default_factory=PipelinePollDiagnostics)


PipelineDiagnostics = WorkManagerPipelineDiagnostics
PipelineDiagnosticsSnapshot = WorkManagerPipelineDiagnostics


class ResourceSignalStatus(str, Enum):
    """Represent resource signal status data used by runtime data transfer."""

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
        """Return whether actionable for tuning holds for runtime data transfer."""
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
    completed_offset_skips_total: int = 0


@dataclass(frozen=True)
class QueueRuntimeSnapshot:
    """Capture runtime state for runtime data transfer."""

    total_in_flight: int
    total_queued: int
    max_in_flight: int
    is_paused: bool
    is_rebalancing: bool
    ordering_mode: OrderingMode
    configured_max_in_flight: Optional[int] = None


@dataclass(frozen=True)
class AdaptiveConcurrencyRuntimeSnapshot:
    """Capture runtime state for runtime data transfer."""

    configured_max_in_flight: int
    effective_max_in_flight: int
    min_in_flight: int
    scale_up_step: int
    scale_down_step: int
    cooldown_ms: int


@dataclass(frozen=True)
class AdaptiveBackpressureSnapshot:
    """Capture runtime state for runtime data transfer."""

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
    """Capture runtime state for runtime data transfer."""

    max_retries: int
    retry_backoff_ms: int
    exponential_backoff: bool
    max_retry_backoff_ms: int
    retry_jitter_ms: int


@dataclass(frozen=True)
class DlqRuntimeSnapshot:
    """Capture runtime state for runtime data transfer."""

    enabled: bool
    topic: str
    payload_mode: DLQPayloadMode
    message_cache_size_bytes: int
    message_cache_entry_count: int


@dataclass(frozen=True)
class PoisonMessageRuntimeSnapshot:
    """Capture runtime state for runtime data transfer."""

    enabled: bool
    failure_threshold: int
    cooldown_ms: int
    open_circuit_count: int


@dataclass(frozen=True)
class PartitionRuntimeSnapshot:
    """Capture runtime state for runtime data transfer."""

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
    """Capture runtime state for runtime data transfer."""

    queue: QueueRuntimeSnapshot
    retry: RetryPolicySnapshot
    dlq: DlqRuntimeSnapshot
    partitions: list[PartitionRuntimeSnapshot]
    adaptive_backpressure: Optional[AdaptiveBackpressureSnapshot] = None
    adaptive_concurrency: Optional[AdaptiveConcurrencyRuntimeSnapshot] = None
    process_batch_metrics: Optional[ProcessBatchMetrics] = None
    poison_message: Optional[PoisonMessageRuntimeSnapshot] = None
