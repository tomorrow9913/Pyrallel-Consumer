from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

_PHASE_NAMES = ("baseline", "async", "process")
_WORKLOAD_NAMES = ("sleep", "cpu", "io")
_ORDERING_NAMES = ("key_hash", "partition", "unordered")
_TOPIC_PATTERN = re.compile(r"topic '([^']+)'")
_JSON_OUTPUT_PATTERN = re.compile(r"JSON summary written to (.+)$")
_FINAL_TPS_PATTERN = re.compile(r"Final TPS:\s*([0-9]+(?:\.[0-9]+)?)")


def _empty_tps_table() -> dict[str, dict[str, str]]:
    return {
        workload: {phase: "--" for phase in _PHASE_NAMES}
        for workload in _WORKLOAD_NAMES
    }


def _empty_ordering_tps_table() -> dict[str, dict[str, dict[str, str]]]:
    return {
        workload: {
            ordering: {phase: "--" for phase in _PHASE_NAMES}
            for ordering in _ORDERING_NAMES
        }
        for workload in _WORKLOAD_NAMES
    }


@dataclass(slots=True)
class BenchmarkProgressSnapshot:
    status_message: str = "Waiting to start"
    phase_statuses: dict[str, str] = field(
        default_factory=lambda: {phase: "pending" for phase in _PHASE_NAMES}
    )
    workload_statuses: dict[str, str] = field(
        default_factory=lambda: {workload: "pending" for workload in _WORKLOAD_NAMES}
    )
    current_workload: str | None = None
    current_ordering: str | None = None
    output_path: str | None = None
    completed_runs: int = 0
    total_runs: int = 0
    progress_value: float = 0.0
    tps_by_workload: dict[str, dict[str, str]] = field(default_factory=_empty_tps_table)
    tps_by_workload_ordering: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=_empty_ordering_tps_table
    )


