# -*- coding: utf-8 -*-
# File: pyrallel_consumer/execution_plane/async_engine.py
# Role: Implements asyncio-based WorkItem execution, retry handling, and completion event buffering.
# Extend here for async-engine behavior; keep shared engine contracts in base.py.
import asyncio
import contextvars
import importlib
import inspect
import logging
import random
import uuid
from asyncio import Semaphore, Task
from collections import deque
from collections.abc import Callable
from typing import Any, Awaitable, Deque, List, Optional, Set, cast

from pyrallel_consumer.config import ExecutionConfig
from pyrallel_consumer.dto import (
    CompletionEvent,
    CompletionStatus,
    EngineRuntimeDiagnostics,
    EngineWorkerDiagnostics,
    ExecutionControlEvent,
    TopicPartition,
    WorkItem,
)
from pyrallel_consumer.execution_plane.base import (
    BaseExecutionEngine,
    BatchCancelScope,
    BatchSubmissionReceipt,
)
from pyrallel_consumer.execution_plane.batch_result import (
    normalize_batch_worker_result,
    ordered_prefix_blocked_tail_items,
)
from pyrallel_consumer.execution_plane.worker_spec import (
    CompletionFailureClass,
    WorkerSpec,
)
from pyrallel_consumer.worker import (
    BatchWorkerContractError,
    BatchWorkerResult,
    _bound_batch_worker_error_reason,
)


