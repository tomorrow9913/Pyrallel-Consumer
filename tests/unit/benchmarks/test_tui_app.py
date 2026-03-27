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
    SelectionList,
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
            "Static",
        ]
        assert _block_child_types(app, "workloads") == [
            "Label",
            "Static",
            "SelectionList",
            "Static",
        ]
        assert _block_child_types(app, "json-output") == [
            "Label",
            "Static",
            "Container",
            "Static",
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
            "Static",
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
    assert "Workloads" in labels
    assert "Ordering modes" in labels


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
        sleep_ancestors = _ancestor_ids(app.screen.query_one("#option-block-workloads"))
        ordering_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-ordering-modes")
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
    assert "option-section-cluster-workload" in ordering_ancestors
    assert "option-section-output-execution" in output_ancestors
    assert "option-section-profiling" in profiling_ancestors
    assert "option-section-advanced-options" in topic_prefix_ancestors


@pytest.mark.asyncio
async def test_options_screen_uses_selection_lists_for_workloads_and_ordering() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        workloads = app.screen.query_one("#workloads", SelectionList)
        ordering = app.screen.query_one("#ordering-modes", SelectionList)

    assert workloads.selected == ["sleep"]
    assert ordering.selected == ["key_hash"]


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
async def test_options_screen_disables_run_for_invalid_numeric_input() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        run_button = app.screen.query_one("#run-button", Button)
        preview = app.screen.query_one("#argv-preview", Static)
        original_preview = str(preview.content)

        num_messages.value = "oops"
        await pilot.pause()

        error = app.screen.query_one("#error-num-messages", Static)

    assert run_button.disabled is True
    assert "number" in str(error.content).lower()
    assert str(preview.content) == original_preview


@pytest.mark.asyncio
async def test_options_screen_hides_error_summary_until_needed() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        summary = app.screen.query_one("#form-error-summary", Static)
        num_messages = app.screen.query_one("#num-messages", Input)

        assert summary.display is False

        num_messages.value = "oops"
        await pilot.pause()
        assert summary.display is True

        num_messages.value = "100"
        await pilot.pause()

    assert summary.display is False


@pytest.mark.asyncio
async def test_success_modal_returns_to_options_with_existing_values(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text('{"options": {}, "results": []}', encoding="utf-8")

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            del on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_complete(0)

        async def cancel(self) -> None:
            return None

    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _CompletedController
    )

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        json_output = app.screen.query_one("#json-output", Input)
        num_messages.value = "42"
        json_output.value = str(results_path)
        await pilot.pause()

        await pilot.click("#run-button")
        await pilot.pause()
        await pilot.pause()

        await pilot.click("#results-modal-settings")
        await pilot.pause()

        restored_messages = app.screen.query_one("#num-messages", Input)
        restored_output = app.screen.query_one("#json-output", Input)

    assert restored_messages.value == "42"
    assert restored_output.value == str(results_path)


@pytest.mark.asyncio
async def test_failed_run_returns_to_options_with_existing_values(
    monkeypatch,
) -> None:
    class _FailedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            del on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_complete(1)

        async def cancel(self) -> None:
            return None

    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FailedController
    )

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        num_messages.value = "4242"
        await pilot.pause()

        await pilot.click("#run-button")
        await pilot.pause()
        await pilot.pause()

        await app.screen.action_settings()

        restored_messages = app.screen.query_one("#num-messages", Input)

    assert restored_messages.value == "4242"


@pytest.mark.asyncio
async def test_run_screen_back_stays_on_active_benchmark(monkeypatch) -> None:
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

        assert _FakeController.instances[0].cancel_called is False
        assert app.screen.__class__.__name__ == "RunScreen"


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
        assert "취소" in str(status.content)


