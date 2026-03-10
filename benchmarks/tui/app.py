from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    Collapsible,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    LoadingIndicator,
    Log,
    ProgressBar,
    Select,
    Static,
    Switch,
)

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.log_parser import BenchmarkProgressSnapshot
from benchmarks.tui.option_help import OPTION_HELP, PROFILING_CONTROL_IDS
from benchmarks.tui.path_picker import DirectoryPickerScreen
from benchmarks.tui.results_report import (
    load_results_table_data,
    render_results_summary,
    summarize_results_overview,
    summarize_workload_winners,
)
from benchmarks.tui.state import BenchmarkTuiState

_PHASE_NAMES = ("baseline", "async", "process")
_WORKLOAD_NAMES = ("sleep", "cpu", "io")


def _safe_int(value: str, default: int) -> int:
    try:
        return int(value)
    except ValueError:
        return default


def _safe_float(value: str, default: float) -> float:
    try:
        return float(value)
    except ValueError:
        return default


class OptionsScreen(Screen[None]):
    BINDINGS = [("q", "app.quit", "Quit")]

    @staticmethod
    def _field_label(text: str) -> Label:
        return Label(text, classes="field-label")

    @staticmethod
    def _option_help(option_id: str) -> Static:
        return Static(OPTION_HELP[option_id].description, classes="option-help")

    @staticmethod
    def _option_block_id(option_id: str) -> str:
        return "option-block-%s" % option_id

    @staticmethod
    def _section_id(section_slug: str) -> str:
        return "option-section-%s" % section_slug

    @staticmethod
    def _section_title(text: str) -> Label:
        return Label(text, classes="option-section-title")

    @staticmethod
    def _section_description(text: str) -> Static:
        return Static(text, classes="option-section-description")

    @classmethod
    def _labeled_input(
        cls,
        *,
        option_id: str,
        value: str,
        widget_id: str,
        placeholder: str | None = None,
    ) -> ComposeResult:
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            if option.browse:
                with Container(classes="input-with-browse"):
                    yield Input(value=value, id=widget_id, placeholder=placeholder)
                    yield Button(
                        "Browse",
                        id="browse-%s" % widget_id,
                        classes="browse-button",
                    )
            else:
                yield Input(value=value, id=widget_id, placeholder=placeholder)

    @classmethod
    def _labeled_select(
        cls,
        *,
        option_id: str,
        options: list[tuple[str, str]],
        value: str,
        widget_id: str,
    ) -> ComposeResult:
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            yield Select(
                options,
                value=value,
                id=widget_id,
                allow_blank=False,
            )

    @classmethod
    def _switch_field(
        cls, *, option_id: str, value: bool, widget_id: str
    ) -> ComposeResult:
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            yield Switch(value=value, id=widget_id)

    def compose(self) -> ComposeResult:
        state = BenchmarkTuiState()
        yield Header()
        with VerticalScroll(id="options-screen"):
            yield Static(
                "Benchmark options",
                id="options-title",
                classes="screen-title",
            )
            with Container(
                id=self._section_id("cluster-workload"),
                classes="option-section",
            ):
                yield self._section_title("Cluster & workload")
                yield self._section_description(
                    "Configure the Kafka target and choose the workload shape to run."
                )
                yield from self._labeled_input(
                    option_id="bootstrap-servers",
                    value=state.bootstrap_servers,
                    widget_id="bootstrap-servers",
                    placeholder="localhost:9092",
                )
                yield from self._labeled_input(
                    option_id="num-messages",
                    value=str(state.num_messages),
                    widget_id="num-messages",
                    placeholder="100000",
                )
                yield from self._labeled_input(
                    option_id="num-keys",
                    value=str(state.num_keys),
                    widget_id="num-keys",
                    placeholder="100",
                )
                yield from self._labeled_input(
                    option_id="num-partitions",
                    value=str(state.num_partitions),
                    widget_id="num-partitions",
                    placeholder="8",
                )
                yield from self._labeled_input(
                    option_id="timeout-sec",
                    value=str(state.timeout_sec),
                    widget_id="timeout-sec",
                    placeholder="60",
                )
                yield from self._labeled_select(
                    option_id="workload",
                    options=[
                        ("sleep", "sleep"),
                        ("cpu", "cpu"),
                        ("io", "io"),
                        ("all", "all"),
                    ],
                    value=state.workload,
                    widget_id="workload",
                )
                yield from self._labeled_input(
                    option_id="worker-sleep-ms",
                    value=str(state.worker_sleep_ms),
                    widget_id="worker-sleep-ms",
                    placeholder="0.5",
                )
                yield from self._labeled_input(
                    option_id="worker-cpu-iterations",
                    value=str(state.worker_cpu_iterations),
                    widget_id="worker-cpu-iterations",
                    placeholder="1000",
                )
                yield from self._labeled_input(
                    option_id="worker-io-sleep-ms",
                    value=str(state.worker_io_sleep_ms),
                    widget_id="worker-io-sleep-ms",
                    placeholder="0.5",
                )
            with Container(
                id=self._section_id("output-execution"),
                classes="option-section",
            ):
                yield self._section_title("Output & execution")
                yield self._section_description(
                    "Choose where results land and whether benchmark topics are reset first."
                )
                yield from self._labeled_input(
                    option_id="json-output",
                    value=state.json_output,
                    widget_id="json-output",
                    placeholder="benchmarks/results/benchmark-results.json",
                )
                yield from self._switch_field(
                    option_id="skip-reset",
                    value=state.skip_reset,
                    widget_id="skip-reset",
                )
            with Container(
                id=self._section_id("profiling"),
                classes="option-section",
            ):
                yield self._section_title("Profiling")
                yield self._section_description(
                    "Enable optional profilers without changing benchmark dashboard behavior."
                )
                yield from self._switch_field(
                    option_id="profiling-enabled",
                    value=state.profiling_enabled,
                    widget_id="profiling-enabled",
                )
                yield from self._switch_field(
                    option_id="profile",
                    value=state.profile,
                    widget_id="profile",
                )
                yield from self._switch_field(
                    option_id="py-spy",
                    value=state.py_spy,
                    widget_id="py-spy",
                )
            with Container(
                id=self._section_id("advanced-options"),
                classes="option-section",
            ):
                yield self._section_title("Advanced options")
                yield self._section_description(
                    "Expand for execution-mode skips, logging, and profiler output details."
                )
                with Collapsible(title="Show advanced controls", collapsed=True):
                    yield from self._labeled_input(
                        option_id="topic-prefix",
                        value=state.topic_prefix,
                        widget_id="topic-prefix",
                        placeholder="pyrallel-benchmark",
                    )
                    yield from self._labeled_select(
                        option_id="log-level",
                        options=[
                            ("DEBUG", "DEBUG"),
                            ("INFO", "INFO"),
                            ("WARNING", "WARNING"),
                            ("ERROR", "ERROR"),
                            ("CRITICAL", "CRITICAL"),
                        ],
                        value=state.log_level,
                        widget_id="log-level",
                    )
                    yield from self._switch_field(
                        option_id="skip-baseline",
                        value=state.skip_baseline,
                        widget_id="skip-baseline",
                    )
                    yield from self._switch_field(
                        option_id="skip-async",
                        value=state.skip_async,
                        widget_id="skip-async",
                    )
                    yield from self._switch_field(
                        option_id="skip-process",
                        value=state.skip_process,
                        widget_id="skip-process",
                    )
                    yield from self._labeled_input(
                        option_id="profile-dir",
                        value=state.profile_dir,
                        widget_id="profile-dir",
                        placeholder="benchmarks/results/profiles",
                    )
                    yield from self._labeled_input(
                        option_id="profile-top-n",
                        value=str(state.profile_top_n),
                        widget_id="profile-top-n",
                        placeholder="0",
                    )
                    yield from self._labeled_select(
                        option_id="py-spy-format",
                        options=[
                            ("flamegraph", "flamegraph"),
                            ("speedscope", "speedscope"),
                            ("raw", "raw"),
                            ("chrometrace", "chrometrace"),
                        ],
                        value=state.py_spy_format,
                        widget_id="py-spy-format",
                    )
                    yield from self._labeled_input(
                        option_id="py-spy-output",
                        value=state.py_spy_output,
                        widget_id="py-spy-output",
                        placeholder="benchmarks/results/pyspy",
                    )
                    yield from self._switch_field(
                        option_id="py-spy-native",
                        value=state.py_spy_native,
                        widget_id="py-spy-native",
                    )
                    yield from self._switch_field(
                        option_id="py-spy-idle",
                        value=state.py_spy_idle,
                        widget_id="py-spy-idle",
                    )
            with Container(id="options-actions"):
                yield Button("Run benchmark", id="run-button", variant="primary")
                yield Button("Quit", id="quit-button")
            yield Static("", id="argv-preview")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_profiling_controls()
        self._refresh_preview()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._refresh_preview()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "profiling-enabled":
            self._sync_profiling_controls()
        self._refresh_preview()

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._refresh_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button":
            self.app.push_screen(RunScreen(self._build_state()))
        elif event.button.id == "quit-button":
            self.app.exit()
        elif event.button.id is not None and event.button.id.startswith("browse-"):
            self._open_directory_picker(event.button.id.removeprefix("browse-"))

    def _refresh_preview(self) -> None:
        preview = self.query_one("#argv-preview", Static)
        state = self._build_state()
        preview.update(" ".join(state.to_argv()))

    def _sync_profiling_controls(self) -> None:
        profiling_enabled = self.query_one("#profiling-enabled", Switch).value
        for widget_id in PROFILING_CONTROL_IDS:
            self.query_one("#%s" % widget_id).disabled = not profiling_enabled

    def _open_directory_picker(self, field_id: str) -> None:
        self.app.push_screen(
            DirectoryPickerScreen(self._picker_start_path(field_id)),
            callback=lambda selected_path: self.apply_selected_path(
                field_id, selected_path
            ),
        )

    def _picker_start_path(self, field_id: str) -> Path:
        current_value = self.query_one("#%s" % field_id, Input).value.strip()
        if not current_value:
            return Path.cwd()
        current_path = Path(current_value).expanduser()
        if field_id == "json-output" or current_path.suffix:
            return current_path.parent if str(current_path.parent) else Path.cwd()
        if current_path.exists() and current_path.is_file():
            return current_path.parent
        return current_path

    def apply_selected_path(
        self, field_id: str, selected_path: str | Path | None
    ) -> None:
        if selected_path is None:
            return
        input_widget = self.query_one("#%s" % field_id, Input)
        normalized_path = Path(selected_path).expanduser()
        if field_id == "json-output":
            current_value = input_widget.value.strip()
            current_path = Path(current_value).expanduser() if current_value else None
            file_name = (
                current_path.name
                if current_path is not None and current_path.suffix
                else "benchmark-results.json"
            )
            input_widget.value = str(normalized_path / file_name)
        else:
            input_widget.value = str(normalized_path)
        self._refresh_preview()

    def _build_state(self) -> BenchmarkTuiState:
        state = BenchmarkTuiState()
        return replace(
            state,
            bootstrap_servers=self.query_one("#bootstrap-servers", Input).value,
            json_output=self.query_one("#json-output", Input).value,
            num_messages=_safe_int(
                self.query_one("#num-messages", Input).value, state.num_messages
            ),
            num_keys=_safe_int(
                self.query_one("#num-keys", Input).value, state.num_keys
            ),
            num_partitions=_safe_int(
                self.query_one("#num-partitions", Input).value, state.num_partitions
            ),
            timeout_sec=_safe_int(
                self.query_one("#timeout-sec", Input).value, state.timeout_sec
            ),
            topic_prefix=self.query_one("#topic-prefix", Input).value,
            workload=str(self.query_one("#workload", Select).value),
            log_level=str(self.query_one("#log-level", Select).value),
            skip_reset=self.query_one("#skip-reset", Switch).value,
            profiling_enabled=self.query_one("#profiling-enabled", Switch).value,
            profile=self.query_one("#profile", Switch).value,
            profile_dir=self.query_one("#profile-dir", Input).value,
            py_spy=self.query_one("#py-spy", Switch).value,
            py_spy_output=self.query_one("#py-spy-output", Input).value,
            skip_baseline=self.query_one("#skip-baseline", Switch).value,
            skip_async=self.query_one("#skip-async", Switch).value,
            skip_process=self.query_one("#skip-process", Switch).value,
            profile_top_n=_safe_int(
                self.query_one("#profile-top-n", Input).value, state.profile_top_n
            ),
            py_spy_format=str(self.query_one("#py-spy-format", Select).value),
            py_spy_native=self.query_one("#py-spy-native", Switch).value,
            py_spy_idle=self.query_one("#py-spy-idle", Switch).value,
            worker_sleep_ms=_safe_float(
                self.query_one("#worker-sleep-ms", Input).value, state.worker_sleep_ms
            ),
            worker_cpu_iterations=_safe_int(
                self.query_one("#worker-cpu-iterations", Input).value,
                state.worker_cpu_iterations,
            ),
            worker_io_sleep_ms=_safe_float(
                self.query_one("#worker-io-sleep-ms", Input).value,
                state.worker_io_sleep_ms,
            ),
        )


