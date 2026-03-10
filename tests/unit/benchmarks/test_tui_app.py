from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from rich.text import Text
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    LoadingIndicator,
    ProgressBar,
    Static,
    Switch,
)

from benchmarks.tui.app import BenchmarkTuiApp, RunScreen
from benchmarks.tui.log_parser import BenchmarkProgressSnapshot
from benchmarks.tui.state import BenchmarkTuiState


class _FakeController:
    instances: list["_FakeController"] = []

    def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
        del state
        del on_output
        del on_progress
        self._on_complete = on_complete
        self.cancel_called = False
        self._done = asyncio.Event()
        self.__class__.instances.append(self)

    async def run(self) -> None:
        await self._done.wait()
        if self.cancel_called:
            self._on_complete(1)
        else:
            self._on_complete(0)

    async def cancel(self) -> None:
        self.cancel_called = True
        self._done.set()


def _block_child_types(app, block_id: str) -> list[str]:
    block = app.screen.query_one(f"#option-block-{block_id}")
    return [child.__class__.__name__ for child in block.children]


def _ancestor_ids(widget) -> list[str]:
    ancestor_ids: list[str] = []
    current = widget.parent
    while current is not None:
        if current.id is not None:
            ancestor_ids.append(current.id)
        current = current.parent
    return ancestor_ids


@pytest.mark.asyncio
async def test_options_screen_orders_input_blocks_label_help_control() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        assert _block_child_types(app, "bootstrap-servers") == [
            "Label",
            "Static",
            "Input",
        ]
        assert _block_child_types(app, "json-output") == [
            "Label",
            "Static",
            "Container",
        ]


@pytest.mark.asyncio
async def test_options_screen_orders_checkbox_blocks_label_help_control() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        assert _block_child_types(app, "profiling-enabled") == [
            "Label",
            "Static",
            "Switch",
        ]


@pytest.mark.asyncio
async def test_option_blocks_expand_to_show_controls() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        input_block = app.screen.query_one("#option-block-bootstrap-servers")
        checkbox_block = app.screen.query_one("#option-block-profiling-enabled")
        assert input_block.region.height > 1
        assert checkbox_block.region.height > 1


@pytest.mark.asyncio
async def test_benchmark_tui_app_mounts_with_run_button() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        run_button = app.screen.query_one("#run-button", Button)
        assert str(run_button.label) == "Run benchmark"


@pytest.mark.asyncio
async def test_options_screen_shows_human_readable_field_labels() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        labels = [str(label.render()) for label in app.screen.query(Label)]

    assert "Bootstrap servers" in labels
    assert "Number of messages" in labels
    assert "Number of keys" in labels
    assert "Number of partitions" in labels
    assert "Timeout (sec)" in labels
    assert "Workload" in labels


@pytest.mark.asyncio
async def test_options_screen_uses_prominent_title_and_helper_text() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        title = app.screen.query_one("#options-title", Static)
        help_texts = [
            str(widget.content) for widget in app.screen.query(".option-help")
        ]
        field_labels = [str(label.render()) for label in app.screen.query(Label)]
        switches = list(app.screen.query(Switch))

    assert title.has_class("screen-title")
    assert "Connect to the Kafka cluster" in help_texts
    assert "benchmark messages" in " ".join(help_texts).lower()
    assert "Profiling enabled" in field_labels
    assert switches


@pytest.mark.asyncio
async def test_options_screen_groups_fields_under_section_headings() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        section_titles = [
            str(widget.render()) for widget in app.screen.query(".option-section-title")
        ]

    assert section_titles == [
        "Cluster & workload",
        "Output & execution",
        "Profiling",
        "Advanced options",
    ]


@pytest.mark.asyncio
async def test_options_screen_places_representative_fields_in_expected_sections() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        bootstrap_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-bootstrap-servers")
        )
        sleep_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-worker-sleep-ms")
        )
        output_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-json-output")
        )
        profiling_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-profiling-enabled")
        )
        topic_prefix_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-topic-prefix")
        )

    assert "option-section-cluster-workload" in bootstrap_ancestors
    assert "option-section-cluster-workload" in sleep_ancestors
    assert "option-section-output-execution" in output_ancestors
    assert "option-section-profiling" in profiling_ancestors
    assert "option-section-advanced-options" in topic_prefix_ancestors


@pytest.mark.asyncio
async def test_options_screen_exposes_output_path_fields_with_browse_buttons() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        json_output = app.screen.query_one("#json-output", Input)
        profile_dir = app.screen.query_one("#profile-dir", Input)
        py_spy_output = app.screen.query_one("#py-spy-output", Input)
        browse_buttons = {
            button.id: str(button.label)
            for button in app.screen.query(".browse-button")
        }

    assert json_output is not None
    assert profile_dir.value == "benchmarks/results/profiles"
    assert py_spy_output.value == "benchmarks/results/pyspy"
    assert browse_buttons == {
        "browse-json-output": "Browse",
        "browse-profile-dir": "Browse",
        "browse-py-spy-output": "Browse",
    }


@pytest.mark.asyncio
async def test_browse_button_opens_directory_picker_modal() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        await pilot.click("#browse-profile-dir")
        await pilot.pause()

        app.screen_stack[-1]
        selected_path = Path.cwd()
        app.pop_screen()
        app.screen.apply_selected_path("profile-dir", selected_path)
        await pilot.pause()

        profile_dir = app.screen.query_one("#profile-dir", Input)

    assert profile_dir.value == str(selected_path)