@pytest.mark.asyncio
async def test_run_screen_mounts_dashboard_widgets(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            RunScreen(
                BenchmarkTuiState(
                    workloads=("sleep", "cpu", "io"),
                    ordering_modes=("key_hash", "partition"),
                )
            )
        )
        await pilot.pause()

        spotlight = app.screen.query_one("#run-spotlight", Static)
        workload_chip = app.screen.query_one("#run-chip-workload", Static)
        ordering_chip = app.screen.query_one("#run-chip-ordering", Static)
        phase_chip = app.screen.query_one("#run-chip-phase", Static)
        current_progress_badge = app.screen.query_one("#phase-progress-badge", Static)
        current_elapsed = app.screen.query_one("#current-run-elapsed", Static)
        progress_badge = app.screen.query_one("#progress-badge", Static)
        elapsed = app.screen.query_one("#run-elapsed", Static)
        phase_progress_bar = app.screen.query_one("#phase-progress", ProgressBar)
        progress_bar = app.screen.query_one("#run-progress", ProgressBar)
        loading_indicator = app.screen.query_one("#run-loading", LoadingIndicator)
        exit_button = app.screen.query_one("#exit-button", Button)
        summary_table = app.screen.query_one("#run-summary", DataTable)
        assert str(spotlight.content) == "현재 실행"
        assert str(workload_chip.content) == "workload: 대기 중"
        assert str(ordering_chip.content) == "ordering: 대기 중"
        assert str(phase_chip.content) == "engine: 대기 중"
        assert workload_chip.has_class("is-waiting")
        assert ordering_chip.has_class("is-waiting")
        assert phase_chip.has_class("is-waiting")
        assert str(current_progress_badge.content) == "현재 0 / -- 메시지"
        assert str(current_elapsed.content) == "현재 처리시간 00:00:00"
        assert str(progress_badge.content) == "전체 0 / 18 벤치마크"
        assert str(elapsed.content) == "전체 처리시간 00:00:00"
        assert phase_progress_bar.total is None
        assert phase_progress_bar.progress == 0
        assert phase_progress_bar.show_eta is True
        assert progress_bar.total == 18
        assert progress_bar.show_eta is True
        assert loading_indicator.display is True
        assert "run-spotlight-card" in _ancestor_ids(progress_bar)
        assert "run-log-header" in _ancestor_ids(loading_indicator)
        assert exit_button.display is False
        assert summary_table.get_row("sleep-key_hash") == [
            "sleep",
            "key_hash",
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
        ]
        assert summary_table.get_row("cpu-partition") == [
            "cpu",
            "partition",
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
        ]
        assert summary_table.get_row("io-partition") == [
            "io",
            "partition",
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
            Text("WAITING", style="grey62"),
        ]


@pytest.mark.asyncio
async def test_run_screen_updates_progress_bar_and_summary_table(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            RunScreen(
                BenchmarkTuiState(
                    workloads=("sleep", "cpu", "io"),
                    ordering_modes=("key_hash", "partition"),
                )
            )
        )
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=2,
                total_runs=18,
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "111.11",
                            "async": "222.22",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    },
                    "cpu": {
                        "key_hash": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    },
                    "io": {
                        "key_hash": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    },
                },
            )
        )
        await pilot.pause()

        progress_bar = run_screen.query_one("#run-progress", ProgressBar)
        summary_table = run_screen.query_one("#run-summary", DataTable)

    assert progress_bar.progress == 2
    row = summary_table.get_row("sleep-key_hash")
    assert row[:2] == ["sleep", "key_hash"]
    assert row[2] == Text("111.11 TPS", style="bold bright_green")
    assert row[3] == Text("222.22 TPS", style="bold bright_green")
    assert row[4] == Text("WAITING", style="grey62")


@pytest.mark.asyncio
async def test_run_screen_formats_ordering_status_for_readability(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            RunScreen(
                BenchmarkTuiState(
                    workloads=("sleep",),
                    ordering_modes=("key_hash", "partition"),
                )
            )
        )
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                current_ordering="partition",
                completed_runs=1,
                total_runs=6,
                progress_value=1.5,
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "111.11",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "222.22",
                            "process": "--",
                        },
                    },
                    "cpu": {
                        "key_hash": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    },
                    "io": {
                        "key_hash": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    },
                },
            )
        )
        await pilot.pause()

        spotlight = run_screen.query_one("#run-spotlight", Static)
        workload_chip = run_screen.query_one("#run-chip-workload", Static)
        ordering_chip = run_screen.query_one("#run-chip-ordering", Static)
        phase_chip = run_screen.query_one("#run-chip-phase", Static)
        summary_table = run_screen.query_one("#run-summary", DataTable)
        active_cell = summary_table.get_cell("sleep-partition", "async")

    assert str(spotlight.content) == "현재 실행"
    assert str(workload_chip.content) == "workload: sleep"
    assert str(ordering_chip.content) == "ordering: partition"
    assert str(phase_chip.content) == "engine: async"
    assert workload_chip.has_class("is-running")
    assert ordering_chip.has_class("is-running")
    assert phase_chip.has_class("is-running")
    assert isinstance(active_cell, Text)
    assert active_cell.plain == "RUNNING"


