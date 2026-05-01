from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Collapsible,
    Footer,
    Header,
    Input,
    Label,
    Select,
    SelectionList,
    Static,
    Switch,
)

from benchmarks.tui.option_help import OPTION_HELP, PROFILING_CONTROL_IDS
from benchmarks.tui.path_picker import DirectoryPickerScreen
from benchmarks.tui.screens.run import RunScreen
from benchmarks.tui.state import BenchmarkTuiState


@dataclass(slots=True)
class _ValidationResult:
    """Represent validation result data used by options."""

    state: BenchmarkTuiState | None
    errors: dict[str, str]


class OptionsScreen(Screen[None]):
    """Represent options screen data used by options."""

    BINDINGS = [("q", "app.quit", "Quit")]
    _POSITIVE_INT_FIELDS = {
        "num-messages": 1,
        "num-keys": 1,
        "num-partitions": 1,
        "timeout-sec": 1,
        "route-batch-size": 1,
    }
    _NON_NEGATIVE_INT_FIELDS = {
        "metrics-port": 0,
        "worker-cpu-iterations": 0,
        "profile-top-n": 0,
    }
    _OPTIONAL_POSITIVE_INT_FIELDS = {
        "process-count": 1,
        "process-batch-size": 1,
    }
    _OPTIONAL_NON_NEGATIVE_INT_FIELDS = {
        "process-max-batch-wait-ms": 0,
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
        """Handle field label within options."""
        return Label(text, classes="field-label")

    @staticmethod
    def _option_help(option_id: str) -> Static:
        """Handle option help within options."""
        return Static(OPTION_HELP[option_id].description, classes="option-help")

    @staticmethod
    def _option_block_id(option_id: str) -> str:
        """Handle option block id within options."""
        return "option-block-%s" % option_id

    @staticmethod
    def _section_id(section_slug: str) -> str:
        """Handle section id within options."""
        return "option-section-%s" % section_slug

    @staticmethod
    def _section_title(text: str) -> Label:
        """Handle section title within options."""
        return Label(text, classes="option-section-title")

    @staticmethod
    def _section_description(text: str) -> Static:
        """Handle section description within options."""
        return Static(text, classes="option-section-description")

    @staticmethod
    def _error_id(widget_id: str) -> str:
        """Handle error id within options."""
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
        """Handle labeled input within options."""
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            if option.browse:
                with Container(classes="input-with-browse"):
                    yield Input(
                        value=value,
                        id=widget_id,
                        placeholder="" if placeholder is None else placeholder,
                    )
                    yield Button(
                        "Browse",
                        id="browse-%s" % widget_id,
                        classes="browse-button",
                    )
            else:
                yield Input(
                    value=value,
                    id=widget_id,
                    placeholder="" if placeholder is None else placeholder,
                )
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
        """Handle labeled select within options."""
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
        """Handle labeled selection list within options."""
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
        """Handle switch field within options."""
        option = OPTION_HELP[option_id]
        with Container(id=cls._option_block_id(option_id), classes="option-block"):
            yield cls._field_label(option.label)
            yield cls._option_help(option_id)
            yield Switch(value=value, id=widget_id)
            yield Static("", id=cls._error_id(widget_id), classes="field-error")

    def compose(self) -> ComposeResult:
        """Handle compose within options."""
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
                    yield from self._labeled_input(
                        option_id="process-count",
                        value=(
                            ""
                            if state.process_count is None
                            else str(state.process_count)
                        ),
                        widget_id="process-count",
                        placeholder="default",
                    )
                    yield from self._labeled_select(
                        option_id="process-transport",
                        options=[
                            ("shared_queue", "shared_queue"),
                            ("worker_pipes", "worker_pipes"),
                        ],
                        value=state.process_transport,
                        widget_id="process-transport",
                    )
                    yield from self._labeled_input(
                        option_id="process-batch-size",
                        value=(
                            ""
                            if state.process_batch_size is None
                            else str(state.process_batch_size)
                        ),
                        widget_id="process-batch-size",
                        placeholder="default",
                    )
                    yield from self._labeled_input(
                        option_id="process-max-batch-wait-ms",
                        value=(
                            ""
                            if state.process_max_batch_wait_ms is None
                            else str(state.process_max_batch_wait_ms)
                        ),
                        widget_id="process-max-batch-wait-ms",
                        placeholder="default",
                    )
                    yield from self._labeled_input(
                        option_id="route-batch-size",
                        value=str(state.route_batch_size),
                        widget_id="route-batch-size",
                        placeholder="1",
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
                yield Button("Copy CLI command", id="copy-command-button")
                yield Static("", id="copy-command-status")
                with Container(id="options-actions"):
                    yield Button("Run benchmark", id="run-button", variant="primary")
                    yield Button("Quit", id="quit-button")
        yield Footer()

    def on_mount(self) -> None:
        """Handle on mount within options."""
        self._sync_profiling_controls()
        self._refresh_form_state()

    def on_input_changed(self, _event: Input.Changed) -> None:
        """Handle on input changed within options."""
        self._refresh_form_state()

    def on_switch_changed(self, event: Switch.Changed) -> None:
        """Handle on switch changed within options."""
        if event.switch.id == "profiling-enabled":
            self._sync_profiling_controls()
        self._refresh_form_state()

    def on_select_changed(self, _event: Select.Changed) -> None:
        """Handle on select changed within options."""
        self._refresh_form_state()

    def on_selection_list_selected_changed(
        self, _event: SelectionList.SelectedChanged
    ) -> None:
        """Handle on selection list selected changed within options."""
        self._refresh_form_state()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle on button pressed within options."""
        if event.button.id == "run-button" and not event.button.disabled:
            self.app.push_screen(RunScreen(self._last_valid_state))
        elif event.button.id == "copy-command-button":
            self._copy_cli_command()
        elif event.button.id == "quit-button":
            self.app.exit()
        elif event.button.id is not None and event.button.id.startswith("browse-"):
            self._open_directory_picker(event.button.id.removeprefix("browse-"))

    def _copy_cli_command(self) -> None:
        """Copy the current benchmark command to the clipboard."""
        command = self._cli_command(self._last_valid_state)
        self.app.copy_to_clipboard(command)
        self.query_one("#copy-command-status", Static).update(
            "CLI command copied to clipboard."
        )

    def _refresh_form_state(self) -> None:
        """Refresh form state for options."""
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

    @staticmethod
    def _cli_command(state: BenchmarkTuiState) -> str:
        """Build the full CLI command for the current TUI state."""
        return "uv run python -m benchmarks.run_parallel_benchmark %s" % " ".join(
            state.to_argv()
        )

    def _render_errors(self, errors: dict[str, str]) -> None:
        """Handle render errors within options."""
        for widget in self.query(Static):
            if "field-error" not in widget.classes:
                continue
            widget.update("")
        for widget_id, message in errors.items():
            self.query_one("#%s" % self._error_id(widget_id), Static).update(message)

    def _validate_form(self) -> _ValidationResult:
        """Validate form for options."""
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
        for widget_id, minimum in self._OPTIONAL_POSITIVE_INT_FIELDS.items():
            self._validate_optional_int(widget_id, minimum, parsed_ints, errors)
        for widget_id, minimum in self._OPTIONAL_NON_NEGATIVE_INT_FIELDS.items():
            self._validate_optional_int(widget_id, minimum, parsed_ints, errors)
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
            process_count=parsed_ints.get("process-count"),
            process_transport=str(self.query_one("#process-transport", Select).value),
            process_batch_size=parsed_ints.get("process-batch-size"),
            process_max_batch_wait_ms=parsed_ints.get("process-max-batch-wait-ms"),
            route_batch_size=parsed_ints["route-batch-size"],
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
        """Validate int for options."""
        raw_value = self.query_one("#%s" % widget_id, Input).value.strip()
        try:
            value = int(raw_value)
        except ValueError:
            errors[widget_id] = "Enter a whole number."
            return
        if value < minimum:
            comparator = ">="
            errors[widget_id] = "Enter a whole number %s %d." % (comparator, minimum)
            return
        parsed_values[widget_id] = value

    def _validate_optional_int(
        self,
        widget_id: str,
        minimum: int,
        parsed_values: dict[str, int],
        errors: dict[str, str],
    ) -> None:
        """Validate optional int for options."""
        raw_value = self.query_one("#%s" % widget_id, Input).value.strip()
        if not raw_value:
            return
        try:
            value = int(raw_value)
        except ValueError:
            errors[widget_id] = "Enter a whole number or leave blank."
            return
        if value < minimum:
            errors[widget_id] = "Enter a whole number >= %d or leave blank." % minimum
            return
        parsed_values[widget_id] = value

    def _validate_float(
        self,
        widget_id: str,
        minimum: float,
        parsed_values: dict[str, float],
        errors: dict[str, str],
    ) -> None:
        """Validate float for options."""
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
        """Handle sync profiling controls within options."""
        profiling_enabled = self.query_one("#profiling-enabled", Switch).value
        for widget_id in PROFILING_CONTROL_IDS:
            self.query_one("#%s" % widget_id).disabled = not profiling_enabled

    def _open_directory_picker(self, field_id: str) -> None:
        """Handle open directory picker within options."""
        self.app.push_screen(
            DirectoryPickerScreen(self._picker_start_path(field_id)),
            callback=lambda selected_path: self.apply_selected_path(
                field_id, selected_path
            ),
        )

    def _picker_start_path(self, field_id: str) -> Path:
        """Handle picker start path within options."""
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
        """Handle apply selected path within options."""
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
