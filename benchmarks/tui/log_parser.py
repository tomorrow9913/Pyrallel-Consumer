from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass, field

from benchmarks.workloads import available_names

_PHASE_NAMES = ("baseline", "async", "process")
_FALLBACK_WORKLOAD_NAMES = ("sleep", "cpu", "io")
_ORDERING_NAMES = ("key_hash", "partition", "unordered")
_TOPIC_PATTERN = re.compile(r"topic '([^']+)'")
_JSON_OUTPUT_PATTERN = re.compile(r"JSON summary written to (.+)$")
_FINAL_TPS_PATTERN = re.compile(r"Final TPS:\s*([0-9]+(?:\.[0-9]+)?)")
_STRICT_SUFFIX_PATTERN = re.compile(r"(?:^|-)strict-(on|off)(?:$|[.-])")
_WILL_PROCESS_PATTERN = re.compile(r"Will process up to (\d+) messages")
_TARGET_MESSAGES_PATTERN = re.compile(r"Target messages to process:\s*(\d+)")
_PROCESSED_MESSAGES_PATTERN = re.compile(r"Processed (\d+) messages")
_TOTAL_MESSAGES_PROCESSED_PATTERN = re.compile(
    r"Total messages processed(?: \(approx\))?:\s*(\d+)"
)

RunIdentity = tuple[str, str, str, str]


def _default_workload_names() -> tuple[str, ...]:
    """Return registry available workloads with a stable built-in fallback."""
    names = available_names()
    return names or _FALLBACK_WORKLOAD_NAMES


def _empty_tps_table(
    workloads: tuple[str, ...] | None = None,
) -> dict[str, dict[str, str]]:
    """Handle empty tps table within log parser."""
    workload_names = workloads or _default_workload_names()
    return {
        workload: {phase: "--" for phase in _PHASE_NAMES} for workload in workload_names
    }


def _empty_ordering_tps_table(
    workloads: tuple[str, ...] | None = None,
) -> dict[str, dict[str, dict[str, str]]]:
    """Handle empty ordering tps table within log parser."""
    workload_names = workloads or _default_workload_names()
    return {
        workload: {
            ordering: {phase: "--" for phase in _PHASE_NAMES}
            for ordering in _ORDERING_NAMES
        }
        for workload in workload_names
    }


@dataclass(slots=True)
class BenchmarkProgressSnapshot:
    """Capture runtime state for log parser."""

    status_message: str = "Waiting to start"
    phase_statuses: dict[str, str] = field(
        default_factory=lambda: {phase: "pending" for phase in _PHASE_NAMES}
    )
    workload_statuses: dict[str, str] = field(
        default_factory=lambda: {
            workload: "pending" for workload in _default_workload_names()
        }
    )
    current_workload: str | None = None
    current_ordering: str | None = None
    current_run_target_messages: int = 0
    current_run_processed_messages: int = 0
    output_path: str | None = None
    completed_runs: int = 0
    total_runs: int = 0
    progress_value: float = 0.0
    tps_by_workload: dict[str, dict[str, str]] = field(default_factory=_empty_tps_table)
    tps_by_workload_ordering: dict[str, dict[str, dict[str, str]]] = field(
        default_factory=_empty_ordering_tps_table
    )


