from __future__ import annotations

import asyncio
from dataclasses import replace

from textual.app import App, ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    Collapsible,
    Footer,
    Header,
    Input,
    Log,
    Select,
    Static,
)

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.log_parser import BenchmarkProgressSnapshot
from benchmarks.tui.state import BenchmarkTuiState


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

    def compose(self) -> ComposeResult:
        state = BenchmarkTuiState()
        yield Header()
        with VerticalScroll(id="options-screen"):
            yield Static("Benchmark options", id="options-title")
            yield Input(value=state.bootstrap_servers, id="bootstrap-servers")
            yield Input(value=str(state.num_messages), id="num-messages")
            yield Input(value=str(state.num_keys), id="num-keys")
            yield Input(value=str(state.num_partitions), id="num-partitions")
            yield Input(value=str(state.timeout_sec), id="timeout-sec")
            yield Select(
                [("sleep", "sleep"), ("cpu", "cpu"), ("io", "io"), ("all", "all")],
                value=state.workload,
                id="workload",
                allow_blank=False,
            )
            yield Input(value=str(state.worker_sleep_ms), id="worker-sleep-ms")
            yield Input(
                value=str(state.worker_cpu_iterations), id="worker-cpu-iterations"
            )
            yield Input(value=str(state.worker_io_sleep_ms), id="worker-io-sleep-ms")
            yield Checkbox(
                "Skip topic/group reset", value=state.skip_reset, id="skip-reset"
            )
            yield Checkbox("Enable yappi profiling", value=state.profile, id="profile")
            yield Checkbox("Enable py-spy", value=state.py_spy, id="py-spy")
            with Collapsible(title="Advanced options", collapsed=True):
                yield Input(value=state.topic_prefix, id="topic-prefix")
                yield Select(
                    [
                        ("DEBUG", "DEBUG"),
                        ("INFO", "INFO"),
                        ("WARNING", "WARNING"),
                        ("ERROR", "ERROR"),
                        ("CRITICAL", "CRITICAL"),
                    ],
                    value=state.log_level,
                    id="log-level",
                    allow_blank=False,
                )
                yield Checkbox(
                    "Skip baseline", value=state.skip_baseline, id="skip-baseline"
                )
                yield Checkbox("Skip async", value=state.skip_async, id="skip-async")
                yield Checkbox(
                    "Skip process", value=state.skip_process, id="skip-process"
                )
                yield Input(value=str(state.profile_top_n), id="profile-top-n")
                yield Select(
                    [
                        ("flamegraph", "flamegraph"),
                        ("speedscope", "speedscope"),
                        ("raw", "raw"),
                        ("chrometrace", "chrometrace"),
                    ],
                    value=state.py_spy_format,
                    id="py-spy-format",
                    allow_blank=False,
                )
                yield Checkbox(
                    "Include native frames",
                    value=state.py_spy_native,
                    id="py-spy-native",
                )
                yield Checkbox(
                    "Include idle frames", value=state.py_spy_idle, id="py-spy-idle"
                )
            with Container(id="options-actions"):
                yield Button("Run benchmark", id="run-button", variant="primary")
                yield Button("Quit", id="quit-button")
            yield Static("", id="argv-preview")
        yield Footer()

    def on_mount(self) -> None:
        self._refresh_preview()

    def on_input_changed(self, _event: Input.Changed) -> None:
        self._refresh_preview()

    def on_checkbox_changed(self, _event: Checkbox.Changed) -> None:
        self._refresh_preview()

    def on_select_changed(self, _event: Select.Changed) -> None:
        self._refresh_preview()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "run-button":
            self.app.push_screen(RunScreen(self._build_state()))
        elif event.button.id == "quit-button":
            self.app.exit()

    def _refresh_preview(self) -> None:
        preview = self.query_one("#argv-preview", Static)
        state = self._build_state()
        preview.update(" ".join(state.to_argv()))

    def _build_state(self) -> BenchmarkTuiState:
        state = BenchmarkTuiState()
        return replace(
            state,
            bootstrap_servers=self.query_one("#bootstrap-servers", Input).value,
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
            skip_reset=self.query_one("#skip-reset", Checkbox).value,
            profile=self.query_one("#profile", Checkbox).value,
            py_spy=self.query_one("#py-spy", Checkbox).value,
            skip_baseline=self.query_one("#skip-baseline", Checkbox).value,
            skip_async=self.query_one("#skip-async", Checkbox).value,
            skip_process=self.query_one("#skip-process", Checkbox).value,
            profile_top_n=_safe_int(
                self.query_one("#profile-top-n", Input).value, state.profile_top_n
            ),
            py_spy_format=str(self.query_one("#py-spy-format", Select).value),
            py_spy_native=self.query_one("#py-spy-native", Checkbox).value,
            py_spy_idle=self.query_one("#py-spy-idle", Checkbox).value,
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


class RunScreen(Screen[None]):
    BINDINGS = [("b", "back", "Back"), ("c", "cancel", "Cancel")]

    def __init__(self, state: BenchmarkTuiState) -> None:
        super().__init__()
        self._state = state
        self._controller: BenchmarkProcessController | None = None
        self._run_task: asyncio.Task[None] | None = None
        self._cancelled = False
        self._closed = False

    def compose(self) -> ComposeResult:
        yield Header()
        with VerticalScroll(id="run-screen"):
            yield Static("Preparing benchmark run", id="run-status")
            yield Static("", id="progress-status")
            yield Log(id="run-log")
            with Container(id="run-actions"):
                yield Button("Cancel", id="cancel-button", variant="error")
                yield Button("Back", id="back-button")
        yield Footer()

    def on_mount(self) -> None:
        self._render_snapshot(BenchmarkProgressSnapshot())
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
        self.query_one("#run-status", Static).update(snapshot.status_message)
        phase_lines = [
            "Phases: baseline=%s async=%s process=%s"
            % (
                snapshot.phase_statuses["baseline"],
                snapshot.phase_statuses["async"],
                snapshot.phase_statuses["process"],
            )
        ]
        if snapshot.current_workload is not None:
            phase_lines.append("Current workload: %s" % snapshot.current_workload)
        phase_lines.append(
            "Workloads: sleep=%s cpu=%s io=%s"
            % (
                snapshot.workload_statuses["sleep"],
                snapshot.workload_statuses["cpu"],
                snapshot.workload_statuses["io"],
            )
        )
        if snapshot.output_path is not None:
            phase_lines.append("Output: %s" % snapshot.output_path)
        self.query_one("#progress-status", Static).update("\n".join(phase_lines))

    def _on_complete(self, return_code: int) -> None:
        if self._closed:
            return
        if self._cancelled:
            self.query_one("#run-status", Static).update("Benchmark cancelled")
            return
        status = "Benchmark completed" if return_code == 0 else "Benchmark failed"
        self.query_one("#run-status", Static).update(
            "%s (exit=%d)" % (status, return_code)
        )


class BenchmarkTuiApp(App[None]):
    TITLE = "Pyrallel Benchmark TUI"
    CSS = """
    #options-screen, #run-screen {
        padding: 1 2;
    }

    Input, Select, Checkbox, Static, Log, Collapsible {
        margin-bottom: 1;
    }

    #run-log {
        height: 20;
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
