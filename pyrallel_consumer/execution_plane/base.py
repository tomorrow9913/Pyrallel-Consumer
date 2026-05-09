# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/base.py
# Role: Defines the abstract execution-engine contract used by the control plane.
# Extend here only when every execution engine must expose the same capability.
from abc import ABC, abstractmethod
from typing import List, Optional

from pyrallel_consumer.dto import (
    CompletionEvent,
    EngineRuntimeDiagnostics,
    ExecutionControlEvent,
    TopicPartition,
    WorkItem,
)


class BatchSubmitError(RuntimeError):
    """Raised when default batch submission fails after partial acceptance."""

    def __init__(self, accepted_count: int, original_error: Exception) -> None:
        super().__init__(str(original_error))
        self.accepted_count = accepted_count
        self.original_error = original_error


class BaseExecutionEngine(ABC):
    """Basic abstract class for execution engines.

    실행 엔진의 기본 추상 클래스입니다.

    Args:
        ABC (_type_): Abstract Base Class

    """

    @property
    def supports_ordered_route_batch(self) -> bool:
        """Return whether this engine can run ordered route batches safely."""
        return False

    @abstractmethod
    async def submit(self, work_item: WorkItem) -> None:
        """Submit a WorkItem to the execution engine for processing.

        작업 항목을 처리하기 위해 실행 엔진에 제출합니다.

        Args:
            work_item (WorkItem): 제출할 작업 항목

        """

    async def submit_batch(self, work_items: list[WorkItem]) -> None:
        """Submit WorkItems using the existing item-level engine contract.

        This default fallback preserves existing engine semantics while allowing
        the control plane to become batch-aware. Engines may override this method
        for transport-specific batch optimizations.

        Contract: implementations must either submit the batch atomically, where
        any failure means no item was accepted, or raise BatchSubmitError after
        partial acceptance with accepted_count set to the accepted prefix length.

        Args:
            work_items: Work items to submit in order.

        """
        accepted_count = 0
        for work_item in work_items:
            try:
                await self.submit(work_item)
            except Exception as exc:
                raise BatchSubmitError(accepted_count, exc) from exc
            accepted_count += 1

    @abstractmethod
    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """Poll for completed events from the execution engine.

        실행 엔진에서 완료된 이벤트를 폴링합니다.

        Args:
            batch_limit (int): 한 번의 호출에서 가져올 최대 이벤트 수.
                Event Loop Starvation 방지를 위해 제한합니다.
                Defaults to 1000.

        Returns:
            List[CompletionEvent]: 완료된 이벤트 리스트

        """

    async def poll_control_events(
        self, batch_limit: int = 1000
    ) -> List[ExecutionControlEvent]:
        """Poll internal control events from the execution engine."""
        return []

    @abstractmethod
    async def wait_for_completion(
        self, timeout_seconds: Optional[float] = None
    ) -> bool:
        """Wait until at least one completion event is available or the timeout expires.

        Args:
            timeout_seconds (Optional[float]): 최대 대기 시간(초). None이면 무기한 대기.

        Returns:
            bool: 완료 이벤트가 준비되면 True, timeout이면 False

        """

    @abstractmethod
    def get_in_flight_count(
        self,
    ) -> int:  # Renamed from in_flight to be consistent with get_* naming
        """Return the number of messages currently in flight.

        현재 처리 중인 메시지 수를 반환합니다.

        Returns:
            int: 현재 처리 중인 메시지 수

        """

    def get_min_inflight_offset(self, _tp: TopicPartition) -> Optional[int]:
        """Expose the deprecated engine-private in-flight offset hook.

        Commit safety is owned by the control-plane WorkManager dispatch ledger,
        not by engine-specific capability methods. Engines that still track
        private recovery registries may expose a best-effort value here, but
        control-plane commit clamping must not depend on it.

        Args:
            _tp (TopicPartition): 조회할 토픽/파티션

        Returns:
            Optional[int]: engine-private 최소 in-flight offset 또는 None

        """
        return None

    def get_runtime_metrics(self) -> Optional[EngineRuntimeDiagnostics]:
        """Return optional engine-specific runtime metrics.

        Returns:
            Engine runtime diagnostics, or None when the engine has no metrics.

        """
        return None

    @abstractmethod
    async def shutdown(self) -> None:
        """Shut down the execution engine gracefully.

        실행 엔진을 정상적으로 종료합니다.

        Returns:
            None

        """