@pytest.mark.asyncio
async def test_run_screen_back_cancels_active_benchmark(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState()))
        await pilot.pause()

        await app.screen.action_back()
        await pilot.pause()

        assert _FakeController.instances[0].cancel_called is True
        assert app.screen.query_one("#run-button", Button)


@pytest.mark.asyncio
async def test_run_screen_preserves_cancelled_status(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState()))
        await pilot.pause()

        run_screen = app.screen
        await run_screen.action_cancel()
        await pilot.pause()

        status = run_screen.query_one("#run-status", Static)
        assert "cancelled" in str(status.content).lower()


@pytest.mark.asyncio
async def test_run_screen_mounts_dashboard_widgets(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workload="all")))
        await pilot.pause()

        phase_badge = app.screen.query_one("#phase-badge", Static)
        workload_badge = app.screen.query_one("#workload-badge", Static)
        progress_badge = app.screen.query_one("#progress-badge", Static)
        baseline_pill = app.screen.query_one("#phase-pill-baseline", Static)
        sleep_pill = app.screen.query_one("#workload-pill-sleep", Static)
        progress_bar = app.screen.query_one("#run-progress", ProgressBar)
        loading_indicator = app.screen.query_one("#run-loading", LoadingIndicator)
        summary_table = app.screen.query_one("#run-summary", DataTable)
        assert str(phase_badge.content) == "PHASE Pending"
        assert str(workload_badge.content) == "WORKLOAD —"
        assert str(progress_badge.content) == "PROGRESS 0 / 9"
        assert str(baseline_pill.content) == "baseline pending"
        assert str(sleep_pill.content) == "sleep pending"
        assert progress_bar.total == 9
        assert loading_indicator.display is True
        assert summary_table.get_row("sleep") == ["sleep", "…", "…", "…"]
        assert summary_table.get_row("cpu") == ["cpu", "…", "…", "…"]
        assert summary_table.get_row("io") == ["io", "…", "…", "…"]


@pytest.mark.asyncio
async def test_run_screen_updates_progress_bar_and_summary_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workload="all")))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=2,
                total_runs=9,
                tps_by_workload={
                    "sleep": {
                        "baseline": "111.11",
                        "async": "222.22",
                        "process": "--",
                    },
                    "cpu": {
                        "baseline": "--",
                        "async": "--",
                        "process": "--",
                    },
                    "io": {
                        "baseline": "--",
                        "async": "--",
                        "process": "--",
                    },
                },
            )
        )
        await pilot.pause()

        progress_bar = run_screen.query_one("#run-progress", ProgressBar)
        summary_table = run_screen.query_one("#run-summary", DataTable)

    assert progress_bar.progress == 2
    assert summary_table.get_row("sleep") == [
        "sleep",
        "111.11 TPS",
        "222.22 TPS",
        "…",
    ]


@pytest.mark.asyncio
async def test_run_screen_uses_lifecycle_progress_value(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workload="sleep")))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=0,
                total_runs=3,
                progress_value=0.5,
            )
        )
        await pilot.pause()

        progress_bar = run_screen.query_one("#run-progress", ProgressBar)

    assert progress_bar.progress == 0.5


@pytest.mark.asyncio
async def test_run_screen_formats_status_and_tps_cells_for_readability(
    monkeypatch
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workload="sleep")))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                completed_runs=1,
                total_runs=3,
                progress_value=1.5,
                tps_by_workload={
                    "sleep": {
                        "baseline": "111.11",
                        "async": "--",
                        "process": "--",
                    },
                    "cpu": {
                        "baseline": "--",
                        "async": "--",
                        "process": "--",
                    },
                    "io": {
                        "baseline": "--",
                        "async": "--",
                        "process": "--",
                    },
                },
            )
        )
        await pilot.pause()

        status = run_screen.query_one("#run-status", Static)
        phase_badge = run_screen.query_one("#phase-badge", Static)
        workload_badge = run_screen.query_one("#workload-badge", Static)
        progress_badge = run_screen.query_one("#progress-badge", Static)
        async_pill = run_screen.query_one("#phase-pill-async", Static)
        sleep_pill = run_screen.query_one("#workload-pill-sleep", Static)
        summary_table = run_screen.query_one("#run-summary", DataTable)

    assert str(status.content) == "Running async (sleep)"
    assert str(phase_badge.content) == "PHASE Async"
    assert str(workload_badge.content) == "WORKLOAD Sleep"
    assert str(progress_badge.content) == "PROGRESS 1 / 3"
    assert str(async_pill.content) == "async running"
    assert str(sleep_pill.content) == "sleep running"
    assert summary_table.get_row("sleep") == ["sleep", "111.11 TPS", "…", "…"]


@pytest.mark.asyncio
async def test_run_screen_marks_failed_cell_in_soft_red(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workload="sleep")))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                phase_statuses={
                    "baseline": "completed",
                    "async": "running",
                    "process": "pending",
                },
                total_runs=3,
                progress_value=1.5,
            )
        )
        run_screen._on_complete(1)
        await pilot.pause()

        failed_cell = run_screen.query_one("#run-summary", DataTable).get_cell(
            "sleep", "async"
        )
        loading_indicator = run_screen.query_one("#run-loading", LoadingIndicator)

    assert isinstance(failed_cell, Text)
    assert failed_cell.plain == "FAILED"
    assert "red" in str(failed_cell.style)
    assert loading_indicator.display is False
