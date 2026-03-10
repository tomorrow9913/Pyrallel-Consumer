from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

_PHASE_NAMES = ("baseline", "async", "process")
_WORKLOAD_NAMES = ("sleep", "cpu", "io")
_TOPIC_PATTERN = re.compile(r"topic '([^']+)'")
_JSON_OUTPUT_PATTERN = re.compile(r"JSON summary written to (.+)$")
_FINAL_TPS_PATTERN = re.compile(r"Final TPS:\s*([0-9]+(?:\.[0-9]+)?)")


def _empty_tps_table() -> dict[str, dict[str, str]]:
    return {
        workload: {phase: "--" for phase in _PHASE_NAMES}
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
    output_path: str | None = None
    completed_runs: int = 0
    total_runs: int = 0
    progress_value: float = 0.0
    tps_by_workload: dict[str, dict[str, str]] = field(default_factory=_empty_tps_table)


class BenchmarkLogParser:
    def __init__(
        self,
        workload_mode: str,
        active_phases: tuple[str, ...] | None = None,
    ) -> None:
        self._workload_mode = workload_mode
        self._active_phases = active_phases or _PHASE_NAMES
        self._started_runs: set[tuple[str, str]] = set()
        self._completed_runs: set[tuple[str, str]] = set()
        self._active_run: tuple[str, str] | None = None
        self._started_run_order: deque[tuple[str, str]] = deque()
        self.snapshot = BenchmarkProgressSnapshot(
            total_runs=len(self._active_workloads()) * len(self._active_phases)
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
            self._mark_workload_running(workload)
            self._mark_phase_running("baseline")
            self._mark_run_started(workload, "baseline")
            self.snapshot.status_message = "Running baseline"
            return self.snapshot

        if "Starting PyrallelConsumer test" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            self._mark_workload_running(workload)
            if topic_name.endswith("-async"):
                self._mark_phase_completed("baseline")
                self._mark_phase_running("async")
                self._mark_run_started(workload, "async")
                self.snapshot.status_message = "Running async benchmark"
            elif topic_name.endswith("-process"):
                self._mark_phase_completed("async")
                self._mark_phase_running("process")
                self._mark_run_started(workload, "process")
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
        topic_name = columns[2] if len(columns) > 2 else ""
        if not run_name:
            return

        workload = self._extract_workload(run_name)
        if workload is None and topic_name:
            workload = self._extract_workload(topic_name)
        phase = self._extract_phase(run_name)
        if phase is None and run_type:
            phase = self._extract_phase(run_type)
        throughput = columns[4]

        if workload is not None:
            self.snapshot.current_workload = workload
        if workload is None or phase is None:
            return

        if self.snapshot.tps_by_workload[workload][phase] == "--":
            self._mark_run_completed(workload, phase)
        self.snapshot.tps_by_workload[workload][phase] = throughput
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

    def _mark_run_started(self, workload: str | None, phase: str) -> None:
        if workload is None or phase not in self._active_phases:
            return
        key = (workload, phase)
        if key not in self._started_runs:
            self._started_run_order.append(key)
        self._started_runs.add(key)
        self._active_run = key
        self._refresh_progress()

    def _mark_run_completed(self, workload: str, phase: str) -> None:
        key = (workload, phase)
        self._started_runs.add(key)
        self._completed_runs.add(key)
        if self._active_run == key:
            self._active_run = None
        self._refresh_progress()

    def _consume_final_tps(self, throughput: str) -> None:
        target_run = self._next_unfinished_run()
        if target_run is None:
            return
        workload, phase = target_run
        self.snapshot.tps_by_workload[workload][phase] = throughput
        self._mark_phase_completed(phase)
        self._mark_run_completed(workload, phase)
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

    def _next_unfinished_run(self) -> tuple[str, str] | None:
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

    def _active_workloads(self) -> tuple[str, ...]:
        if self._workload_mode == "all":
            return _WORKLOAD_NAMES
        return (self._workload_mode,)
