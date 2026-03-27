from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from pathlib import Path
from time import monotonic

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
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
    SelectionList,
    Static,
    Switch,
    Tab,
    Tabs,
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
_ORDERING_NAMES = ("key_hash", "partition", "unordered")
_DONE_STYLE = "bold bright_green"
_RUNNING_STYLE = "bold black on bright_cyan"
_WAITING_STYLE = "grey62"
_FAILED_STYLE = "bold bright_red"
_CANCELLED_STYLE = "bold bright_yellow"
_ACTIVE_ROW_STYLE = "bold bright_cyan"


@dataclass(slots=True)
class _ValidationResult:
    state: BenchmarkTuiState | None
    errors: dict[str, str]


def _format_elapsed(seconds: float) -> str:
    total_seconds = max(0, int(seconds))
    hours, remainder = divmod(total_seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    return "%02d:%02d:%02d" % (hours, minutes, secs)


class OptionsScreen(Screen[None]):
    BINDINGS = [("q", "app.quit", "Quit")]
    _POSITIVE_INT_FIELDS = {
        "num-messages": 1,
        "num-keys": 1,
        "num-partitions": 1,
        "timeout-sec": 1,
    }
    _NON_NEGATIVE_INT_FIELDS = {
        "metrics-port": 0,
        "worker-cpu-iterations": 0,
        "profile-top-n": 0,
    }
    _NON_NEGATIVE_FLOAT_FIELDS = {
        "worker-sleep-ms": 0.0,
        "worker-io-sleep-ms": 0.0,
    }

    def __init__(self, initial_state: BenchmarkTuiState | None = None) -> None:
        super().__init__()
        self._initial_state = initial_state or BenchmarkTuiState()
        self._last_valid_state = self._initial_state

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

    @staticmethod
    def _error_id(widget_id: str) -> str:
        return "error-%s" % widget_id

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
            yield Static("", id=cls._error_id(widget_id), classes="field-error")

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
            yield Static("", id=cls._error_id(widget_id), classes="field-error")

    @classmethod
    def _labeled_selection_list(
        cls,
        *,
        option_id: str,
        selections: list[tuple[str, str, bool]],
        widget_id: str,
    ) -> ComposeResult:
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            yield SelectionList(*selections, id=widget_id)
            yield Static("", id=cls._error_id(widget_id), classes="field-error")

    @classmethod
    def _switch_field(
        cls, *, option_id: str, value: bool, widget_id: str
    ) -> ComposeResult:
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            yield Switch(value=value, id=widget_id)
            yield Static("", id=cls._error_id(widget_id), classes="field-error")

    def compose(self) -> ComposeResult:
        state = self._initial_state
        yield Header()
        with Container(id="options-layout"):
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
                    yield from self._labeled_selection_list(
                        option_id="workloads",
                        selections=[
                            ("sleep", "sleep", "sleep" in state.workloads),
                            ("cpu", "cpu", "cpu" in state.workloads),
                            ("io", "io", "io" in state.workloads),
                        ],
                        widget_id="workloads",
                    )
                    yield from self._labeled_selection_list(
                        option_id="ordering-modes",
                        selections=[
                            (
                                "key_hash",
                                "key_hash",
                                "key_hash" in state.ordering_modes,
                            ),
                            (
                                "partition",
                                "partition",
                                "partition" in state.ordering_modes,
                            ),
                            (
                                "unordered",
                                "unordered",
                                "unordered" in state.ordering_modes,
                            ),
                        ],
                        widget_id="ordering-modes",
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
                    yield from self._labeled_input(
                        option_id="metrics-port",
                        value=str(state.metrics_port),
                        widget_id="metrics-port",
                        placeholder="9091",
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
                        yield Static(
                            "",
                            id=self._error_id("skip-phase-group"),
                            classes="field-error",
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
            with Container(id="options-footer"):
                yield Static("", id="form-error-summary")
                yield Static(" ".join(state.to_argv()), id="argv-preview")
                with Container(id="options-actions"):
                    yield Button("Run benchmark", id="run-button", variant="primary")
                    yield Button("Quit", id="quit-button")
        yield Footer()

    def on_mount(self) -> None:
        self._sync_profiling_controls()
        self._refresh_form_state()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._refresh_form_state()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        if event.switch.id == "profiling-enabled":
            self._sync_profiling_controls()
        self._refresh_form_state()

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._refresh_form_state()

    def on_selection_list_selected_changed(
        self, _event: SelectionList.SelectedChanged
    ) -> None:
        self._refresh_form_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button" and not event.button.disabled:
            self.app.push_screen(RunScreen(self._last_valid_state))
        elif event.button.id == "quit-button":
            self.app.exit()
        elif event.button.id is not None and event.button.id.startswith("browse-"):
            self._open_directory_picker(event.button.id.removeprefix("browse-"))

    def _refresh_form_state(self) -> None:
        validation = self._validate_form()
        self._render_errors(validation.errors)
        summary = self.query_one("#form-error-summary", Static)
        run_button = self.query_one("#run-button", Button)
        run_button.disabled = validation.state is None
        summary.display = bool(validation.errors)

        if validation.errors:
            summary.update("Please fix the highlighted inputs before running.")
        else:
            summary.update("")

        if validation.state is not None:
            self._last_valid_state = validation.state
            self.query_one("#argv-preview", Static).update(
                " ".join(validation.state.to_argv())
            )

    def _render_errors(self, errors: dict[str, str]) -> None:
        for widget in self.query(".field-error"):
            widget.update("")
        for widget_id, message in errors.items():
            self.query_one("#%s" % self._error_id(widget_id), Static).update(message)

    def _validate_form(self) -> _ValidationResult:
        errors: dict[str, str] = {}
        parsed_ints: dict[str, int] = {}
        parsed_floats: dict[str, float] = {}

        profiling_enabled = self.query_one("#profiling-enabled", Switch).value

        for widget_id, minimum in self._POSITIVE_INT_FIELDS.items():
            self._validate_int(widget_id, minimum, parsed_ints, errors)
        for widget_id, minimum in self._NON_NEGATIVE_INT_FIELDS.items():
            if widget_id == "profile-top-n" and not profiling_enabled:
                continue
            self._validate_int(widget_id, minimum, parsed_ints, errors)
        for widget_id, minimum_float in self._NON_NEGATIVE_FLOAT_FIELDS.items():
            self._validate_float(widget_id, minimum_float, parsed_floats, errors)

        workloads = tuple(self.query_one("#workloads", SelectionList).selected)
        if not workloads:
            errors["workloads"] = "Select at least one workload."

        ordering_modes = tuple(
            self.query_one("#ordering-modes", SelectionList).selected
        )
        if not ordering_modes:
            errors["ordering-modes"] = "Select at least one ordering mode."

        skip_baseline = self.query_one("#skip-baseline", Switch).value
        skip_async = self.query_one("#skip-async", Switch).value
        skip_process = self.query_one("#skip-process", Switch).value
        if skip_baseline and skip_async and skip_process:
            errors["skip-phase-group"] = "Keep at least one execution mode enabled."

        if errors:
            return _ValidationResult(state=None, errors=errors)

        base_state = self._last_valid_state
        state = replace(
            base_state,
            bootstrap_servers=self.query_one("#bootstrap-servers", Input).value,
            json_output=self.query_one("#json-output", Input).value,
            num_messages=parsed_ints["num-messages"],
            num_keys=parsed_ints["num-keys"],
            num_partitions=parsed_ints["num-partitions"],
            timeout_sec=parsed_ints["timeout-sec"],
            metrics_port=parsed_ints["metrics-port"],
            topic_prefix=self.query_one("#topic-prefix", Input).value,
            workloads=workloads,
            ordering_modes=ordering_modes,
            log_level=str(self.query_one("#log-level", Select).value),
            skip_reset=self.query_one("#skip-reset", Switch).value,
            profiling_enabled=profiling_enabled,
            profile=self.query_one("#profile", Switch).value,
            profile_dir=self.query_one("#profile-dir", Input).value,
            py_spy=self.query_one("#py-spy", Switch).value,
            py_spy_output=self.query_one("#py-spy-output", Input).value,
            skip_baseline=skip_baseline,
            skip_async=skip_async,
            skip_process=skip_process,
            profile_top_n=parsed_ints.get("profile-top-n", base_state.profile_top_n),
            py_spy_format=str(self.query_one("#py-spy-format", Select).value),
            py_spy_native=self.query_one("#py-spy-native", Switch).value,
            py_spy_idle=self.query_one("#py-spy-idle", Switch).value,
            worker_sleep_ms=parsed_floats["worker-sleep-ms"],
            worker_cpu_iterations=parsed_ints["worker-cpu-iterations"],
            worker_io_sleep_ms=parsed_floats["worker-io-sleep-ms"],
        )
        return _ValidationResult(state=state, errors={})

    def _validate_int(
        self,
        widget_id: str,
        minimum: int,
        parsed_values: dict[str, int],
        errors: dict[str, str],
    ) -> None:
        raw_value = self.query_one("#%s" % widget_id, Input).value.strip()
        try:
            value = int(raw_value)
        except ValueError:
            errors[widget_id] = "Enter a whole number."
            return
        if value < minimum:
            comparator = ">=" if minimum == 0 else ">="
            errors[widget_id] = "Enter a whole number %s %d." % (comparator, minimum)
            return
        parsed_values[widget_id] = value

    def _validate_float(
        self,
        widget_id: str,
        minimum: float,
        parsed_values: dict[str, float],
        errors: dict[str, str],
    ) -> None:
        raw_value = self.query_one("#%s" % widget_id, Input).value.strip()
        try:
            value = float(raw_value)
        except ValueError:
            errors[widget_id] = "Enter a number."
            return
        if value < minimum:
            errors[widget_id] = "Enter a number >= %.1f." % minimum
            return
        parsed_values[widget_id] = value

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
        self._refresh_form_state()


class ResultsSummaryModalScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "close", "Close"), ("s", "settings", "Settings")]
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
        self._visible_orderings = tuple(
            ordering
            for ordering in ("key_hash", "partition", "unordered")
            if ordering in self._winners
        )
        self._selected_ordering = (
            self._visible_orderings[0] if self._visible_orderings else None
        )
        self._table_data = (
            load_results_table_data(output_path) if output_path is not None else None
        )

    def compose(self) -> ComposeResult:
        with Container(id="results-modal"):
            with VerticalScroll(id="results-modal-scroll"):
                with Container(id="results-modal-header"):
                    yield Static("벤치마크 결과", id="results-modal-title")
                    yield Static(
                        "정렬 방식별 요약을 확인하고 다음 실험으로 돌아가세요.",
                        id="results-modal-subtitle",
                    )
                yield Static(self._overview_text(), id="results-overview-text")
                yield Static(self._output_path_text(), id="results-output-path")
                if self._selected_ordering is not None:
                    yield Tabs(
                        *[
                            Tab(ordering, id="ordering-tab-%s" % ordering)
                            for ordering in self._visible_orderings
                        ],
                        active="ordering-tab-%s" % self._selected_ordering,
                        id="results-ordering-tabs",
                    )
                yield Static(
                    "",
                    id="results-ordering-summary",
                    classes="results-order-section",
                )
                yield Static("상세 결과", id="results-detail-title")
                yield DataTable(id="results-table")
            with Container(id="results-modal-actions"):
                yield Button("Back to settings", id="results-modal-settings")
                yield Button("Close", id="results-modal-close", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        if self._table_data is None:
            table.add_column("요약")
            table.add_row(self._summary_text)
        else:
            for header in self._table_data.headers:
                table.add_column(header)
            for row in self._table_data.rows:
                table.add_row(*row)
        self._refresh_ordering_summary()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tabs.id == "results-ordering-tabs":
            self._selected_ordering = event.tab.id.removeprefix("ordering-tab-")
            self._refresh_ordering_summary()

    def _refresh_ordering_summary(self) -> None:
        summary = self.query_one("#results-ordering-summary", Static)
        if self._selected_ordering is None:
            summary.update(self._summary_text)
            return
        summary.update(self._winner_section_text(self._selected_ordering))

    def _winner_section_text(self, ordering: str) -> str:
        winners = self._winners.get(ordering, {})
        lines = ["정렬: %s" % ordering]
        for workload in _WORKLOAD_NAMES:
            winner = winners.get(workload)
            if winner is None:
                continue
            lines.append(
                "%s · %s · %s TPS · 평균 %sms · P99 %sms"
                % (
                    workload,
                    winner.run_type,
                    winner.throughput_tps,
                    winner.avg_processing_ms,
                    winner.p99_processing_ms,
                )
            )
        return "\n".join(lines)

    def _output_path_text(self) -> str:
        if self._overview is None:
            return "결과 파일을 찾을 수 없습니다."
        return "결과 파일: %s" % self._overview.output_path

    def _overview_text(self) -> str:
        if self._overview is None:
            return "요약을 만들 수 없습니다."
        return "실행 %d건 | workload: %s | 최고 TPS: %s (%s TPS)" % (
            self._overview.total_runs,
            ", ".join(self._overview.workloads) or "unknown",
            self._overview.best_run_name,
            self._overview.best_tps,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "results-modal-settings":
            self.dismiss("settings")
        elif event.button.id == "results-modal-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_settings(self) -> None:
        self.dismiss("settings")


class RunScreen(Screen[None]):
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
                yield Button("Cancel run", id="cancel-button", variant="error")
                yield Button("Back to settings", id="settings-button")
                yield Button("Back", id="exit-button")
        yield Footer()

    def on_mount(self) -> None:
        self._configure_run_summary_table()
        self._set_terminal_actions(show_report=False)
        self._render_snapshot(BenchmarkProgressSnapshot(total_runs=self._total_runs))
        self._set_loading(True)
        self._elapsed_timer = self.set_interval(0.5, self._refresh_elapsed)
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
        elif event.button.id == "settings-button":
            await self.action_settings()
        elif event.button.id == "exit-button":
            await self.action_exit()

    async def action_cancel(self) -> None:
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
        if self._run_task is not None and not self._run_task.done():
            return
        self.app.pop_screen()

    async def action_exit(self) -> None:
        if self._run_task is not None and not self._run_task.done():
            return
        self.app.exit()

    async def action_back(self) -> None:
        await self.action_exit()

    def on_unmount(self) -> None:
        self._closed = True
        if self._elapsed_timer is not None:
            self._elapsed_timer.stop()

    def _append_log(self, line: str, is_error: bool) -> None:
        if self._closed:
            return
        if is_error and line.strip():
            self._last_error_line = line.strip()
        log = self.query_one("#run-log", Log)
        prefix = "[stderr] " if is_error else ""
        log.write_line("%s%s" % (prefix, line))

    def _refresh_elapsed(self) -> None:
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
        end_time = self._finished_at if self._finished_at is not None else monotonic()
        return end_time - self._started_at

    def _current_run_elapsed_seconds(self) -> float:
        end_time = self._finished_at if self._finished_at is not None else monotonic()
        return max(0.0, end_time - self._current_run_started_at)

    def _render_snapshot(self, snapshot: BenchmarkProgressSnapshot) -> None:
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
        self.app.push_screen(
            ResultsSummaryModalScreen(
                self._build_results_summary(), self._latest_output_path
            ),
            callback=self._handle_results_modal_result,
        )

    def _handle_results_modal_result(self, result: str | None) -> None:
        if result == "settings":
            self.app.pop_screen()

    def _set_terminal_actions(self, *, show_report: bool) -> None:
        cancel_button = self.query_one("#cancel-button", Button)
        settings_button = self.query_one("#settings-button", Button)
        exit_button = self.query_one("#exit-button", Button)
        is_terminal = self._finished_at is not None or self._cancelled

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

        settings_button.display = is_terminal
        exit_button.display = is_terminal
        exit_button.label = "Exit"

    def _configure_run_summary_table(self) -> None:
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
        table = self.query_one("#run-summary", DataTable)
        active_row_key = self._active_row_key(snapshot)
        active_phase = self._current_phase(snapshot)

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
                        value = self._status_text("%s TPS" % row[phase], _DONE_STYLE)
                    else:
                        value = self._status_text("WAITING", _WAITING_STYLE)
                    table.update_cell(row_key, phase, value, update_width=True)

    def _build_results_summary(self) -> str:
        if self._latest_output_path is None:
            return "No JSON summary was reported by the benchmark run."
        return render_results_summary(self._latest_output_path)

    def _mark_terminal_cell(self, status: str) -> None:
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
        for phase in self._active_phases:
            if self._last_snapshot.phase_statuses.get(phase) == "running":
                return phase
        return None

    def _set_loading(self, is_running: bool) -> None:
        self.query_one("#run-loading", LoadingIndicator).display = is_running

    def _force_completion_progress(self) -> None:
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
        output = self.query_one("#run-output-path", Static)
        if self._latest_output_path is None:
            output.update("")
            return
        output.update("Output: %s" % self._latest_output_path)

    def _status_line(self, snapshot: BenchmarkProgressSnapshot) -> str:
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
        if snapshot.current_workload is None or snapshot.current_ordering is None:
            return None
        return self._row_key(snapshot.current_workload, snapshot.current_ordering)

    def _sync_current_run_timing(self, snapshot: BenchmarkProgressSnapshot) -> None:
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
        return Text(content, style=style)

    @staticmethod
    def _identity_cell_text(content: str, is_active: bool) -> str | Text:
        if not is_active:
            return content
        return Text("▶ %s" % content, style=_ACTIVE_ROW_STYLE)

    @staticmethod
    def _terminal_style(status: str) -> str:
        if status == "FAILED":
            return _FAILED_STYLE
        if status == "CANCELLED":
            return _CANCELLED_STYLE
        return _WAITING_STYLE

    def _resolve_workloads(self) -> tuple[str, ...]:
        return tuple(
            workload
            for workload in self._state.workloads
            if workload in _WORKLOAD_NAMES
        )

    def _resolve_orderings(self) -> tuple[str, ...]:
        return tuple(
            ordering
            for ordering in self._state.ordering_modes
            if ordering in _ORDERING_NAMES
        )

    def _row_key(self, workload: str, ordering: str) -> str:
        return "%s-%s" % (workload, ordering)

    def _resolve_phases(self) -> tuple[str, ...]:
        phases: list[str] = []
        if not self._state.skip_baseline:
            phases.append("baseline")
        if not self._state.skip_async:
            phases.append("async")
        if not self._state.skip_process:
            phases.append("process")
        return tuple(phases)


class BenchmarkTuiApp(App[None]):
    TITLE = "Pyrallel Benchmark TUI"
    CSS = """
    #options-layout, #run-layout {
        layout: vertical;
        height: 1fr;
    }

    #options-screen, #run-screen {
        padding: 1 2;
        height: 1fr;
    }

    #options-footer {
        height: auto;
        border-top: solid $surface-lighten-1;
        padding: 1 2;
        background: $surface;
        dock: bottom;
    }

    #argv-preview, #form-error-summary {
        border: round $accent;
        padding: 1;
        margin-bottom: 1;
    }

    #form-error-summary {
        color: $warning;
        display: block;
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

    .field-error {
        color: $error;
        margin-top: -1;
        min-height: 1;
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

    #run-spotlight-card {
        border: round $accent;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #run-spotlight {
        text-style: bold;
        margin-bottom: 0;
    }

    #run-chip-row {
        height: auto;
        margin-bottom: 0;
    }

    .status-chip {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: auto;
        text-style: bold;
        margin-bottom: 0;
        background: transparent;
    }

    .status-chip.is-waiting {
        color: $text-muted;
        border: round $surface-lighten-2;
    }

    .status-chip.is-running {
        color: $success;
        border: round $success;
    }

    .status-chip.is-done {
        color: $success;
        border: round $success;
    }

    .status-chip.is-failed {
        color: $error;
        border: round $error;
    }

    #run-phase-meta, #run-overall-meta {
        height: auto;
        margin-bottom: 0;
    }

    .status-badge {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: auto;
        margin-bottom: 0;
    }

    #run-terminal-reason, #run-output-path, #results-output-path {
        color: $text-muted;
    }

    #phase-progress, #run-progress {
        width: 100%;
        height: 1;
        margin-bottom: 1;
    }

    #run-terminal-reason {
        display: none;
    }

    #run-terminal-reason.is-failed {
        color: $error;
        text-style: bold;
    }

    #run-terminal-reason.is-cancelled {
        color: $warning;
        text-style: bold;
    }

    #run-terminal-reason.is-complete {
        color: $success;
    }

    #run-log-header {
        height: auto;
        margin-bottom: 0;
        align-vertical: middle;
    }

    #run-log-title {
        width: 1fr;
        text-style: bold;
    }

    #run-summary {
        height: auto;
    }

    #run-log {
        height: 16;
    }

    #run-actions, #options-actions {
        height: auto;
        width: 100%;
        layout: horizontal;
    }

    #run-actions {
        border-top: solid $surface-lighten-1;
        padding: 1 2;
        background: $surface;
        dock: bottom;
    }

    #run-actions Button, #options-actions Button, #results-modal-actions Button {
        margin-right: 1;
    }

    #results-modal {
        width: 80%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #results-modal-scroll {
        height: 1fr;
    }

    #results-modal-header {
        height: auto;
        margin-bottom: 1;
    }

    #results-modal-title, #results-detail-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #results-modal-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    .results-order-section {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #results-table {
        border: round $accent;
        height: 12;
        margin-bottom: 1;
    }

    #results-modal-actions {
        height: auto;
        align-horizontal: right;
        layout: horizontal;
    }

    #results-modal-actions Button {
        width: 24;
    }

    #results-ordering-tabs {
        margin-bottom: 0;
    }

    #results-ordering-tabs Tab {
        padding: 0 2;
    }

    #results-ordering-summary {
        margin-top: 0;
    }
    """

    def on_mount(self) -> None:
        self.push_screen(OptionsScreen())
