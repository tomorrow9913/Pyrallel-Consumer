from __future__ import annotations

import asyncio
import os
import re
import signal
from importlib import import_module
from time import monotonic

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    LoadingIndicator,
    Log,
    ProgressBar,
    Static,
)

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.log_parser import BenchmarkProgressSnapshot
from benchmarks.tui.results_modal import ResultsSummaryModalScreen
from benchmarks.tui.results_report import render_results_summary
from benchmarks.tui.state import BenchmarkTuiState
from benchmarks.workloads import available_names

_PHASE_NAMES = ("baseline", "async", "process")
_ORDERING_NAMES = ("key_hash", "partition", "unordered")
_DONE_STYLE = "bold bright_green"
_SLOWER_THAN_BASELINE_STYLE = "bold bright_yellow"
_RUNNING_STYLE = "bold black on bright_cyan"
_WAITING_STYLE = "grey62"
_FAILED_STYLE = "bold bright_red"
_CANCELLED_STYLE = "bold bright_yellow"
_ACTIVE_ROW_STYLE = "bold bright_cyan"
_METRICS_PORT_PID_PATTERN = re.compile(
    r"Metrics port \d+ is already in use\(PID ([0-9,\s]+)\)"
)


def _format_elapsed(seconds: float) -> str:
    """Handle format elapsed within run."""
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