class BenchmarkLogParser:
    """Represent benchmark log parser data used by log parser."""

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
        self._started_runs: set[RunIdentity] = set()
        self._completed_runs: set[RunIdentity] = set()
        self._active_run: RunIdentity | None = None
        self._started_run_order: deque[RunIdentity] = deque()
        self._base_total_runs = (
            len(self._active_workloads)
            * len(self._active_orderings)
            * len(self._active_phases)
        )
        self._strict_variants_by_base: dict[
            tuple[str, str, str], set[str]
        ] = defaultdict(set)
        self.snapshot = BenchmarkProgressSnapshot(
            total_runs=self._base_total_runs,
            workload_statuses={
                workload: "pending" for workload in self._active_workloads
            },
            tps_by_workload=_empty_tps_table(self._active_workloads),
            tps_by_workload_ordering=_empty_ordering_tps_table(self._active_workloads),
        )

    def consume(self, line: str) -> BenchmarkProgressSnapshot:
        """Handle consume within log parser."""
        stripped = line.strip()
        if not stripped:
            return self.snapshot

        if "Resetting benchmark topics/groups:" in stripped:
            self.snapshot.status_message = "Resetting topics/groups"
            return self.snapshot

        will_process_match = _WILL_PROCESS_PATTERN.search(stripped)
        if will_process_match is not None:
            self.snapshot.current_run_target_messages = int(will_process_match.group(1))
            return self.snapshot

        target_messages_match = _TARGET_MESSAGES_PATTERN.search(stripped)
        if target_messages_match is not None:
            self.snapshot.current_run_target_messages = int(
                target_messages_match.group(1)
            )
            return self.snapshot

        processed_messages_match = _PROCESSED_MESSAGES_PATTERN.search(stripped)
        if processed_messages_match is not None:
            self.snapshot.current_run_processed_messages = int(
                processed_messages_match.group(1)
            )

        total_processed_match = _TOTAL_MESSAGES_PROCESSED_PATTERN.search(stripped)
        if total_processed_match is not None:
            self.snapshot.current_run_processed_messages = int(
                total_processed_match.group(1)
            )

        topic_match = _TOPIC_PATTERN.search(stripped)
        topic_name = topic_match.group(1) if topic_match else None

        if "Starting baseline consumer" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            ordering = self._extract_ordering(topic_name)
            strict_mode = self._extract_strict_mode(topic_name)
            self._mark_workload_running(workload)
            self._mark_ordering_running(ordering)
            self._mark_phase_running("baseline")
            self._mark_run_started(workload, ordering, strict_mode, "baseline")
            self.snapshot.status_message = "Running baseline"
            return self.snapshot

        if "Starting PyrallelConsumer test" in stripped and topic_name is not None:
            workload = self._extract_workload(topic_name)
            ordering = self._extract_ordering(topic_name)
            strict_mode = self._extract_strict_mode(topic_name)
            self._mark_workload_running(workload)
            self._mark_ordering_running(ordering)
            phase = self._extract_phase(topic_name)
            if phase == "async":
                self._mark_phase_completed("baseline")
                self._mark_phase_running("async")
                self._mark_run_started(workload, ordering, strict_mode, "async")
                self.snapshot.status_message = "Running async benchmark"
            elif phase == "process":
                self._mark_phase_completed("async")
                self._mark_phase_running("process")
                self._mark_run_started(workload, ordering, strict_mode, "process")
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
        """Handle consume result row within log parser."""
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
        strict_mode = self._extract_strict_mode(run_name)
        if strict_mode == "" and topic_name:
            strict_mode = self._extract_strict_mode(topic_name)
        phase = self._extract_phase(run_name)
        if phase is None and run_type:
            phase = self._extract_phase(run_type)
        if phase is None and topic_name:
            phase = self._extract_phase(topic_name)

        if workload is not None:
            self.snapshot.current_workload = workload
        if ordering is not None:
            self.snapshot.current_ordering = ordering
        if workload is None or ordering is None or phase is None:
            return

        self._mark_run_completed(workload, ordering, strict_mode, phase)
        self.snapshot.tps_by_workload[workload][phase] = throughput
        self.snapshot.tps_by_workload_ordering[workload][ordering][phase] = throughput
        self._mark_phase_completed(phase)

        if phase == self._active_phases[-1]:
            self.snapshot.workload_statuses[workload] = "completed"

    def _mark_workload_running(self, workload: str | None) -> None:
        """Handle mark workload running within log parser."""
        if workload is None:
            return
        self.snapshot.current_workload = workload
        if self.snapshot.workload_statuses[workload] == "pending":
            self.snapshot.workload_statuses[workload] = "running"

    def _mark_phase_running(self, phase: str) -> None:
        """Handle mark phase running within log parser."""
        self.snapshot.phase_statuses[phase] = "running"

    def _mark_phase_completed(self, phase: str) -> None:
        """Handle mark phase completed within log parser."""
        self.snapshot.phase_statuses[phase] = "completed"

    def _mark_ordering_running(self, ordering: str | None) -> None:
        """Handle mark ordering running within log parser."""
        if ordering is not None:
            self.snapshot.current_ordering = ordering

    def _mark_run_started(
        self,
        workload: str | None,
        ordering: str | None,
        strict_mode: str,
        phase: str,
    ) -> None:
        """Handle mark run started within log parser."""
        if workload is None or ordering is None or phase not in self._active_phases:
            return
        self.snapshot.current_ordering = ordering
        self.snapshot.current_run_processed_messages = 0
        self.snapshot.current_run_target_messages = 0
        self._record_run_variant(workload, ordering, strict_mode, phase)
        key = (workload, ordering, strict_mode, phase)
        if key not in self._started_runs:
            self._started_run_order.append(key)
        self._started_runs.add(key)
        self._active_run = key
        self._refresh_progress()

    def _mark_run_completed(
        self, workload: str, ordering: str, strict_mode: str, phase: str
    ) -> None:
        """Handle mark run completed within log parser."""
        self._record_run_variant(workload, ordering, strict_mode, phase)
        key = (workload, ordering, strict_mode, phase)
        self._started_runs.add(key)
        self._completed_runs.add(key)
        if self._active_run == key:
            self._active_run = None
        self._refresh_progress()

    def _record_run_variant(
        self, workload: str, ordering: str, strict_mode: str, phase: str
    ) -> None:
        """Record run variant for log parser."""
        if strict_mode:
            self._strict_variants_by_base[(workload, ordering, phase)].add(strict_mode)

        extra_variant_runs = sum(
            max(0, len(variants) - 1)
            for variants in self._strict_variants_by_base.values()
        )
        self.snapshot.total_runs = self._base_total_runs + extra_variant_runs

    def _consume_final_tps(self, throughput: str) -> None:
        """Handle consume final tps within log parser."""
        target_run = self._next_unfinished_run()
        if target_run is None:
            return
        workload, ordering, strict_mode, phase = target_run
        self.snapshot.current_workload = workload
        self.snapshot.current_ordering = ordering
        self.snapshot.tps_by_workload[workload][phase] = throughput
        self.snapshot.tps_by_workload_ordering[workload][ordering][phase] = throughput
        self._mark_phase_completed(phase)
        self._mark_run_completed(workload, ordering, strict_mode, phase)
        if phase == self._active_phases[-1]:
            self.snapshot.workload_statuses[workload] = "completed"
        self._active_run = self._next_unfinished_run()

    def _refresh_progress(self) -> None:
        """Refresh progress for log parser."""
        self.snapshot.completed_runs = len(self._completed_runs)
        active_runs = len(self._started_runs - self._completed_runs)
        self.snapshot.progress_value = min(
            float(self.snapshot.total_runs),
            float(self.snapshot.completed_runs) + (0.5 * active_runs),
        )

    def _next_unfinished_run(self) -> RunIdentity | None:
        """Handle next unfinished run within log parser."""
        while self._started_run_order:
            candidate = self._started_run_order[0]
            if candidate in self._completed_runs:
                self._started_run_order.popleft()
                continue
            return candidate
        return None

    def _extract_phase(self, value: str) -> str | None:
        """Handle extract phase within log parser."""
        if re.search(r"(?:^|-)async(?:$|-strict-)", value):
            return "async"
        if re.search(r"(?:^|-)process(?:$|-strict-)", value):
            return "process"
        if "baseline" in value:
            return "baseline"
        return None

    def _extract_strict_mode(self, value: str) -> str:
        """Handle extract strict mode within log parser."""
        strict_mode_match = _STRICT_SUFFIX_PATTERN.search(value)
        if strict_mode_match is None:
            return ""
        return strict_mode_match.group(1)

    def _extract_workload(self, value: str) -> str | None:
        """Extract the workload from a run or topic name.

        Topic names can include arbitrary prefixes. Match the workload by its
        position immediately before the ordering/phase suffix instead of the
        first workload-looking token in the prefix.
        """
        if self._workload_mode != "all":
            return self._workload_mode

        candidate = self._extract_workload_candidate(value)
        if candidate in self._active_workloads:
            return candidate
        return None

    def _extract_workload_candidate(self, value: str) -> str | None:
        """Return the suffix-position workload token from a topic/run name."""
        parts = value.split("-")
        for index in range(len(parts) - 1, -1, -1):
            if parts[index] not in _PHASE_NAMES:
                continue
            prefix_parts = parts[:index]
            if prefix_parts and prefix_parts[-1] == "pyrallel":
                prefix_parts = prefix_parts[:-1]
            if prefix_parts and prefix_parts[-1] in self._active_orderings:
                prefix_parts = prefix_parts[:-1]
            if prefix_parts:
                return prefix_parts[-1]
        return None

    def _extract_ordering(self, value: str) -> str | None:
        """Handle extract ordering within log parser."""
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
        """Resolve active workloads for log parser."""
        if self._workload_mode == "all":
            return _default_workload_names()
        return (self._workload_mode,)
