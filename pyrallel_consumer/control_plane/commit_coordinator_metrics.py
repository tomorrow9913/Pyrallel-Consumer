from __future__ import annotations

from collections.abc import Callable
from typing import Any


class CommitCoordinatorMetricsSink:
    """Typed adapter from coordinator events to the configured metrics exporter."""

    def __init__(
        self,
        *,
        get_metrics_exporter: Callable[[], Any | None],
        get_engine_type: Callable[[], str],
        get_pending_depth: Callable[[], int | None],
    ) -> None:
        self._get_metrics_exporter = get_metrics_exporter
        self._get_engine_type = get_engine_type
        self._get_pending_depth = get_pending_depth

    def record_pending_depth(self) -> None:
        exporter = self._get_metrics_exporter()
        if exporter is None:
            return
        depth = self._get_pending_depth()
        if depth is None:
            return
        recorder = getattr(exporter, "set_commit_coordinator_pending_partitions", None)
        if callable(recorder):
            recorder(self._get_engine_type(), depth)

    def record_submitted(self, count: int) -> None:
        self._call_counter("record_commit_coordinator_submitted", count)

    def record_success(self, count: int, latency: float | None) -> None:
        self._call_counter("record_commit_coordinator_success", count)
        if latency is None:
            return
        exporter = self._get_metrics_exporter()
        if exporter is None:
            return
        observer = getattr(
            exporter,
            "observe_commit_coordinator_settlement_latency",
            None,
        )
        if callable(observer):
            observer(self._get_engine_type(), latency)

    def record_failure(self, reason: str, count: int) -> None:
        self._call_reason_counter("record_commit_coordinator_failure", reason, count)

    def record_retry(self, reason: str, count: int) -> None:
        self._call_reason_counter("record_commit_coordinator_retry", reason, count)

    def record_coalesced(self, count: int) -> None:
        self._call_counter("record_commit_coordinator_coalesced", count)

    def record_event(
        self,
        event: str,
        reason: str | None,
        count: int,
        latency: float | None,
    ) -> None:
        """Compatibility callback used by CommitCoordinator."""
        self.record_pending_depth()
        if event == "submitted":
            self.record_submitted(count)
        elif event == "success":
            self.record_success(count, latency)
        elif event == "failure" and reason is not None:
            self.record_failure(reason, count)
        elif event == "retry" and reason is not None:
            self.record_retry(reason, count)
        elif event == "coalesced":
            self.record_coalesced(count)

    def _call_counter(self, method_name: str, count: int) -> None:
        exporter = self._get_metrics_exporter()
        if exporter is None:
            return
        recorder = getattr(exporter, method_name, None)
        if callable(recorder):
            recorder(self._get_engine_type(), count)

    def _call_reason_counter(self, method_name: str, reason: str, count: int) -> None:
        exporter = self._get_metrics_exporter()
        if exporter is None:
            return
        recorder = getattr(exporter, method_name, None)
        if callable(recorder):
            recorder(self._get_engine_type(), reason, count)
