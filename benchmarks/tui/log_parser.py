from __future__ import annotations

import re
from dataclasses import dataclass, field

_PHASE_NAMES = ("baseline", "async", "process")
_WORKLOAD_NAMES = ("sleep", "cpu", "io")
_TOPIC_PATTERN = re.compile(r"topic '([^']+)'")
_JSON_OUTPUT_PATTERN = re.compile(r"JSON summary written to (.+)$")


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


class BenchmarkLogParser:
    def __init__(self, workload_mode: str) -> None:
        self._workload_mode = workload_mode
        self.snapshot = BenchmarkProgressSnapshot()

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
            self.snapshot.status_message = "Running baseline"
            return self.snapshot

        if "Starting PyrallelConsumer test" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            self._mark_workload_running(workload)
            if topic_name.endswith("-async"):
                self._mark_phase_completed("baseline")
                self._mark_phase_running("async")
                self.snapshot.status_message = "Running async benchmark"
            elif topic_name.endswith("-process"):
                self._mark_phase_completed("async")
                self._mark_phase_running("process")
                self.snapshot.status_message = "Running process benchmark"
            return self.snapshot

        json_output_match = _JSON_OUTPUT_PATTERN.search(stripped)
        if json_output_match is not None:
            self.snapshot.status_message = "JSON summary written"
            self.snapshot.output_path = json_output_match.group(1)
            return self.snapshot

        if " | " in stripped:
            self._consume_result_row(stripped)

        return self.snapshot

    def _consume_result_row(self, line: str) -> None:
        columns = [part.strip() for part in line.split("|")]
        if len(columns) < 2:
            return
        run_name = columns[0]
        if not run_name:
            return

        workload = self._extract_workload(run_name)
        if workload is not None:
            self.snapshot.current_workload = workload

        if "baseline" in run_name:
            self._mark_phase_completed("baseline")
        elif "async" in run_name:
            self._mark_phase_completed("async")
        elif "process" in run_name:
            self._mark_phase_completed("process")
            if workload is not None:
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

    def _extract_workload(self, value: str) -> str | None:
        if self._workload_mode != "all":
            return self._workload_mode
        for workload in _WORKLOAD_NAMES:
            if f"-{workload}-" in value or value.startswith(f"{workload}-"):
                return workload
        return None