@pytest.mark.asyncio
async def test_run_screen_spotlight_uses_single_progress_semantics_and_selected_rows_only(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            RunScreen(
                BenchmarkTuiState(
                    workloads=("sleep",),
                    ordering_modes=("key_hash", "partition"),
                    skip_process=True,
                )
            )
        )
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                current_ordering="partition",
                current_run_target_messages=50000,
                current_run_processed_messages=1000,
                completed_runs=1,
                total_runs=4,
                progress_value=1.5,
                phase_statuses={
                    "baseline": "completed",
                    "async": "running",
                    "process": "pending",
                },
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "111.11",
                            "async": "--",
                            "process": "--",
                        },
                        "partition": {
                            "baseline": "--",
                            "async": "--",
                            "process": "--",
                        },
                    }
                },
            )
        )
        await pilot.pause()

        status = run_screen.query_one("#run-status", Static)
        spotlight = run_screen.query_one("#run-spotlight", Static)
        workload_chip = run_screen.query_one("#run-chip-workload", Static)
        ordering_chip = run_screen.query_one("#run-chip-ordering", Static)
        phase_chip = run_screen.query_one("#run-chip-phase", Static)
        current_progress_badge = run_screen.query_one("#phase-progress-badge", Static)
        current_elapsed = run_screen.query_one("#current-run-elapsed", Static)
        phase_progress_bar = run_screen.query_one("#phase-progress", ProgressBar)
        progress_badge = run_screen.query_one("#progress-badge", Static)
        progress_bar = run_screen.query_one("#run-progress", ProgressBar)
        elapsed = run_screen.query_one("#run-elapsed", Static)
        summary_table = run_screen.query_one("#run-summary", DataTable)
        active_cell = summary_table.get_cell("sleep-partition", "async")

    assert str(status.content) == "벤치마크 실행 중"
    assert str(spotlight.content) == "현재 실행"
    assert str(workload_chip.content) == "workload: sleep"
    assert str(ordering_chip.content) == "ordering: partition"
    assert str(phase_chip.content) == "engine: async"
    assert str(current_progress_badge.content) == "현재 1000 / 50000 메시지"
    assert str(current_elapsed.content).startswith("현재 처리시간 ")
    assert str(progress_badge.content) == "전체 1 / 4 벤치마크"
    assert str(elapsed.content).startswith("전체 처리시간 ")
    assert phase_progress_bar.total == 50000
    assert phase_progress_bar.progress == 1000
    assert progress_bar.progress == 1
    row = summary_table.get_row("sleep-key_hash")
    assert row[:2] == ["sleep", "key_hash"]
    assert row[2] == Text("111.11 TPS", style="bold bright_green")
    assert row[3] == Text("WAITING", style="grey62")
    assert isinstance(active_cell, Text)
    assert "RUNNING" in active_cell.plain

    assert summary_table.row_count == 2