class ResultsSummaryModalScreen(ModalScreen[None]):
    BINDINGS = [("escape", "close", "Close")]
    DEFAULT_CSS = """
    ResultsSummaryModalScreen {
        align: center middle;
    }
    """

    def __init__(self, summary_text: str, output_path: str | None = None) -> None:
        super().__init__()
        self._summary_text = summary_text
        self._output_path = output_path
        self._overview = (
            summarize_results_overview(output_path) if output_path is not None else None
        )
        self._winners = (
            summarize_workload_winners(output_path) if output_path is not None else {}
        )
        self._table_data = (
            load_results_table_data(output_path) if output_path is not None else None
        )

    def compose(self) -> ComposeResult:
        with Container(id="results-modal"):
            with Container(id="results-modal-header"):
                yield Static("Benchmark completed", id="results-modal-title")
                yield Static(
                    "Review the final benchmark summary below.",
                    id="results-modal-subtitle",
                )
            with Horizontal(id="results-overview-row"):
                yield Static(
                    self._winner_card_text("sleep"),
                    id="results-winner-sleep",
                    classes="results-card",
                )
                yield Static(
                    self._winner_card_text("cpu"),
                    id="results-winner-cpu",
                    classes="results-card",
                )
                yield Static(
                    self._winner_card_text("io"),
                    id="results-winner-io",
                    classes="results-card",
                )
            yield Static(self._output_path_text(), id="results-output-path")
            yield Static("Detailed report", id="results-detail-title")
            yield DataTable(id="results-table")
            with Container(id="results-modal-actions"):
                yield Button("Close", id="results-modal-close", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        if self._table_data is None:
            table.add_column("Summary")
            table.add_row(self._summary_text)
            return
        for header in self._table_data.headers:
            table.add_column(header)
        for row in self._table_data.rows:
            table.add_row(*row)

    def _winner_card_text(self, workload: str) -> str:
        winner = self._winners.get(workload)
        if winner is None:
            return "%s 1등\n—\n—\n—" % workload
        return "%s 1등\n%s\n%s TPS\n%ss" % (
            workload,
            winner.run_type,
            winner.throughput_tps,
            winner.total_time_sec,
        )

    def _output_path_text(self) -> str:
        if self._overview is None:
            return "Results file unavailable"
        return "Overview: %d runs | workloads: %s\nResults file: %s" % (
            self._overview.total_runs,
            ", ".join(self._overview.workloads) or "unknown",
            self._overview.output_path,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "results-modal-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class RunScreen(Screen[None]):
    BINDINGS = [("b", "back", "Back"), ("c", "cancel", "Cancel")]

    def __init__(self, state: BenchmarkTuiState) -> None:
        super().__init__()
        self._state = state
        self._controller: BenchmarkProcessController | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._cancelled = False
        self._closed = False
        self._active_workloads = self._resolve_workloads()
        self._active_phases = self._resolve_phases()
        self._total_runs = len(self._active_workloads) * len(self._active_phases)
        self._latest_output_path = state.json_output or None
        self._last_snapshot = BenchmarkProgressSnapshot(total_runs=self._total_runs)

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="run-screen"):
            yield Static("Preparing benchmark run", id="run-status")
            with Horizontal(id="run-meta-row"):
                yield Static("", id="phase-badge", classes="status-badge")
                yield Static("", id="workload-badge", classes="status-badge")
                yield Static("", id="progress-badge", classes="status-badge")
            with Horizontal(id="run-phase-row"):
                yield Static("", id="phase-pill-baseline", classes="phase-pill")
                yield Static("", id="phase-pill-async", classes="phase-pill")
                yield Static("", id="phase-pill-process", classes="phase-pill")
            with Horizontal(id="run-workload-row"):
                yield Static("", id="workload-pill-sleep", classes="workload-pill")
                yield Static("", id="workload-pill-cpu", classes="workload-pill")
                yield Static("", id="workload-pill-io", classes="workload-pill")
            yield Static("", id="run-output-path")
            with Horizontal(id="run-progress-row"):
                yield ProgressBar(total=max(self._total_runs, 1), id="run-progress")
                yield LoadingIndicator(id="run-loading")
            yield DataTable(id="run-summary")
            yield Log(id="run-log")
            with Container(id="run-actions"):
                yield Button("Cancel", id="cancel-button", variant="error")
                yield Button("Back", id="back-button")
        yield Footer()

    def on_mount(self) -> None:
        self._configure_run_summary_table()
        self._render_snapshot(BenchmarkProgressSnapshot(total_runs=self._total_runs))
        self._set_loading(True)
        self._controller = BenchmarkProcessController(
            state=self._state,
            on_output=self._append_log,
            on_progress=self._render_snapshot,
            on_complete=self._on_complete,
        )
        self._run_task = asyncio.create_task(self._controller.run())

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel-button":
            await self.action_cancel()
        elif event.button.id == "back-button":
            await self.action_back()

    async def action_cancel(self) -> None:
        self._cancelled = True
        if self._controller is not None:
            await self._controller.cancel()
        if self._run_task is not None:
            await self._run_task
        if not self._closed:
            self._set_loading(False)
            self.query_one("#run-status", Static).update("Benchmark cancelled")

    async def action_back(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            await self.action_cancel()
        self.app.pop_screen()

    def on_unmount(self) -> None:
        self._closed = True

    def _append_log(self, line: str, is_error: bool) -> None:
        if self._closed:
            return
        log = self.query_one("#run-log", Log)
        prefix = "[stderr] " if is_error else ""
        log.write_line("%s%s" % (prefix, line))

    def _render_snapshot(self, snapshot: BenchmarkProgressSnapshot) -> None:
        if self._closed:
            return
        self._last_snapshot = snapshot
        if snapshot.output_path is not None:
            self._latest_output_path = snapshot.output_path
        self.query_one("#run-status", Static).update(
            self._format_status_message(snapshot)
        )
        self._render_meta_badges(snapshot)
        self._render_workload_pills(snapshot)
        self._render_output_path()
        progress_value = (
            snapshot.progress_value
            if snapshot.progress_value > 0
            else float(snapshot.completed_runs)
        )
        self.query_one("#run-progress", ProgressBar).update(
            total=max(snapshot.total_runs or self._total_runs, 1),
            progress=progress_value,
        )
        self._update_run_summary_table(snapshot)

    def _on_complete(self, return_code: int) -> None:
        if self._closed:
            return
        if self._cancelled:
            self._set_loading(False)
            self.query_one("#run-status", Static).update("Benchmark cancelled")
            return
        status = "Benchmark completed" if return_code == 0 else "Benchmark failed"
        self._set_loading(False)
        if return_code == 0:
            self._force_completion_progress()
        self.query_one("#run-status", Static).update(
            "%s (exit=%d)" % (status, return_code)
        )
        if return_code == 0:
            self.app.push_screen(
                ResultsSummaryModalScreen(
                    self._build_results_summary(), self._latest_output_path
                )
            )
        else:
            self._mark_failed_run_cell()

    def _configure_run_summary_table(self) -> None:
        table = self.query_one("#run-summary", DataTable)
        table.cursor_type = "none"
        table.add_column("Workload", key="workload")
        for phase in self._active_phases:
            table.add_column(phase.title(), key=phase)
        for workload in self._active_workloads:
            table.add_row(
                workload,
                *("--" for _ in self._active_phases),
                key=workload,
            )

    def _update_run_summary_table(self, snapshot: BenchmarkProgressSnapshot) -> None:
        table = self.query_one("#run-summary", DataTable)
        for workload in self._active_workloads:
            for phase in self._active_phases:
                value = self._format_tps_cell(
                    snapshot.tps_by_workload.get(workload, {}).get(phase, "--")
                )
                table.update_cell(workload, phase, value, update_width=True)

    def _build_results_summary(self) -> str:
        if self._latest_output_path is None:
            return "No JSON summary was reported by the benchmark run."
        return render_results_summary(self._latest_output_path)

    @staticmethod
    def _format_tps_cell(value: str) -> str:
        if value == "--":
            return "…"
        return "%s TPS" % value

    def _mark_failed_run_cell(self) -> None:
        workload = self._last_snapshot.current_workload
        phase = self._last_running_phase()
        if workload is None or phase is None:
            return
        self.query_one("#run-summary", DataTable).update_cell(
            workload,
            phase,
            Text("FAILED", style="bright_red"),
            update_width=True,
        )

    def _last_running_phase(self) -> str | None:
        for phase in self._active_phases:
            if self._last_snapshot.phase_statuses.get(phase) == "running":
                return phase
        return None

    def _set_loading(self, is_running: bool) -> None:
        self.query_one("#run-loading", LoadingIndicator).display = is_running

    def _force_completion_progress(self) -> None:
        self._last_snapshot.completed_runs = self._total_runs
        self._last_snapshot.progress_value = float(self._total_runs)
        self.query_one("#progress-badge", Static).update(
            "PROGRESS %d / %d" % (self._total_runs, self._total_runs)
        )
        self.query_one("#run-progress", ProgressBar).update(
            total=max(self._total_runs, 1),
            progress=float(self._total_runs),
        )

    def _render_meta_badges(self, snapshot: BenchmarkProgressSnapshot) -> None:
        self.query_one("#phase-badge", Static).update(
            "PHASE %s" % self._display_phase(snapshot)
        )
        self.query_one("#workload-badge", Static).update(
            "WORKLOAD %s" % self._display_workload(snapshot)
        )
        self.query_one("#progress-badge", Static).update(
            "PROGRESS %d / %d"
            % (snapshot.completed_runs, snapshot.total_runs or self._total_runs)
        )
        for phase in _PHASE_NAMES:
            widget = self.query_one("#phase-pill-%s" % phase, Static)
            status = snapshot.phase_statuses.get(phase, "pending")
            if status == "pending" and phase in snapshot.status_message.lower():
                status = "running"
            widget.update("%s %s" % (phase, status))
            widget.remove_class("is-pending", "is-running", "is-completed", "is-failed")
            widget.add_class("is-%s" % status)

    def _render_workload_pills(self, snapshot: BenchmarkProgressSnapshot) -> None:
        for workload in _WORKLOAD_NAMES:
            widget = self.query_one("#workload-pill-%s" % workload, Static)
            status = self._display_workload_status(snapshot, workload)
            widget.update("%s %s" % (workload, status))
            widget.remove_class("is-pending", "is-running", "is-completed", "is-failed")
            widget.add_class("is-%s" % status)

    def _render_output_path(self) -> None:
        output = self.query_one("#run-output-path", Static)
        if self._latest_output_path is None:
            output.update("")
            return
        output.update("Output: %s" % self._latest_output_path)

    def _display_phase(self, snapshot: BenchmarkProgressSnapshot) -> str:
        for phase in self._active_phases:
            if snapshot.phase_statuses.get(phase) == "running":
                return phase.title()
        for phase in reversed(self._active_phases):
            if snapshot.phase_statuses.get(phase) == "completed":
                return "%s Done" % phase.title()
        lowered = snapshot.status_message.lower()
        for phase in self._active_phases:
            if phase in lowered:
                return phase.title()
        return "Pending"

    def _display_workload(self, snapshot: BenchmarkProgressSnapshot) -> str:
        if snapshot.current_workload is None:
            return "—"
        return snapshot.current_workload.title()

    def _display_workload_status(
        self, snapshot: BenchmarkProgressSnapshot, workload: str
    ) -> str:
        status = snapshot.workload_statuses.get(workload, "pending")
        if status != "pending":
            return status
        if snapshot.current_workload == workload:
            lowered = snapshot.status_message.lower()
            if "running" in lowered:
                return "running"
            if "failed" in lowered:
                return "failed"
            if "completed" in lowered:
                return "completed"
        return status

    @staticmethod
    def _format_status_message(snapshot: BenchmarkProgressSnapshot) -> str:
        if snapshot.current_workload is None:
            return snapshot.status_message
        base_message = snapshot.status_message.replace(" benchmark", "")
        return "%s (%s)" % (base_message, snapshot.current_workload)

    def _resolve_workloads(self) -> tuple[str, ...]:
        if self._state.workload == "all":
            return _WORKLOAD_NAMES
        return (self._state.workload,)

    def _resolve_phases(self) -> tuple[str, ...]:
        phases: list[str] = []
        if not self._state.skip_baseline:
            phases.append("baseline")
        if not self._state.skip_async:
            phases.append("async")
        if not self._state.skip_process:
            phases.append("process")
        if phases:
            return tuple(phases)
        return _PHASE_NAMES


class BenchmarkTuiApp(App[None]):
    TITLE = "Pyrallel Benchmark TUI"
    CSS = """
    #options-screen, #run-screen {
        padding: 1 2;
    }

    Input, Select, Switch, Static, Log, Collapsible, ProgressBar, DataTable, LoadingIndicator {
        margin-bottom: 1;
    }

    .screen-title {
        text-style: bold;
        color: $accent;
    }

    .field-label {
        margin-top: 1;
    }

    .option-block {
        height: auto;
    }

    .option-section {
        height: auto;
        border: round $surface-lighten-1;
        padding: 0 1 1 1;
        margin-bottom: 1;
    }

    .option-section-title {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }

    .option-section-description {
        color: $text-muted;
        margin-bottom: 1;
    }

    .option-help {
        color: $text-muted;
        margin-top: -1;
        margin-bottom: 1;
    }

    .input-with-browse {
        layout: horizontal;
        height: auto;
    }

    .input-with-browse Input {
        width: 1fr;
    }

    .browse-button {
        width: 12;
        margin-left: 1;
    }

    #run-log {
        height: 20;
    }

    #run-summary {
        height: auto;
    }

    #run-progress-row {
        height: auto;
        align-vertical: middle;
    }

    #run-meta-row, #run-phase-row, #run-workload-row {
        height: auto;
    }

    .status-badge, .phase-pill, .workload-pill {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: auto;
    }

    .phase-pill.is-pending, .workload-pill.is-pending {
        color: $text-muted;
    }

    .phase-pill.is-running, .workload-pill.is-running {
        color: $accent;
    }

    .phase-pill.is-completed, .workload-pill.is-completed {
        color: $success;
    }

    .phase-pill.is-failed, .workload-pill.is-failed {
        color: $error;
    }

    #run-output-path {
        color: $text-muted;
    }

    #run-progress {
        width: 1fr;
        margin-right: 1;
    }

    #results-modal {
        width: 80%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #results-modal-header {
        height: auto;
        margin-bottom: 1;
    }

    #results-modal-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #results-modal-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    #results-overview-row {
        height: auto;
        margin-bottom: 1;
    }

    .results-card {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: 1fr;
        height: 5;
        content-align: center middle;
        text-align: center;
    }

    #results-output-path {
        color: $text-muted;
        margin-bottom: 1;
    }

    #results-detail-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #results-table {
        border: round $accent;
        height: 1fr;
        margin-bottom: 1;
    }

    #results-modal-actions {
        height: auto;
        align-horizontal: right;
    }

    #options-actions, #run-actions {
        height: auto;
        width: 100%;
    }

    #argv-preview, #progress-status {
        border: round $accent;
        padding: 1;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(OptionsScreen())