class RunScreen(Screen[None]):
    """Represent run screen data used by run."""

    BINDINGS = [
        ("c", "cancel", "Cancel"),
        ("s", "settings", "Settings"),
        ("x", "exit", "Exit"),
    ]

    def __init__(self, state: BenchmarkTuiState) -> None:
        super().__init__()
        self._state = state
        self._controller: BenchmarkProcessController | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._elapsed_timer: Timer | None = None
        self._cancelled = False
        self._closed = False
        self._completed_successfully = False
        self._last_error_line: str | None = None
        self._terminal_reason = ""
        self._terminal_cells: dict[tuple[str, str], str] = {}
        self._started_at = monotonic()
        self._current_run_started_at = self._started_at
        self._current_run_identity: tuple[str, str, str] | None = None
        self._finished_at: float | None = None
        self._active_workloads = self._resolve_workloads()
        self._active_orderings = self._resolve_orderings()
        self._active_phases = self._resolve_phases()
        self._total_runs = (
            len(self._active_workloads)
            * len(self._active_orderings)
            * len(self._active_phases)
        )
        self._latest_output_path = state.json_output or None
        self._last_snapshot = BenchmarkProgressSnapshot(total_runs=self._total_runs)

    def compose(self) -> ComposeResult:
        """Handle compose within run."""
        yield Header()
        with Container(id="run-layout"):
            with VerticalScroll(id="run-screen"):
                yield Static("벤치마크 실행 준비 중", id="run-status")
                with Container(id="run-spotlight-card"):
                    yield Static("", id="run-spotlight")
                    with Horizontal(id="run-chip-row"):
                        yield Static("", id="run-chip-workload", classes="status-chip")
                        yield Static("", id="run-chip-ordering", classes="status-chip")
                        yield Static("", id="run-chip-phase", classes="status-chip")
                    with Horizontal(id="run-phase-meta"):
                        yield Static(
                            "", id="phase-progress-badge", classes="status-badge"
                        )
                        yield Static(
                            "", id="current-run-elapsed", classes="status-badge"
                        )
                    yield ProgressBar(
                        total=None,
                        show_percentage=False,
                        show_eta=True,
                        id="phase-progress",
                    )
                    with Horizontal(id="run-overall-meta"):
                        yield Static("", id="progress-badge", classes="status-badge")
                        yield Static("", id="run-elapsed", classes="status-badge")
                    yield ProgressBar(
                        total=max(self._total_runs, 1),
                        show_percentage=False,
                        show_eta=True,
                        id="run-progress",
                    )
                    yield Static("", id="run-terminal-reason")
                yield Static("", id="run-output-path")
                with Horizontal(id="run-log-header"):
                    yield Static("실행 로그", id="run-log-title")
                    yield LoadingIndicator(id="run-loading")
                yield DataTable(id="run-summary")
                yield Log(id="run-log")
            with Container(id="run-actions"):
                yield Button(
                    "Kill PID?",
                    id="kill-metrics-port-button",
                    variant="error",
                )
                yield Button("Cancel run", id="cancel-button", variant="error")
                yield Button("Back to settings", id="settings-button")
                yield Button("Back", id="exit-button")
        yield Footer()

    def on_mount(self) -> None:
        """Handle on mount within run."""
        self._configure_run_summary_table()
        self._set_terminal_actions(show_report=False)
        self._render_snapshot(BenchmarkProgressSnapshot(total_runs=self._total_runs))
        self._set_loading(True)
        self._elapsed_timer = self.set_interval(0.5, self._refresh_elapsed)
        app_module = import_module("benchmarks.tui.app")
        controller_cls = getattr(
            app_module,
            "BenchmarkProcessController",
            BenchmarkProcessController,
        )
        self._controller = controller_cls(
            state=self._state,
            on_output=self._append_log,
            on_progress=self._render_snapshot,
            on_complete=self._on_complete,
        )
        self._run_task = asyncio.create_task(self._controller.run())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle on button pressed within run."""
        if event.button.id == "cancel-button":
            await self.action_cancel()
        elif event.button.id == "kill-metrics-port-button":
            self._kill_metrics_port_processes()
        elif event.button.id == "settings-button":
            await self.action_settings()
        elif event.button.id == "exit-button":
            await self.action_exit()

    async def action_cancel(self) -> None:
        """Handle action cancel within run."""
        if self._completed_successfully:
            self._open_results_report()
            return
        if self._run_task is None or self._run_task.done():
            return
        self._cancelled = True
        if self._controller is not None:
            await self._controller.cancel()
        if self._run_task is not None:
            await self._run_task

    async def action_settings(self) -> None:
        """Handle action settings within run."""
        if self._run_task is not None and not self._run_task.done():
            return
        self.app.pop_screen()

    async def action_exit(self) -> None:
        """Handle action exit within run."""
        if self._run_task is not None and not self._run_task.done():
            return
        self.app.exit()

    async def action_back(self) -> None:
        """Handle action back within run."""
        await self.action_exit()

    def on_unmount(self) -> None:
        """Handle on unmount within run."""
        self._closed = True
        if self._elapsed_timer is not None:
            self._elapsed_timer.stop()

    def _append_log(self, line: str, is_error: bool) -> None:
        """Handle append log within run."""
        if self._closed:
            return
        if is_error and line.strip():
            self._last_error_line = line.strip()
        log = self.query_one("#run-log", Log)
        prefix = "[stderr] " if is_error else ""
        log.write_line("%s%s" % (prefix, line))

    def _refresh_elapsed(self) -> None:
        """Refresh elapsed for run."""
        if self._closed:
            return
        self.query_one("#current-run-elapsed", Static).update(
            "현재 처리시간 %s" % _format_elapsed(self._current_run_elapsed_seconds())
        )
        self.query_one("#run-elapsed", Static).update(
            "전체 처리시간 %s" % _format_elapsed(self._elapsed_seconds())
        )
        self._render_spotlight(self._last_snapshot)

    def _elapsed_seconds(self) -> float:
        """Handle elapsed seconds within run."""
        end_time = self._finished_at if self._finished_at is not None else monotonic()
        return end_time - self._started_at

    def _current_run_elapsed_seconds(self) -> float:
        """Handle current run elapsed seconds within run."""
        end_time = self._finished_at if self._finished_at is not None else monotonic()
        return max(0.0, end_time - self._current_run_started_at)

    def _render_snapshot(self, snapshot: BenchmarkProgressSnapshot) -> None:
        """Handle render snapshot within run."""
        if self._closed:
            return
        self._last_snapshot = snapshot
        if snapshot.output_path is not None:
            self._latest_output_path = snapshot.output_path
        self._sync_current_run_timing(snapshot)
        self.query_one("#run-status", Static).update(self._status_line(snapshot))
        self._render_spotlight(snapshot)
        self.query_one("#current-run-elapsed", Static).update(
            "현재 처리시간 %s" % _format_elapsed(self._current_run_elapsed_seconds())
        )
        self.query_one("#run-elapsed", Static).update(
            "전체 처리시간 %s" % _format_elapsed(self._elapsed_seconds())
        )
        self._render_output_path()
        current_target = snapshot.current_run_target_messages or None
        current_processed = snapshot.current_run_processed_messages
        overall_total = max(snapshot.total_runs or self._total_runs, 1)
        self.query_one("#phase-progress-badge", Static).update(
            "현재 %d / %s 메시지"
            % (
                current_processed,
                str(current_target) if current_target is not None else "--",
            )
        )
        self.query_one("#phase-progress", ProgressBar).update(
            total=current_target,
            progress=float(current_processed),
        )
        self.query_one("#progress-badge", Static).update(
            "전체 %d / %d 벤치마크"
            % (snapshot.completed_runs, snapshot.total_runs or self._total_runs)
        )
        self.query_one("#run-progress", ProgressBar).update(
            total=overall_total,
            progress=float(snapshot.completed_runs),
        )
        self._update_run_summary_table(snapshot)

    def _on_complete(self, return_code: int) -> None:
        """Handle on complete within run."""
        if self._closed:
            return
        self._finished_at = monotonic()
        self._set_loading(False)

        if self._cancelled:
            self.query_one("#run-status", Static).update("벤치마크가 취소되었습니다")
            self._terminal_reason = "종료 사유: 사용자가 실행을 취소했습니다."
            self._mark_terminal_cell("CANCELLED")
            self._set_terminal_actions(show_report=False)
            self._refresh_elapsed()
            return

        if return_code == 0:
            self._completed_successfully = True
            self._force_completion_progress()
            self.query_one("#run-status", Static).update("벤치마크가 완료되었습니다")
            self._terminal_reason = "다음 실험을 위해 설정으로 바로 돌아갈 수 있습니다."
            self._set_terminal_actions(show_report=True)
            self._refresh_elapsed()
            self._open_results_report()
            return

        self.query_one("#run-status", Static).update("벤치마크가 실패했습니다")
        reason = self._last_error_line or "exit=%d" % return_code
        self._terminal_reason = "종료 사유: %s" % reason
        self._mark_terminal_cell("FAILED")
        self._set_terminal_actions(show_report=False)
        self._refresh_elapsed()

    def _open_results_report(self) -> None:
        """Handle open results report within run."""
        self.app.push_screen(
            ResultsSummaryModalScreen(
                self._build_results_summary(), self._latest_output_path
            ),
            callback=self._handle_results_modal_result,
        )

    def _handle_results_modal_result(self, result: str | None) -> None:
        """Handle results modal result for run."""
        if result == "settings":
            self.app.pop_screen()

    def _set_terminal_actions(self, *, show_report: bool) -> None:
        """Install or update terminal actions for run."""
        cancel_button = self.query_one("#cancel-button", Button)
        kill_button = self.query_one("#kill-metrics-port-button", Button)
        settings_button = self.query_one("#settings-button", Button)
        exit_button = self.query_one("#exit-button", Button)
        is_terminal = self._finished_at is not None or self._cancelled
        metrics_pids = self._metrics_port_error_pids()

        if show_report:
            cancel_button.label = "View results"
            cancel_button.variant = "primary"
            cancel_button.display = True
        elif not is_terminal:
            cancel_button.label = "Cancel run"
            cancel_button.variant = "error"
            cancel_button.display = True
        else:
            cancel_button.display = False

        if is_terminal and not show_report and metrics_pids:
            kill_button.label = "Kill PID %s?" % self._format_pids(metrics_pids)
            kill_button.variant = "error"
            kill_button.display = True
        else:
            kill_button.display = False

        settings_button.display = is_terminal
        exit_button.display = is_terminal
        exit_button.label = "Exit"

    def _metrics_port_error_pids(self) -> tuple[int, ...]:
        """Return PIDs from the metrics-port-in-use failure message."""
        if self._last_error_line is None:
            return ()
        match = _METRICS_PORT_PID_PATTERN.search(self._last_error_line)
        if match is None:
            return ()
        pids: list[int] = []
        for token in match.group(1).split(","):
            token = token.strip()
            if token.isdigit():
                pids.append(int(token))
        return tuple(dict.fromkeys(pids))

    @staticmethod
    def _format_pids(pids: tuple[int, ...]) -> str:
        """Format process ids for terminal action labels."""
        return ",".join(str(pid) for pid in pids)

    def _kill_metrics_port_processes(self) -> None:
        """Send SIGTERM to PIDs reported by the metrics port conflict."""
        pids = self._metrics_port_error_pids()
        if not pids:
            return
        killed: list[str] = []
        failed: list[str] = []
        for pid in pids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                killed.append("%d(already exited)" % pid)
            except PermissionError:
                failed.append("%d(permission denied)" % pid)
            except OSError as exc:
                failed.append("%d(%s)" % (pid, exc))
            else:
                killed.append(str(pid))
        if failed:
            self._terminal_reason = "종료 실패: %s | kill 대상: %s" % (
                ", ".join(failed),
                ", ".join(str(pid) for pid in pids),
            )
        else:
            self._terminal_reason = "종료 신호 전송: PID %s" % ", ".join(killed)
            self.query_one("#kill-metrics-port-button", Button).display = False
        self._render_spotlight(self._last_snapshot)

    def _configure_run_summary_table(self) -> None:
        """Handle configure run summary table within run."""
        table = self.query_one("#run-summary", DataTable)
        table.cursor_type = "none"
        table.add_column("Workload", key="workload")
        table.add_column("Ordering", key="ordering")
        for phase in self._active_phases:
            table.add_column(phase.title(), key=phase)
        for workload in self._active_workloads:
            for ordering in self._active_orderings:
                row_values = [workload, ordering]
                row_values.extend("WAITING" for _ in self._active_phases)
                table.add_row(*row_values, key=self._row_key(workload, ordering))

    def _update_run_summary_table(self, snapshot: BenchmarkProgressSnapshot) -> None:
        """Update run summary table for run."""
        table = self.query_one("#run-summary", DataTable)
        active_row_key = self._active_row_key(snapshot)
        active_phase = self._current_phase(snapshot)
        baseline_averages = self._baseline_averages_by_workload(snapshot)

        for workload in self._active_workloads:
            for ordering in self._active_orderings:
                row_key = self._row_key(workload, ordering)
                is_active_row = row_key == active_row_key
                table.update_cell(
                    row_key,
                    "workload",
                    self._identity_cell_text(workload, is_active_row),
                    update_width=True,
                )
                table.update_cell(
                    row_key,
                    "ordering",
                    self._identity_cell_text(ordering, is_active_row),
                    update_width=True,
                )

                row = snapshot.tps_by_workload_ordering.get(workload, {}).get(
                    ordering, {}
                )
                for phase in self._active_phases:
                    phase_key = (row_key, phase)
                    if phase_key in self._terminal_cells:
                        value = self._status_text(
                            self._terminal_cells[phase_key],
                            self._terminal_style(self._terminal_cells[phase_key]),
                        )
                    elif is_active_row and phase == active_phase:
                        value = self._status_text("RUNNING", _RUNNING_STYLE)
                    elif row.get(phase, "--") != "--":
                        value = self._status_text(
                            "%s TPS" % row[phase],
                            self._result_style(
                                row[phase],
                                baseline_averages.get(workload)
                                if phase != "baseline"
                                else None,
                            ),
                        )
                    else:
                        value = self._status_text("WAITING", _WAITING_STYLE)
                    table.update_cell(row_key, phase, value, update_width=True)

    def _baseline_averages_by_workload(
        self, snapshot: BenchmarkProgressSnapshot
    ) -> dict[str, float]:
        """Calculate baseline average TPS by workload for result coloring."""
        averages: dict[str, float] = {}
        for workload in self._active_workloads:
            baseline_values: list[float] = []
            ordering_rows = snapshot.tps_by_workload_ordering.get(workload, {})
            for ordering in self._active_orderings:
                value = ordering_rows.get(ordering, {}).get("baseline", "--")
                parsed_value = self._parse_tps(value)
                if parsed_value is not None:
                    baseline_values.append(parsed_value)
            if baseline_values:
                averages[workload] = sum(baseline_values) / len(baseline_values)
        return averages

    def _result_style(self, value: str, baseline_average: float | None) -> str:
        """Style completed result cells relative to workload baseline average."""
        parsed_value = self._parse_tps(value)
        if (
            baseline_average is not None
            and parsed_value is not None
            and parsed_value < baseline_average
        ):
            return _SLOWER_THAN_BASELINE_STYLE
        return _DONE_STYLE

    def _build_results_summary(self) -> str:
        """Build results summary for run."""
        if self._latest_output_path is None:
            return "No JSON summary was reported by the benchmark run."
        return render_results_summary(self._latest_output_path)

    def _mark_terminal_cell(self, status: str) -> None:
        """Handle mark terminal cell within run."""
        workload = self._last_snapshot.current_workload
        ordering = self._last_snapshot.current_ordering
        phase = self._current_phase(self._last_snapshot)
        if phase is None:
            phase = self._last_running_phase()
        if ordering is None and len(self._active_orderings) == 1:
            ordering = self._active_orderings[0]
        if workload is None or ordering is None or phase is None:
            return
        self._terminal_cells[(self._row_key(workload, ordering), phase)] = status
        self._update_run_summary_table(self._last_snapshot)

    def _last_running_phase(self) -> str | None:
        """Handle last running phase within run."""
        for phase in self._active_phases:
            if self._last_snapshot.phase_statuses.get(phase) == "running":
                return phase
        return None

    def _set_loading(self, is_running: bool) -> None:
        """Install or update loading for run."""
        self.query_one("#run-loading", LoadingIndicator).display = is_running

    def _force_completion_progress(self) -> None:
        """Force completion progress in run."""
        self._last_snapshot.completed_runs = self._total_runs
        overall_total = max(self._total_runs, 1)
        current_target = self._last_snapshot.current_run_target_messages or None
        current_processed = self._last_snapshot.current_run_processed_messages
        if current_target is not None:
            current_processed = max(current_processed, current_target)
        self.query_one("#phase-progress-badge", Static).update(
            "현재 %d / %s 메시지"
            % (
                current_processed,
                str(current_target) if current_target is not None else "--",
            )
        )
        self.query_one("#phase-progress", ProgressBar).update(
            total=current_target,
            progress=float(current_processed),
        )
        self.query_one("#progress-badge", Static).update(
            "전체 %d / %d 벤치마크" % (self._total_runs, self._total_runs)
        )
        self.query_one("#run-progress", ProgressBar).update(
            total=overall_total,
            progress=float(self._total_runs),
        )

    def _render_spotlight(self, snapshot: BenchmarkProgressSnapshot) -> None:
        """Handle render spotlight within run."""
        spotlight = self.query_one("#run-spotlight", Static)
        workload_chip = self.query_one("#run-chip-workload", Static)
        ordering_chip = self.query_one("#run-chip-ordering", Static)
        phase_chip = self.query_one("#run-chip-phase", Static)
        phase = self._current_phase(snapshot)
        if (
            snapshot.current_workload is None
            or snapshot.current_ordering is None
            or phase is None
        ):
            spotlight.update("현재 실행")
            workload_chip.update("workload: 대기 중")
            ordering_chip.update("ordering: 대기 중")
            phase_chip.update("engine: 대기 중")
        else:
            spotlight.update("현재 실행")
            workload_chip.update("workload: %s" % snapshot.current_workload)
            ordering_chip.update("ordering: %s" % snapshot.current_ordering)
            phase_chip.update("engine: %s" % phase)
        self._update_chip_classes(workload_chip, ordering_chip, phase_chip, phase)
        terminal_reason = self.query_one("#run-terminal-reason", Static)
        terminal_reason.update(self._terminal_reason)
        terminal_reason.display = bool(self._terminal_reason)
        terminal_reason.remove_class("is-failed", "is-cancelled", "is-complete")
        if self._cancelled:
            terminal_reason.add_class("is-cancelled")
        elif self._finished_at is not None and not self._completed_successfully:
            terminal_reason.add_class("is-failed")
        elif self._completed_successfully:
            terminal_reason.add_class("is-complete")

    def _render_output_path(self) -> None:
        """Handle render output path within run."""
        output = self.query_one("#run-output-path", Static)
        if self._latest_output_path is None:
            output.update("")
            return
        output.update("Output: %s" % self._latest_output_path)

    def _status_line(self, snapshot: BenchmarkProgressSnapshot) -> str:
        """Handle status line within run."""
        if self._cancelled:
            return "벤치마크가 취소되었습니다"
        if self._completed_successfully:
            return "벤치마크가 완료되었습니다"
        if self._finished_at is not None:
            return "벤치마크가 실패했습니다"
        if snapshot.current_workload is None:
            return "벤치마크 실행 준비 중"
        return "벤치마크 실행 중"

    def _update_chip_classes(
        self,
        workload_chip: Static,
        ordering_chip: Static,
        phase_chip: Static,
        phase: str | None,
    ) -> None:
        """Update chip classes for run."""
        for chip in (workload_chip, ordering_chip, phase_chip):
            chip.remove_class("is-waiting", "is-running", "is-done", "is-failed")
        if self._finished_at is not None and not self._completed_successfully:
            phase_chip.add_class("is-failed")
            workload_chip.add_class("is-failed")
            ordering_chip.add_class("is-failed")
            return
        if self._completed_successfully:
            phase_chip.add_class("is-done")
            workload_chip.add_class("is-done")
            ordering_chip.add_class("is-done")
            return
        if phase is None:
            phase_chip.add_class("is-waiting")
            workload_chip.add_class("is-waiting")
            ordering_chip.add_class("is-waiting")
            return
        phase_chip.add_class("is-running")
        workload_chip.add_class("is-running")
        ordering_chip.add_class("is-running")

    def _current_phase(self, snapshot: BenchmarkProgressSnapshot) -> str | None:
        """Handle current phase within run."""
        for phase in self._active_phases:
            if snapshot.phase_statuses.get(phase) == "running":
                return phase
        lowered = snapshot.status_message.lower()
        for phase in self._active_phases:
            if phase in lowered and "running" in lowered:
                return phase
        if snapshot.current_workload is not None and self._finished_at is not None:
            for phase in reversed(self._active_phases):
                if snapshot.phase_statuses.get(phase) in {"running", "completed"}:
                    return phase
        return None

    def _active_row_key(self, snapshot: BenchmarkProgressSnapshot) -> str | None:
        """Handle active row key within run."""
        if snapshot.current_workload is None or snapshot.current_ordering is None:
            return None
        return self._row_key(snapshot.current_workload, snapshot.current_ordering)

    def _sync_current_run_timing(self, snapshot: BenchmarkProgressSnapshot) -> None:
        """Handle sync current run timing within run."""
        phase = self._current_phase(snapshot)
        if (
            snapshot.current_workload is None
            or snapshot.current_ordering is None
            or phase is None
        ):
            return
        identity = (
            snapshot.current_workload,
            snapshot.current_ordering,
            phase,
        )
        if identity != self._current_run_identity:
            self._current_run_identity = identity
            self._current_run_started_at = monotonic()

    @staticmethod
    def _status_text(content: str, style: str) -> Text:
        """Handle status text within run."""
        return Text(content, style=style)

    @staticmethod
    def _parse_tps(value: str) -> float | None:
        """Parse TPS value for result comparisons."""
        try:
            return float(value.replace(",", ""))
        except ValueError:
            return None

    @staticmethod
    def _identity_cell_text(content: str, is_active: bool) -> str | Text:
        """Handle identity cell text within run."""
        if not is_active:
            return content
        return Text("▶ %s" % content, style=_ACTIVE_ROW_STYLE)

    @staticmethod
    def _terminal_style(status: str) -> str:
        """Handle terminal style within run."""
        if status == "FAILED":
            return _FAILED_STYLE
        if status == "CANCELLED":
            return _CANCELLED_STYLE
        return _WAITING_STYLE

    def _resolve_workloads(self) -> tuple[str, ...]:
        """Resolve workloads for run."""
        workloads = tuple(workload for workload in self._state.workloads if workload)
        if workloads:
            return workloads
        return available_names()[:1]

    def _resolve_orderings(self) -> tuple[str, ...]:
        """Resolve orderings for run."""
        return tuple(
            ordering
            for ordering in self._state.ordering_modes
            if ordering in _ORDERING_NAMES
        )

    def _row_key(self, workload: str, ordering: str) -> str:
        """Handle row key within run."""
        return "%s-%s" % (workload, ordering)

    def _resolve_phases(self) -> tuple[str, ...]:
        """Resolve phases for run."""
        phases: list[str] = []
        if not self._state.skip_baseline:
            phases.append("baseline")
        if not self._state.skip_async:
            phases.append("async")
        if not self._state.skip_process:
            phases.append("process")
        return tuple(phases)