@pytest.mark.asyncio
async def test_run_screen_uses_lifecycle_progress_value(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
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

    assert progress_bar.progress == 0


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
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                current_ordering="key_hash",
                current_run_target_messages=100,
                current_run_processed_messages=10,
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
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "111.11",
                            "async": "--",
                            "process": "--",
                        }
                    }
                },
            )
        )
        await pilot.pause()

        status = run_screen.query_one("#run-status", Static)
        spotlight = run_screen.query_one("#run-spotlight", Static)
        workload_chip = run_screen.query_one("#run-chip-workload", Static)
        ordering_chip = run_screen.query_one("#run-chip-ordering", Static)
        phase_chip = run_screen.query_one("#run-chip-phase", Static)
        current_progress_badge = run_screen.query_one("#phase-progress-badge", Static)
        current_elapsed = run_screen.query_one("#current-run-elapsed", Static)
        phase_progress_bar = run_screen.query_one("#phase-progress", ProgressBar)
        progress_badge = run_screen.query_one("#progress-badge", Static)
        elapsed = run_screen.query_one("#run-elapsed", Static)
        summary_table = run_screen.query_one("#run-summary", DataTable)
        active_cell = summary_table.get_cell("sleep-key_hash", "async")

    assert str(status.content) == "벤치마크 실행 중"
    assert str(spotlight.content) == "현재 실행"
    assert str(workload_chip.content) == "workload: sleep"
    assert str(ordering_chip.content) == "ordering: key_hash"
    assert str(phase_chip.content) == "engine: async"
    assert str(current_progress_badge.content) == "현재 10 / 100 메시지"
    assert str(current_elapsed.content).startswith("현재 처리시간 ")
    assert str(progress_badge.content) == "전체 1 / 3 벤치마크"
    assert str(elapsed.content).startswith("전체 처리시간 ")
    assert phase_progress_bar.total == 100
    assert phase_progress_bar.progress == 10
    assert isinstance(active_cell, Text)
    assert active_cell.plain == "RUNNING"
    active_row = summary_table.get_row("sleep-key_hash")
    assert isinstance(active_row[0], Text)
    assert isinstance(active_row[1], Text)
    assert active_row[2] == Text("111.11 TPS", style="bold bright_green")
    assert active_row[3] == active_cell
    assert active_row[4] == Text("WAITING", style="grey62")


@pytest.mark.asyncio
async def test_run_screen_back_does_not_leave_screen_while_running(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = app.screen
        await run_screen.action_back()
        await pilot.pause()

        assert app.screen is run_screen
        assert _FakeController.instances[0].cancel_called is False


@pytest.mark.asyncio
async def test_run_screen_marks_failed_cell_in_soft_red(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = app.screen
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                status_message="Running async benchmark",
                current_workload="sleep",
                current_ordering="key_hash",
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
            "sleep-key_hash", "async"
        )
        loading_indicator = run_screen.query_one("#run-loading", LoadingIndicator)
        status = run_screen.query_one("#run-status", Static)
        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert isinstance(failed_cell, Text)
    assert failed_cell.plain == "FAILED"
    assert "red" in str(failed_cell.style)
    assert loading_indicator.display is False
    assert str(status.content) == "벤치마크가 실패했습니다"
    assert reason.has_class("is-failed")


@pytest.mark.asyncio
async def test_run_screen_surfaces_last_error_line_in_failure_status(
    monkeypatch
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = app.screen
        run_screen._append_log("RuntimeError: boom", is_error=True)
        run_screen._on_complete(1)
        await pilot.pause()

        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert str(reason.content) == "종료 사유: RuntimeError: boom"


@pytest.mark.asyncio
async def test_run_screen_exposes_report_and_exit_controls_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text('{"options": {}, "results": []}', encoding="utf-8")

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            del on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_complete(0)

        async def cancel(self) -> None:
            return None

    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _CompletedController
    )

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = app.screen
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        run_screen = app.screen
        report_button = run_screen.query_one("#cancel-button", Button)
        settings_button = run_screen.query_one("#settings-button", Button)
        exit_button = run_screen.query_one("#exit-button", Button)

    assert str(report_button.label) == "결과 다시 보기"
    assert str(settings_button.label) == "설정으로 돌아가기"
    assert str(exit_button.label) == "종료"


@pytest.mark.asyncio
async def test_run_screen_reopens_results_modal_from_terminal_report_button(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text('{"options": {}, "results": []}', encoding="utf-8")

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            del on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_complete(0)

        async def cancel(self) -> None:
            return None

    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _CompletedController
    )

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = app.screen
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        await app.screen.action_cancel()
        await pilot.pause()

        assert app.screen.__class__.__name__ == "ResultsSummaryModalScreen"


@pytest.mark.asyncio
async def test_run_screen_exit_control_quits_app_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text('{"options": {}, "results": []}', encoding="utf-8")

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            del on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_complete(0)

        async def cancel(self) -> None:
            return None

    exit_calls: list[object] = []

    def _record_exit(*args, **kwargs) -> None:
        del args
        del kwargs
        exit_calls.append(object())

    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _CompletedController
    )

    app = BenchmarkTuiApp()
    monkeypatch.setattr(app, "exit", _record_exit)

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = app.screen
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        await app.screen.action_exit()

    assert len(exit_calls) == 1
