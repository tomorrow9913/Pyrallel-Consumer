from __future__ import annotations

from typing import Any

from pyrallel_consumer.dto import PartitionMetrics, SystemMetrics


class BrokerRuntimeSupport:
    def __init__(
        self,
        *,
        work_manager: Any,
        offset_trackers: dict[Any, Any],
        consumer: Any,
        max_in_flight_messages: int,
        min_in_flight_messages_to_resume: int,
        queue_max_messages: int,
        queue_resume_threshold: int,
        is_paused: bool,
        blocking_warn_seconds: float,
        logger: Any,
    ) -> None:
        self._work_manager = work_manager
        self._offset_trackers = offset_trackers
        self._consumer = consumer
        self._max_in_flight_messages = max_in_flight_messages
        self._min_in_flight_messages_to_resume = min_in_flight_messages_to_resume
        self._queue_max_messages = queue_max_messages
        self._queue_resume_threshold = queue_resume_threshold
        self._is_paused = is_paused
        self._blocking_warn_seconds = blocking_warn_seconds
        self._logger = logger

    def log_partition_diagnostics(self) -> None:
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        gaps = self._work_manager.get_gaps()
        blocking = self._work_manager.get_blocking_offsets()
        parts: list[str] = []
        for tp, tracker in self._offset_trackers.items():
            queued = sum(queue_sizes.get(tp, {}).values())
            gap_count = len(gaps.get(tp, []))
            blocking_offset = blocking.get(tp)
            blocking_age = None
            if blocking_offset is not None:
                durations = tracker.get_blocking_offset_durations()
                blocking_age = durations.get(blocking_offset.start)
            age_str = (
                "%.2fs" % blocking_age
                if isinstance(blocking_age, (int, float))
                else "n/a"
            )
            parts.append(
                "%s-%d queued=%d gaps=%d blocking=%s age=%s"
                % (
                    tp.topic,
                    tp.partition,
                    queued,
                    gap_count,
                    blocking_offset.start if blocking_offset else "none",
                    age_str,
                )
            )
            if (
                blocking_offset is not None
                and isinstance(blocking_age, (int, float))
                and blocking_age >= self._blocking_warn_seconds
            ):
                self._logger.warning(
                    "Prolonged blocking offset %s@%d age=%.2fs gaps=%d queued=%d",
                    tp,
                    blocking_offset.start,
                    blocking_age,
                    gap_count,
                    queued,
                )
        self._logger.debug("Partition diag: %s", "; ".join(parts))

    def check_backpressure(self, *, total_queued: int) -> bool:
        if self._consumer is None:
            raise RuntimeError("Consumer must be initialized for backpressure checks")

        total_in_flight = self._work_manager.get_total_in_flight_count()
        current_load = total_in_flight + total_queued
        partitions = self._consumer.assignment()
        if not partitions:
            return self._is_paused

        queue_full = (
            self._queue_max_messages > 0 and total_queued >= self._queue_max_messages
        )

        if not self._is_paused and (
            current_load > self._max_in_flight_messages or queue_full
        ):
            self._logger.warning(
                "Backpressure: pausing consumer (load=%d limit=%d)",
                current_load,
                self._max_in_flight_messages,
            )
            self._consumer.pause(partitions)
            return True

        if self._is_paused and (
            current_load < self._min_in_flight_messages_to_resume
            and (
                self._queue_resume_threshold == 0
                or total_queued <= self._queue_resume_threshold
            )
        ):
            self._logger.debug(
                "Backpressure released: resuming consumer (load=%d resume=%d)",
                current_load,
                self._min_in_flight_messages_to_resume,
            )
            self._consumer.resume(partitions)
            return False

        return self._is_paused

    def build_system_metrics(self) -> SystemMetrics:
        partition_metrics_list: list[PartitionMetrics] = []
        queue_sizes = self._work_manager.get_virtual_queue_sizes()
        for tp, tracker in self._offset_trackers.items():
            true_lag = max(
                0, tracker.last_fetched_offset - tracker.last_committed_offset
            )
            gaps = tracker.get_gaps()
            gap_count = len(gaps)
            blocking_offset = gaps[0].start if gaps else None
            durations = tracker.get_blocking_offset_durations()
            blocking_duration = (
                durations.get(blocking_offset) if blocking_offset else None
            )
            queued_count = sum(queue_sizes.get(tp, {}).values())
            partition_metrics_list.append(
                PartitionMetrics(
                    tp=tp,
                    true_lag=true_lag,
                    gap_count=gap_count,
                    blocking_offset=blocking_offset,
                    blocking_duration_sec=blocking_duration,
                    queued_count=queued_count,
                )
            )

        return SystemMetrics(
            total_in_flight=self._work_manager.get_total_in_flight_count(),
            is_paused=self._is_paused,
            partitions=partition_metrics_list,
        )