class BenchmarkLogParser:
    def __init__(
        self,
        workload_mode: str,
        active_phases: tuple[str, ...] | None = None,
        active_orderings: tuple[str, ...] | None = None,
        active_workloads: tuple[str, ...] | None = None,
    ) -> None:
        self._workload_mode = workload_mode
        self._active_phases = active_phases or _PHASE_NAMES
        self._active_orderings = active_orderings or ("key_hash",)
        self._active_workloads = active_workloads or self._resolve_active_workloads()
        self._started_runs: set[tuple[str, str, str]] = set()
        self._completed_runs: set[tuple[str, str, str]] = set()
        self._active_run: tuple[str, str, str] | None = None
        self._started_run_order: deque[tuple[str, str, str]] = deque()
        self.snapshot = BenchmarkProgressSnapshot(
            total_runs=(
                len(self._active_workloads)
                * len(self._active_orderings)
                * len(self._active_phases)
            )
        )

    def consume(self, line: str) -> BenchmarkProgressSnapshot:
        stripped = line.strip()
        if not stripped:
            return self.snapshot

        if "Resetting benchmark topics/groups:" in stripped:
            self.snapshot.status_message = "Resetting topics/groups"
            return self.snapshot

        topic_match = _TOPIC_PATTERN.search(stripped)
        topic_name = topic_match.group(1) if topic_match else None

        if "Starting baseline consumer" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            ordering = self._extract_ordering(topic_name)
            self._mark_workload_running(workload)
            self._mark_ordering_running(ordering)
            self._mark_phase_running("baseline")
            self._mark_run_started(workload, ordering, "baseline")
            self.snapshot.status_message = "Running baseline"
            return self.snapshot

        if "Starting PyrallelConsumer test" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            ordering = self._extract_ordering(topic_name)
            self._mark_workload_running(workload)
            self._mark_ordering_running(ordering)
            if topic_name.endswith("-async"):
                self._mark_phase_completed("baseline")
                self._mark_phase_running("async")
                self._mark_run_started(workload, ordering, "async")
                self.snapshot.status_message = "Running async benchmark"
            elif topic_name.endswith("-process"):
                self._mark_phase_completed("async")
                self._mark_phase_running("process")
                self._mark_run_started(workload, ordering, "process")
                self.snapshot.status_message = "Running process benchmark"
            return self.snapshot

        json_output_match = _JSON_OUTPUT_PATTERN.search(stripped)
        if json_output_match is not None:
            self.snapshot.status_message = "JSON summary written"
            self.snapshot.output_path = json_output_match.group(1)
            return self.snapshot

        final_tps_match = _FINAL_TPS_PATTERN.search(stripped)
        if final_tps_match is not None:
            self._consume_final_tps(final_tps_match.group(1))
            return self.snapshot

        if " | " in stripped:
            self._consume_result_row(stripped)

        return self.snapshot

    def _consume_result_row(self, line: str) -> None:
        columns = [part.strip() for part in line.split("|")]
        if len(columns) < 5:
            return
        run_name = columns[0]
        run_type = columns[1] if len(columns) > 1 else ""
        ordering_name = ""
        topic_name = columns[2] if len(columns) > 2 else ""
        throughput = columns[4]
        if len(columns) >= 8 and columns[2] in _ORDERING_NAMES:
            ordering_name = columns[2]
            topic_name = columns[3] if len(columns) > 3 else ""
            throughput = columns[5]
        if not run_name:
            return
        if run_name == "Run":
            return

        workload = self._extract_workload(run_name)
        if workload is None and topic_name:
            workload = self._extract_workload(topic_name)
        ordering = self._extract_ordering(ordering_name or run_name)
        if ordering is None and topic_name:
            ordering = self._extract_ordering(topic_name)
        phase = self._extract_phase(run_name)
        if phase is None and run_type:
            phase = self._extract_phase(run_type)

        if workload is not None:
            self.snapshot.current_workload = workload
        if ordering is not None:
            self.snapshot.current_ordering = ordering
        if workload is None or ordering is None or phase is None:
            return

        if self.snapshot.tps_by_workload_ordering[workload][ordering][phase] == "--":
            self._mark_run_completed(workload, ordering, phase)
        self.snapshot.tps_by_workload[workload][phase] = throughput
        self.snapshot.tps_by_workload_ordering[workload][ordering][phase] = throughput
        self._mark_phase_completed(phase)

        if phase == self._active_phases[-1]:
            self.snapshot.workload_statuses[workload] = "completed"

    def _mark_workload_running(self, workload: str | None) -> None:
        if workload is None:
            return
        self.snapshot.current_workload = workload
        if self.snapshot.workload_statuses[workload] == "pending":
            self.snapshot.workload_statuses[workload] = "running"

    def _mark_phase_running(self, phase: str) -> None:
        self.snapshot.phase_statuses[phase] = "running"

    def _mark_phase_completed(self, phase: str) -> None:
        self.snapshot.phase_statuses[phase] = "completed"

    def _mark_ordering_running(self, ordering: str | None) -> None:
        if ordering is not None:
            self.snapshot.current_ordering = ordering

    def _mark_run_started(
        self, workload: str | None, ordering: str | None, phase: str
    ) -> None:
        if workload is None or ordering is None or phase not in self._active_phases:
            return
        self.snapshot.current_ordering = ordering
        key = (workload, ordering, phase)
        if key not in self._started_runs:
            self._started_run_order.append(key)
        self._started_runs.add(key)
        self._active_run = key
        self._refresh_progress()

    def _mark_run_completed(self, workload: str, ordering: str, phase: str) -> None:
        key = (workload, ordering, phase)
        self._started_runs.add(key)
        self._completed_runs.add(key)
        if self._active_run == key:
            self._active_run = None
        self._refresh_progress()

    def _consume_final_tps(self, throughput: str) -> None:
        target_run = self._next_unfinished_run()
        if target_run is None:
            return
        workload, ordering, phase = target_run
        self.snapshot.current_workload = workload
        self.snapshot.current_ordering = ordering
        self.snapshot.tps_by_workload[workload][phase] = throughput
        self.snapshot.tps_by_workload_ordering[workload][ordering][phase] = throughput
        self._mark_phase_completed(phase)
        self._mark_run_completed(workload, ordering, phase)
        if phase == self._active_phases[-1]:
            self.snapshot.workload_statuses[workload] = "completed"
        self._active_run = self._next_unfinished_run()

    def _refresh_progress(self) -> None:
        self.snapshot.completed_runs = len(self._completed_runs)
        active_runs = len(self._started_runs - self._completed_runs)
        self.snapshot.progress_value = min(
            float(self.snapshot.total_runs),
            float(self.snapshot.completed_runs) + (0.5 * active_runs),
        )

    def _next_unfinished_run(self) -> tuple[str, str, str] | None:
        while self._started_run_order:
            candidate = self._started_run_order[0]
            if candidate in self._completed_runs:
                self._started_run_order.popleft()
                continue
            return candidate
        return None

    def _extract_phase(self, value: str) -> str | None:
        if value.endswith("-async") or value.endswith("async"):
            return "async"
        if value.endswith("-process") or value.endswith("process"):
            return "process"
        if "baseline" in value:
            return "baseline"
        return None

    def _extract_workload(self, value: str) -> str | None:
        if self._workload_mode != "all":
            return self._workload_mode
        for workload in _WORKLOAD_NAMES:
            if f"-{workload}-" in value or value.startswith(f"{workload}-"):
                return workload
        return None

    def _extract_ordering(self, value: str) -> str | None:
        for ordering in self._active_orderings:
            if (
                f"-{ordering}-" in value
                or value.startswith(f"{ordering}-")
                or value.endswith(f"-{ordering}")
                or value == ordering
            ):
                return ordering
        if len(self._active_orderings) == 1:
            return self._active_orderings[0]
        return None

    def _resolve_active_workloads(self) -> tuple[str, ...]:
        if self._workload_mode == "all":
            return _WORKLOAD_NAMES
        return (self._workload_mode,)