class AsyncExecutionEngine(BaseExecutionEngine):
    """비동기 실행 엔진의 구현입니다.

    세마포어를 사용하여 동시 실행 태스크 수를 제어합니다.

    Args:
        config (ExecutionConfig): 실행 엔진 설정.
        worker_fn (Callable[[WorkItem], Any]): 사용자 정의 비동기 워커 함수.

    """

    def __init__(
        self,
        config: ExecutionConfig,
        worker_fn: Callable[[WorkItem], Any] | WorkerSpec,
    ):
        """Initialize this component.

        Args:
            config: Configuration object used to initialize this component.
            worker_fn: User worker callable invoked for each work item.

        """
        self._config = config
        self._worker_spec = (
            worker_fn
            if isinstance(worker_fn, WorkerSpec)
            else WorkerSpec.single(worker_fn)
        )
        self._worker_fn = self._worker_spec.callable
        self._semaphore = Semaphore(config.max_in_flight)
        self._completion_queue: asyncio.Queue[CompletionEvent] = asyncio.Queue()
        self._control_queue: asyncio.Queue[ExecutionControlEvent] = asyncio.Queue()
        self._prefetched_completion_events: Deque[CompletionEvent] = deque()
        self._in_flight_tasks: Set[Task] = set()
        self._task_permit_counts: dict[Task, int] = {}
        self._shutdown_event = asyncio.Event()
        self._activity_event = asyncio.Event()
        self._cancelled_batch_item_keys: set[tuple[str, str, int, int]] = set()
        self._cancelled_batch_partition_keys: set[tuple[str, int, int | None]] = set()
        self._cancelled_all_batch_items = False

        # Logger for this module
        self._logger = logging.getLogger(__name__)

        # Context propagation (initial capture for consistent logging in tasks)
        # Note: contextvars.copy_context() is primarily for when the worker function itself
        # depends on contextvars set up by the caller. For simple logging/tracing,
        # passing relevant IDs explicitly or relying on thread-local storage might be simpler.
        self._context = contextvars.copy_context()

    @property
    def supports_ordered_route_batch(self) -> bool:
        """Return whether this engine can accept ordered route batches."""
        return self._worker_spec.kind == "batch"

    async def submit(self, work_item: WorkItem) -> None:
        """제출된 작업 항목을 처리합니다. 세마포어를 획득하고, 새 비동기 태스크를 생성하여 워커 함수를 실행합니다.

        Args:
            work_item (WorkItem): 제출할 작업 항목

        Raises:
            RuntimeError: 실행 엔진이 종료 중이면 새 작업을 받을 수 없을 때 발생합니다.

        """
        if self._worker_spec.kind == "batch":
            await self.submit_batch([work_item])
            return
        if self._shutdown_event.is_set():
            raise RuntimeError("Engine is shutting down, cannot accept new work")

        await self._semaphore.acquire()

        # Create a task to execute the worker function within the captured context
        task = self._context.copy().run(
            asyncio.create_task, self._execute_worker_task(work_item)
        )
        self._in_flight_tasks.add(task)
        self._task_permit_counts[task] = 1
        task.add_done_callback(self._task_done_callback)

    async def submit_batch(
        self, work_items: list[WorkItem]
    ) -> BatchSubmissionReceipt | None:
        """Submit a public batch-worker attempt and return ownership metadata."""
        if self._worker_spec.kind != "batch":
            return await super().submit_batch(work_items)
        if self._shutdown_event.is_set():
            raise RuntimeError("Engine is shutting down, cannot accept new work")
        if len(work_items) > self._config.max_in_flight:
            raise ValueError("batch_worker_batch_size_exceeds_max_in_flight")
        acquired = 0
        try:
            for _ in work_items:
                await self._semaphore.acquire()
                acquired += 1
            receipt = self._build_batch_submission_receipt(
                work_items,
                batch_id=uuid.uuid4().hex,
                attempt=1,
            )
            registry = getattr(self, "_engine_settlement_notification_registry", None)
            if registry is not None:
                registry.register_batch_owner(receipt)
            task = self._context.copy().run(
                asyncio.create_task,
                self._execute_batch_worker_task(
                    list(work_items), batch_id=receipt.batch_id
                ),
            )
            self._in_flight_tasks.add(task)
            self._task_permit_counts[task] = acquired
            task.add_done_callback(self._task_done_callback)
            return receipt
        except BaseException:
            for _ in range(acquired):
                self._semaphore.release()
            raise

    def _build_batch_submission_receipt(
        self,
        work_items: list[WorkItem],
        *,
        batch_id: str,
        attempt: int,
    ) -> BatchSubmissionReceipt:
        """Build async ownership metadata for accepted batch items."""
        return BatchSubmissionReceipt(
            batch_id=batch_id,
            owner="async",
            worker_generation=0,
            accepted=tuple(
                (
                    item.id,
                    item.tp,
                    item.offset,
                    item.epoch,
                    attempt,
                )
                for item in work_items
            ),
        )

    def _transfer_batch_ownership(
        self,
        work_items: list[WorkItem],
        *,
        expected_old_batch_id: str | None,
        attempt: int,
    ) -> str | None:
        """Transfer async ownership before emitting a later-attempt completion."""
        if not work_items or expected_old_batch_id is None:
            return expected_old_batch_id
        registry = getattr(self, "_engine_settlement_notification_registry", None)
        transfer = getattr(registry, "transfer_batch_owner", None)
        if not callable(transfer):
            return expected_old_batch_id
        receipt = self._build_batch_submission_receipt(
            work_items,
            batch_id=uuid.uuid4().hex,
            attempt=attempt,
        )
        if not transfer(expected_old_batch_id=expected_old_batch_id, receipt=receipt):
            return None
        return receipt.batch_id

    def cancel_batch_items(self, scope: BatchCancelScope) -> None:
        """Record async-engine tombstones before revoke/shutdown/fatal cleanup."""
        item_keys: set[tuple[str, str, int, int]] = getattr(
            self, "_cancelled_batch_item_keys", set()
        )
        partition_keys: set[tuple[str, int, int | None]] = getattr(
            self, "_cancelled_batch_partition_keys", set()
        )
        if not scope.item_ids and not scope.topic_partitions and scope.epoch is None:
            self._cancelled_all_batch_items = True
        wildcard_tp = TopicPartition("", -1)
        for item_id in scope.item_ids:
            for tp in scope.topic_partitions or (wildcard_tp,):
                item_keys.add((item_id, tp.topic, tp.partition, scope.epoch or -1))
        for tp in scope.topic_partitions:
            partition_keys.add((tp.topic, tp.partition, scope.epoch))
        self._cancelled_batch_item_keys = item_keys
        self._cancelled_batch_partition_keys = partition_keys
        super().cancel_batch_items(scope)

    def _is_batch_item_cancelled(self, item: WorkItem) -> bool:
        """Return whether a work item is blocked by an internal tombstone."""
        item_keys: set[tuple[str, str, int, int]] = getattr(
            self, "_cancelled_batch_item_keys", set()
        )
        partition_keys: set[tuple[str, int, int | None]] = getattr(
            self, "_cancelled_batch_partition_keys", set()
        )
        if getattr(self, "_cancelled_all_batch_items", False):
            return True
        return (
            (item.id, item.tp.topic, item.tp.partition, item.epoch) in item_keys
            or (item.id, item.tp.topic, item.tp.partition, -1) in item_keys
            or (item.id, "", -1, item.epoch) in item_keys
            or (item.id, "", -1, -1) in item_keys
            or (item.tp.topic, item.tp.partition, item.epoch) in partition_keys
            or (item.tp.topic, item.tp.partition, None) in partition_keys
        )

    def _is_completion_cancelled(self, event: CompletionEvent) -> bool:
        """Return whether a completion belongs to a tombstoned batch item."""
        item = WorkItem(
            event.id,
            event.tp,
            event.offset,
            event.epoch,
            None,
            None,
        )
        return self._is_batch_item_cancelled(item)

    async def _wait_for_item_settlement(
        self,
        event: CompletionEvent,
        *,
        batch_id: str | None,
    ) -> bool:
        """Wait for control-plane settlement before promoting an ordered tail."""
        if batch_id is None:
            return True
        if self._is_completion_cancelled(event):
            return False
        registry = getattr(self, "_engine_settlement_notification_registry", None)
        wait_for_settlement = getattr(registry, "wait_for_item_settlement", None)
        if not callable(wait_for_settlement):
            return True
        result = wait_for_settlement(
            item_id=event.id,
            tp=event.tp,
            offset=event.offset,
            epoch=event.epoch,
            attempt=event.attempt,
            batch_id=batch_id,
        )
        if inspect.isawaitable(result):
            result = await result
        return bool(result)

    async def _execute_worker_task(self, work_item: WorkItem) -> None:
        """사용자 워커 함수를 실행하고 결과를 완료 큐에 넣습니다.

        예외 처리 및 타임아웃 로직을 포함합니다.

        Args:
            work_item (WorkItem): 워커 함수에 전달할 작업 항목입니다.

        """
        status = CompletionStatus.SUCCESS
        error: Optional[str] = None
        attempt = 0

        for attempt in range(1, self._config.max_retries + 1):
            try:
                await asyncio.wait_for(
                    self._worker_fn(work_item),
                    timeout=self._config.async_config.task_timeout_ms / 1000.0,
                )
                status = CompletionStatus.SUCCESS
                error = None
                break
            except asyncio.TimeoutError:
                status = CompletionStatus.FAILURE
                error = "Task for offset %d timed out." % work_item.offset
                self._logger.error(error)
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)
            except Exception as e:
                status = CompletionStatus.FAILURE
                error = _bound_batch_worker_error_reason(str(e))
                self._logger.exception(
                    "Task for offset %d failed with exception: %s"
                    % (work_item.offset, error)
                )
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)

        completion_event = CompletionEvent(
            id=work_item.id,
            tp=work_item.tp,
            offset=work_item.offset,
            epoch=work_item.epoch,
            status=status,
            error=error,
            attempt=attempt,
        )
        await self._put_completion_event(completion_event)

    async def _put_completion_event(self, event: CompletionEvent) -> None:
        """Enqueue a completion event and wake waiters."""
        await self._completion_queue.put(event)
        self._activity_event.set()

    async def _put_control_event(self, event: ExecutionControlEvent) -> None:
        """Enqueue a control event and wake waiters."""
        await self._control_queue.put(event)
        self._activity_event.set()

    async def _execute_batch_worker_task(
        self,
        work_items: list[WorkItem],
        *,
        first_attempt: int = 1,
        batch_id: str | None = None,
    ) -> None:
        """Run one async batch-worker attempt sequence for the pending items."""
        result: BatchWorkerResult = None
        error: Optional[str] = None
        attempt = 0
        batch_worker = cast(
            Callable[
                [list[WorkItem]], Awaitable[BatchWorkerResult] | BatchWorkerResult
            ],
            self._worker_fn,
        )

        current_batch_id = batch_id
        for attempt in range(first_attempt, self._config.max_retries + 1):
            if any(self._is_batch_item_cancelled(item) for item in work_items):
                return
            if attempt > 1:
                current_batch_id = self._transfer_batch_ownership(
                    work_items,
                    expected_old_batch_id=current_batch_id,
                    attempt=attempt,
                )
                if current_batch_id is None:
                    return
            try:
                maybe_result = batch_worker(list(work_items))
                if inspect.isawaitable(maybe_result):
                    result = await asyncio.wait_for(
                        maybe_result,
                        timeout=self._config.async_config.task_timeout_ms / 1000.0,
                    )
                else:
                    result = cast(BatchWorkerResult, maybe_result)
                error = None
                break
            except asyncio.TimeoutError:
                error = _bound_batch_worker_error_reason("Batch task timed out.")
                self._logger.error(error)
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)
            except Exception as exc:
                error = _bound_batch_worker_error_reason(str(exc))
                self._logger.exception("Batch task failed with exception: %s", error)
                if attempt < self._config.max_retries:
                    await self._apply_backoff(attempt)

        if error is not None:
            for item in work_items:
                await self._put_completion_event(
                    CompletionEvent(
                        id=item.id,
                        tp=item.tp,
                        offset=item.offset,
                        epoch=item.epoch,
                        status=CompletionStatus.FAILURE,
                        error=error,
                        attempt=attempt,
                    )
                )
            return

        batch_runtime = self._worker_spec.batch_runtime
        if batch_runtime is None:
            raise RuntimeError("batch worker runtime spec is required")
        ordering_mode = batch_runtime.ordering_mode
        try:
            events = normalize_batch_worker_result(
                pending_items=work_items,
                result=result,
                ordering_mode=ordering_mode,
                attempt=attempt,
            )
        except BatchWorkerContractError as exc:
            self.cancel_batch_items(
                BatchCancelScope(
                    item_ids=tuple(item.id for item in work_items),
                    topic_partitions=tuple({item.tp for item in work_items}),
                    reason="fatal",
                )
            )
            await self._put_control_event(
                ExecutionControlEvent(
                    kind="fatal",
                    error=exc,
                    code=exc.code,
                    reason=exc.reason,
                    failure_class=CompletionFailureClass.BATCH_WORKER_CONTRACT_ERROR.value,
                    committable=False,
                    batch_id=current_batch_id,
                    worker_generation=0,
                    item_ids=tuple(item.id for item in work_items),
                    item_count=len(work_items),
                    epoch=work_items[0].epoch if work_items else None,
                    attempt=attempt,
                )
            )
            self._shutdown_event.set()
            return
        for event in events:
            await self._put_completion_event(event)
        blocked_tail = ordered_prefix_blocked_tail_items(
            pending_items=work_items,
            result=result,
        )
        if not blocked_tail:
            return
        if any(self._is_batch_item_cancelled(item) for item in blocked_tail):
            return
        predecessor_event = next(
            (event for event in events if event.status == CompletionStatus.FAILURE),
            None,
        )
        if predecessor_event is not None and not await self._wait_for_item_settlement(
            predecessor_event,
            batch_id=current_batch_id,
        ):
            return
        provider = getattr(self, "_poison_policy_snapshot_provider", None)
        if callable(provider):
            poison_policy = importlib.import_module(
                "pyrallel_consumer.control_plane.poison_policy"
            )
            decision = poison_policy.apply_poison_policy(
                blocked_tail,
                ordering_mode=ordering_mode,
                snapshot=provider(),
            )
            for event in decision.forced_failure_events:
                forced_items = [item for item in blocked_tail if item.id == event.id]
                current_batch_id = self._transfer_batch_ownership(
                    forced_items,
                    expected_old_batch_id=current_batch_id,
                    attempt=event.attempt,
                )
                if current_batch_id is None:
                    return
                await self._put_completion_event(event)
            blocked_tail = list(decision.accepted_items)
            if not blocked_tail:
                return
        if attempt < self._config.max_retries:
            current_batch_id = self._transfer_batch_ownership(
                blocked_tail,
                expected_old_batch_id=current_batch_id,
                attempt=attempt,
            )
            if current_batch_id is None:
                return
            await self._execute_batch_worker_task(
                blocked_tail,
                first_attempt=attempt,
                batch_id=current_batch_id,
            )
            return
        for item in blocked_tail:
            await self._put_completion_event(
                CompletionEvent(
                    id=item.id,
                    tp=item.tp,
                    offset=item.offset,
                    epoch=item.epoch,
                    status=CompletionStatus.FAILURE,
                    error="ordered_prefix_blocked",
                    attempt=attempt,
                    terminal=True,
                    failure_class=CompletionFailureClass.WORKER_FAILURE.value,
                )
            )

    async def _apply_backoff(self, attempt: int) -> None:
        """Handle apply backoff within async execution.

        Args:
            attempt (int): 현재 재시도 횟수입니다.

        """
        base_delay_ms = self._config.retry_backoff_ms

        if self._config.exponential_backoff:
            delay_ms = base_delay_ms * (2 ** (attempt - 1))
            delay_ms = min(delay_ms, self._config.max_retry_backoff_ms)
        else:
            delay_ms = base_delay_ms

        if self._config.retry_jitter_ms > 0:
            jitter = random.uniform(0, self._config.retry_jitter_ms)
            delay_ms += jitter

        await asyncio.sleep(delay_ms / 1000.0)

    def _task_done_callback(self, task: Task) -> None:
        """태스크 완료 시 호출되는 콜백. 세마포어를 해제하고 완료된 태스크를 추적 목록에서 제거합니다.

        Args:
            task (Task): 완료된 asyncio 태스크입니다.

        """
        self._in_flight_tasks.discard(task)
        permit_count = self._task_permit_counts.pop(task, 1)
        for _ in range(permit_count):
            self._semaphore.release()

        # Check for exceptions that were not caught within _execute_worker_task
        # (e.g., if the task itself was cancelled or an unexpected error occurred before try/except)
        if task.cancelled():
            self._logger.warning("Task %s was cancelled." % task.get_name())
        elif task.exception():
            # Exception already handled and logged in _execute_worker_task,
            # but this catches any unhandled exceptions during the callback itself.
            self._logger.error(
                "Unhandled exception in task done callback for task %s."
                % task.get_name(),
                exc_info=task.exception(),
            )

    async def poll_completed_events(
        self, batch_limit: int = 1000
    ) -> List[CompletionEvent]:
        """완료 큐에서 완료 이벤트를 가져와 리스트로 반환합니다.

        Args:
            batch_limit (int): 한 번에 반환할 최대 완료 이벤트 수입니다.

        Returns:
            List[CompletionEvent]: 완료 이벤트 목록입니다.

        """
        completed_events: List[CompletionEvent] = []
        while (
            len(completed_events) < batch_limit and self._prefetched_completion_events
        ):
            completed_events.append(self._prefetched_completion_events.popleft())
        while (
            len(completed_events) < batch_limit and not self._completion_queue.empty()
        ):
            completed_events.append(self._completion_queue.get_nowait())
        if self._completion_queue.empty() and self._control_queue.empty():
            self._activity_event.clear()
        return completed_events

    async def poll_control_events(
        self, batch_limit: int = 1000
    ) -> List[ExecutionControlEvent]:
        """Drain queued control events for the broker poller."""
        control_events: List[ExecutionControlEvent] = []
        while len(control_events) < batch_limit and not self._control_queue.empty():
            control_events.append(self._control_queue.get_nowait())
        if self._completion_queue.empty() and self._control_queue.empty():
            self._activity_event.clear()
        return control_events

    async def wait_for_completion(
        self, timeout_seconds: Optional[float] = None
    ) -> bool:
        """Wait for completion in async execution.

        Args:
            timeout_seconds (Optional[float]): 완료 이벤트를 기다릴 최대 시간입니다.
                None이면 무기한 대기합니다.

        Returns:
            bool: 완료 이벤트가 준비되었으면 True, 제한 시간 안에 없으면 False입니다.

        """
        if (
            self._prefetched_completion_events
            or not self._completion_queue.empty()
            or not self._control_queue.empty()
        ):
            return True

        try:
            if timeout_seconds is None:
                await self._activity_event.wait()
            else:
                await asyncio.wait_for(
                    self._activity_event.wait(),
                    timeout=timeout_seconds,
                )
        except asyncio.TimeoutError:
            return False

        return bool(
            self._prefetched_completion_events
            or not self._completion_queue.empty()
            or not self._control_queue.empty()
        )

    def get_in_flight_count(self) -> int:
        """현재 처리 중인 작업 항목의 수를 반환합니다.

        Returns:
            int: 아직 완료되지 않은 비동기 작업 수입니다.

        """
        return len(self._in_flight_tasks)

    def get_runtime_metrics(self) -> EngineRuntimeDiagnostics:
        """Return async-engine-owned runtime diagnostics."""
        return EngineRuntimeDiagnostics(
            engine_type="async",
            workers=EngineWorkerDiagnostics(
                total=self._config.max_in_flight,
                executing=len(self._in_flight_tasks),
                admitted=None,
                top_k_loads=[],
            ),
        )

    def get_min_inflight_offset(self, tp: TopicPartition) -> Optional[int]:
        """Return min inflight offset for async execution.

        Args:
            tp (TopicPartition): 조회할 토픽 파티션입니다.

        Returns:
            Optional[int]: async 엔진은 offset별 in-flight 추적을 제공하지 않으므로 항상 None입니다.

        """
        del tp
        return None

    async def shutdown(self) -> None:
        """실행 엔진을 정상적으로 종료합니다. 모든 진행 중인 태스크가 완료되거나 취소될 때까지 대기합니다."""
        self._logger.debug("Initiating AsyncExecutionEngine shutdown.")
        self._shutdown_event.set()
        self.cancel_batch_items(BatchCancelScope(reason="shutdown"))

        grace_timeout = self._config.async_config.shutdown_grace_timeout_ms / 1000.0

        if self._in_flight_tasks:
            self._logger.debug(
                "Waiting for %d in-flight tasks to complete."
                % len(self._in_flight_tasks)
            )

            done, pending = await asyncio.wait(
                self._in_flight_tasks, timeout=grace_timeout
            )

            if pending:
                self._logger.warning(
                    "Cancelling %d task(s) after shutdown grace timeout", len(pending)
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)

            if done:
                await asyncio.gather(*done, return_exceptions=True)
            self._logger.debug("All in-flight tasks handled during shutdown.")

        self._logger.debug("AsyncExecutionEngine shutdown complete.")
