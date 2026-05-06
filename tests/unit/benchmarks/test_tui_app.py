from __future__ import annotations

import asyncio
import shlex
import signal
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from rich.text import Text
from textual.widgets import (
    Button,
    DataTable,
    Input,
    Label,
    ProgressBar,
    SelectionList,
    Static,
    Switch,
)

from benchmarks.tui.app import (
    BenchmarkTuiApp,
    OptionsScreen,
    ResultsSummaryModalScreen,
    RunScreen,
)
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


def _run_screen(app: BenchmarkTuiApp) -> RunScreen:
    return cast(RunScreen, app.screen)


def _options_screen(app: BenchmarkTuiApp) -> OptionsScreen:
    return cast(OptionsScreen, app.screen)


def _results_modal_screen(app: BenchmarkTuiApp) -> ResultsSummaryModalScreen:
    return cast(ResultsSummaryModalScreen, app.screen)


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


def _assert_text_cell(cell, plain: str, style: str) -> None:
    assert isinstance(cell, Text)
    assert cell.plain == plain
    assert str(cell.style) == style


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
        input_block_height = input_block.region.height
        checkbox_block_height = checkbox_block.region.height

    assert input_block_height > 1
    assert checkbox_block_height > 1


@pytest.mark.asyncio
async def test_options_footer_does_not_overlap_scroll_region() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test(size=(100, 60)) as pilot:
        await pilot.pause()
        options_screen = app.screen.query_one("#options-screen")
        options_footer = app.screen.query_one("#options-footer")
        options_screen_bottom = options_screen.region.y + options_screen.region.height
        options_footer_top = options_footer.region.y

    assert options_screen_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_default_workload_option_is_visible_above_options_footer() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        options_footer = app.screen.query_one("#options-footer")
        sleep_option_bottom = sleep_option.region.y + sleep_option.region.height
        options_footer_top = options_footer.region.y

    assert sleep_option_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_ordering_modes_remain_visible_with_selected_workload_options() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        ordering_modes = app.screen.query_one("#ordering-modes", SelectionList)
        options_footer = app.screen.query_one("#options-footer")
        ordering_bottom = ordering_modes.region.y + ordering_modes.region.height
        options_footer_top = options_footer.region.y

    assert ordering_bottom <= options_footer_top


@pytest.mark.asyncio
async def test_workload_option_group_uses_content_height() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test(size=(100, 80)) as pilot:
        await pilot.pause()
        group = app.screen.query_one("#workload-options-sleep")
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)

    assert group.region.height < 6
    assert sleep_option.region.y + sleep_option.region.height <= (
        group.region.y + group.region.height
    )


@pytest.mark.asyncio
async def test_options_screen_hides_empty_field_errors() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        error = app.screen.query_one("#error-num-messages", Static)

        assert error.display is False

        num_messages.value = "oops"
        await pilot.pause()
        assert error.display is True

        num_messages.value = "100"
        await pilot.pause()

    assert error.display is False


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
    assert "Process count" in labels
    assert "Process batch size" in labels
    assert "Process max batch wait (ms)" in labels
    assert "Process route batch size" in labels


@pytest.mark.asyncio
async def test_options_screen_uses_prominent_title_and_helper_text() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        title = app.screen.query_one("#options-title", Static)
        help_texts = [
            str(cast(Static, widget).content)
            for widget in app.screen.query(".option-help")
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
        process_ancestors = _ancestor_ids(
            app.screen.query_one("#option-block-process-route-batch-size")
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
    assert "option-section-output-execution" in process_ancestors
    assert "option-section-profiling" in profiling_ancestors
    assert "option-section-advanced-options" in topic_prefix_ancestors


@pytest.mark.asyncio
async def test_options_screen_places_workload_matrix_before_detail_knobs() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        await pilot.pause()
        workloads = app.screen.query_one("#option-block-workloads")
        ordering_modes = app.screen.query_one("#option-block-ordering-modes")
        workload_options = app.screen.query_one("#workload-options")
        bootstrap = app.screen.query_one("#option-block-bootstrap-servers")
        positions = (
            workloads.region.y,
            ordering_modes.region.y,
            workload_options.region.y,
            bootstrap.region.y,
        )

    assert positions == tuple(sorted(positions))


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
async def test_options_screen_renders_selected_workload_option_controls() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        labels = [str(label.render()) for label in app.screen.query(Label)]

    assert sleep_option.value == "0.5"
    assert "Sleep per message" in labels


@pytest.mark.asyncio
async def test_options_screen_does_not_render_legacy_builtin_workload_option_inputs() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        dynamic_sleep = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        legacy_sleep = list(app.screen.query("#worker-sleep-ms"))
        legacy_cpu = list(app.screen.query("#worker-cpu-iterations"))
        legacy_io = list(app.screen.query("#worker-io-sleep-ms"))

    assert dynamic_sleep.value == "0.5"
    assert legacy_sleep == []
    assert legacy_cpu == []
    assert legacy_io == []


@pytest.mark.asyncio
async def test_options_screen_updates_preview_with_dynamic_workload_option() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        sleep_option.value = "1.25"
        await pilot.pause()

        preview = app.screen.query_one("#argv-preview", Static)

    assert "--worker-sleep-ms 1.25" in str(preview.content)


@pytest.mark.asyncio
async def test_options_screen_updates_workload_option_controls_when_selection_changes() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        workloads = app.screen.query_one("#workloads", SelectionList)
        workloads.select("cpu")
        await pilot.pause()

        cpu_option = app.screen.query_one("#workload-option-cpu-iterations", Input)

    assert cpu_option.value == "1000"


@pytest.mark.asyncio
async def test_options_screen_workload_option_refresh_preserves_unrelated_inputs() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        num_messages.value = "12345"
        workloads = app.screen.query_one("#workloads", SelectionList)
        workloads.select("cpu")
        await pilot.pause()

        restored_messages = app.screen.query_one("#num-messages", Input)
        preview = app.screen.query_one("#argv-preview", Static)

    assert restored_messages.value == "12345"
    assert "--num-messages 12345" in str(preview.content)


@pytest.mark.asyncio
async def test_options_screen_workload_option_refresh_preserves_dynamic_draft_when_form_invalid() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        num_messages.value = "not-a-number"
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        sleep_option.value = "1.75"
        workloads = app.screen.query_one("#workloads", SelectionList)
        workloads.select("cpu")
        await pilot.pause()

        preserved_sleep_option = app.screen.query_one(
            "#workload-option-sleep-sleep_ms", Input
        )

    assert preserved_sleep_option.value == "1.75"


@pytest.mark.asyncio
async def test_options_screen_renders_custom_workload_option_schema(
    monkeypatch,
) -> None:
    from benchmarks.workloads.base import WorkloadOptionMetadata

    @dataclass(frozen=True, slots=True)
    class CustomOptions:
        retries: int = field(
            default=3,
            metadata={
                "workload_option": WorkloadOptionMetadata(
                    label="Retry count", description="Attempts per message.", minimum=0
                )
            },
        )

    class CustomWorkload:
        name = "custom"
        options_type = CustomOptions

    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(
                name="custom",
                available=True,
                workload_cls=CustomWorkload,
                error=None,
            ),
        ),
    )
    monkeypatch.setattr("benchmarks.workloads.available_names", lambda: ("custom",))
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        custom_option = app.screen.query_one("#workload-option-custom-retries", Input)
        labels = [str(label.render()) for label in app.screen.query(Label)]

    assert custom_option.value == "3"
    assert "Retry count" in labels


@pytest.mark.asyncio
async def test_options_screen_invalid_dynamic_workload_option_blocks_run() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        sleep_option.value = "nan"
        await pilot.pause()

        run_button = app.screen.query_one("#run-button", Button)
        error = app.screen.query_one("#error-workload-option-sleep-sleep_ms", Static)

    assert run_button.disabled is True
    assert "sleep.sleep_ms" in str(error.content)


@pytest.mark.asyncio
async def test_options_screen_validates_dynamic_workload_options_together(
    monkeypatch,
) -> None:
    from benchmarks.workloads.base import WorkloadOptionMetadata

    @dataclass(frozen=True, slots=True)
    class RangeOptions:
        lower: int = field(
            default=0,
            metadata={"workload_option": WorkloadOptionMetadata(label="Lower")},
        )
        upper: int = field(
            default=10,
            metadata={"workload_option": WorkloadOptionMetadata(label="Upper")},
        )

        def __post_init__(self) -> None:
            if self.lower > self.upper:
                raise ValueError("lower must not exceed upper")

    class RangeWorkload:
        name = "range"
        options_type = RangeOptions

    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(
                name="range",
                available=True,
                workload_cls=RangeWorkload,
                error=None,
            ),
        ),
    )
    monkeypatch.setattr("benchmarks.workloads.available_names", lambda: ("range",))
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        lower = app.screen.query_one("#workload-option-range-lower", Input)
        upper = app.screen.query_one("#workload-option-range-upper", Input)
        lower.value = "7"
        upper.value = "5"
        await pilot.pause()

        run_button = app.screen.query_one("#run-button", Button)
        error = app.screen.query_one("#error-workload-option-range-lower", Static)

    assert run_button.disabled is True
    assert "lower must not exceed upper" in str(error.content)


def test_options_screen_builds_workloads_from_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(name="sleep", available=True, error=None),
            SimpleNamespace(name="custom", available=True, error=None),
            SimpleNamespace(name="broken", available=False, error="import failed"),
        ),
    )

    selections = OptionsScreen._workload_selections(
        BenchmarkTuiState(workloads=("custom", "broken"))
    )

    assert selections == [
        ("sleep", "sleep", False),
        ("custom", "custom", True),
        ("broken (unavailable)", "broken", True),
    ]
    assert OptionsScreen._unavailable_workload_reasons() == {"broken": "import failed"}


def test_options_screen_deduplicates_duplicate_unavailable_workload_records(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(name="dupe", available=False, error="duplicate name"),
            SimpleNamespace(name="dupe", available=False, error="duplicate name"),
            SimpleNamespace(name="sleep", available=True, error=None),
        ),
    )

    selections = OptionsScreen._workload_selections(BenchmarkTuiState(workloads=()))

    assert selections == [
        ("dupe (unavailable)", "dupe", False),
        ("sleep", "sleep", False),
    ]
    assert OptionsScreen._unavailable_workload_reasons() == {"dupe": "duplicate name"}


def test_run_screen_uses_custom_selected_workloads() -> None:
    screen = RunScreen(BenchmarkTuiState(workloads=("custom",)))

    assert screen._active_workloads == ("custom",)


@pytest.mark.asyncio
async def test_options_screen_exposes_output_path_fields_with_browse_buttons() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        json_output = app.screen.query_one("#json-output", Input)
        profile_dir = app.screen.query_one("#profile-dir", Input)
        py_spy_output = app.screen.query_one("#py-spy-output", Input)
        browse_buttons = {
            cast(Button, button).id: str(cast(Button, button).label)
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
async def test_options_screen_exposes_process_route_batch_controls() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        process_count = app.screen.query_one("#process-count", Input)
        process_batch_size = app.screen.query_one("#process-batch-size", Input)
        process_max_batch_wait_ms = app.screen.query_one(
            "#process-max-batch-wait-ms", Input
        )
        route_batch_size = app.screen.query_one("#process-route-batch-size", Input)

    assert process_count.value == "4"
    assert process_batch_size.value == "1"
    assert process_max_batch_wait_ms.value == "0"
    assert route_batch_size.value == "64"


@pytest.mark.asyncio
async def test_options_screen_updates_preview_with_process_route_batch_controls() -> (
    None
):
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        process_count = app.screen.query_one("#process-count", Input)
        process_batch_size = app.screen.query_one("#process-batch-size", Input)
        process_max_batch_wait_ms = app.screen.query_one(
            "#process-max-batch-wait-ms", Input
        )
        route_batch_size = app.screen.query_one("#process-route-batch-size", Input)

        process_count.value = "4"
        process_batch_size.value = "1"
        process_max_batch_wait_ms.value = "0"
        route_batch_size.value = "64"
        await pilot.pause()

        preview = app.screen.query_one("#argv-preview", Static)

    preview_text = str(preview.content)
    assert "--process-count 4" in preview_text
    assert "--process-batch-size 1" in preview_text
    assert "--process-max-batch-wait-ms 0" in preview_text
    assert "--process-route-batch-size 64" in preview_text


@pytest.mark.asyncio
async def test_options_screen_copies_full_cli_command(monkeypatch) -> None:
    app = BenchmarkTuiApp()
    copied_values: list[str] = []

    def _record_clipboard(text: str) -> None:
        copied_values.append(text)

    monkeypatch.setattr(app, "copy_to_clipboard", _record_clipboard)

    async with app.run_test() as pilot:
        await pilot.click("#copy-command-button")
        await pilot.pause()

        status = app.screen.query_one("#copy-command-status", Static)

    assert copied_values == [
        shlex.join(
            [
                "uv",
                "run",
                "python",
                "-m",
                "benchmarks.run_parallel_benchmark",
                *BenchmarkTuiState().to_argv(),
            ]
        )
    ]
    assert str(status.content) == "CLI command copied to clipboard."


@pytest.mark.asyncio
async def test_options_screen_shell_quotes_copied_cli_command(monkeypatch) -> None:
    state = BenchmarkTuiState(json_output="benchmarks/results/space path.json")
    app = BenchmarkTuiApp()
    copied_values: list[str] = []

    def _record_clipboard(text: str) -> None:
        copied_values.append(text)

    monkeypatch.setattr(app, "copy_to_clipboard", _record_clipboard)

    async with app.run_test() as pilot:
        json_output = app.screen.query_one("#json-output", Input)
        json_output.value = state.json_output
        await pilot.pause()

        await pilot.click("#copy-command-button")
        await pilot.pause()

    assert copied_values == [
        shlex.join(
            [
                "uv",
                "run",
                "python",
                "-m",
                "benchmarks.run_parallel_benchmark",
                *state.to_argv(),
            ]
        )
    ]
    assert "'benchmarks/results/space path.json'" in copied_values[0]


@pytest.mark.asyncio
async def test_browse_button_opens_directory_picker_modal() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        await pilot.click("#browse-profile-dir")
        await pilot.pause()

        app.screen_stack[-1]
        selected_path = Path.cwd()
        app.pop_screen()
        _options_screen(app).apply_selected_path("profile-dir", selected_path)
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

        await _run_screen(app).action_settings()

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

        await _run_screen(app).action_back()
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

        run_screen = _run_screen(app)
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
        assert "run-spotlight-card" in _ancestor_ids(progress_bar)
        assert "run-spotlight-card" in _ancestor_ids(summary_table)
        assert not list(app.screen.query("#run-loading"))
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

        run_screen = _run_screen(app)
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
async def test_run_screen_marks_results_below_workload_baseline_average_yellow(
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
                )
            )
        )
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=6,
                total_runs=6,
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "100.00",
                            "async": "149.99",
                            "process": "150.00",
                        },
                        "partition": {
                            "baseline": "200.00",
                            "async": "151.00",
                            "process": "--",
                        },
                    },
                },
            )
        )
        await pilot.pause()

        summary_table = run_screen.query_one("#run-summary", DataTable)

    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "baseline"),
        "100.00 TPS",
        "bold bright_green",
    )
    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "async"),
        "149.99 TPS",
        "bold bright_yellow",
    )
    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "process"),
        "150.00 TPS",
        "bold bright_green",
    )
    _assert_text_cell(
        summary_table.get_cell("sleep-partition", "async"),
        "151.00 TPS",
        "bold bright_green",
    )


@pytest.mark.asyncio
async def test_run_screen_keeps_baseline_comparison_for_comma_formatted_tps(
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
                )
            )
        )
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=6,
                total_runs=6,
                tps_by_workload_ordering={
                    "sleep": {
                        "key_hash": {
                            "baseline": "1,000.00",
                            "async": "1,499.99",
                            "process": "1,500.00",
                        },
                        "partition": {
                            "baseline": "2,000.00",
                            "async": "1,501.00",
                            "process": "--",
                        },
                    },
                },
            )
        )
        run_screen._on_complete(0)
        await pilot.pause()

        modal_screen = _results_modal_screen(app)
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        summary_table = _run_screen(app).query_one("#run-summary", DataTable)

    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "async"),
        "1,499.99 TPS",
        "bold bright_yellow",
    )
    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "process"),
        "1,500.00 TPS",
        "bold bright_green",
    )
    _assert_text_cell(
        summary_table.get_cell("sleep-partition", "async"),
        "1,501.00 TPS",
        "bold bright_green",
    )


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

        run_screen = _run_screen(app)
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
    assert active_cell.plain.endswith(" RUNNING")


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

        run_screen = _run_screen(app)
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

        run_screen = _run_screen(app)
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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = _run_screen(app)
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
    assert active_cell.plain.endswith(" RUNNING")
    active_row = summary_table.get_row("sleep-key_hash")
    assert isinstance(active_row[0], Text)
    assert isinstance(active_row[1], Text)
    assert active_row[2] == Text("111.11 TPS", style="bold bright_green")
    assert isinstance(active_row[3], Text)
    assert active_row[3].plain.endswith(" RUNNING")
    assert active_row[3].style == active_cell.style
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

        run_screen = _run_screen(app)
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

        run_screen = _run_screen(app)
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
        status = run_screen.query_one("#run-status", Static)
        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert isinstance(failed_cell, Text)
    assert failed_cell.plain == "FAILED"
    assert "red" in str(failed_cell.style)
    assert not list(run_screen.query("#run-loading"))
    assert str(status.content) == "벤치마크가 실패했습니다"
    assert reason.has_class("is-failed")


@pytest.mark.asyncio
async def test_run_screen_animates_running_summary_cell_without_loading_widget(
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
                    ordering_modes=("partition",),
                    skip_baseline=True,
                    skip_process=True,
                )
            )
        )
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=0,
                current_workload="sleep",
                current_ordering="partition",
                phase_statuses={"async": "running"},
                total_runs=1,
            )
        )
        first_cell = run_screen.query_one("#run-summary", DataTable).get_cell(
            "sleep-partition", "async"
        )
        run_screen._advance_running_spinner()
        second_cell = run_screen.query_one("#run-summary", DataTable).get_cell(
            "sleep-partition", "async"
        )

    assert run_screen._spinner_interval_seconds < 0.2
    assert isinstance(first_cell, Text)
    assert isinstance(second_cell, Text)
    assert first_cell.plain == "⠋ RUNNING"
    assert second_cell.plain == "⠙ RUNNING"
    assert not list(run_screen.query("#run-loading"))


@pytest.mark.asyncio
async def test_run_screen_spinner_refresh_updates_only_active_summary_cell(
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
                    workloads=("sleep", "cpu", "io"),
                    ordering_modes=("key_hash", "partition"),
                )
            )
        )
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._render_snapshot(
            BenchmarkProgressSnapshot(
                completed_runs=1,
                current_workload="cpu",
                current_ordering="partition",
                phase_statuses={"async": "running"},
                total_runs=18,
            )
        )
        table = run_screen.query_one("#run-summary", DataTable)
        calls: list[tuple[object, object, object, object]] = []
        original_update_cell = table.update_cell

        def _record_update_cell(*args: Any, **kwargs: Any) -> object:
            if len(args) != 3:
                raise AssertionError("Expected row key, column key, and value")
            calls.append((args[0], args[1], args[2], kwargs.get("update_width")))
            return original_update_cell(*args, **kwargs)

        monkeypatch.setattr(table, "update_cell", _record_update_cell)
        run_screen._advance_running_spinner()

    assert len(calls) == 1
    row_key, column_key, value, update_width = calls[0]
    assert row_key == "cpu-partition"
    assert column_key == "async"
    assert isinstance(value, Text)
    assert value.plain == "⠙ RUNNING"
    assert update_width is False


@pytest.mark.asyncio
async def test_run_screen_surfaces_last_error_line_in_failure_status(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._append_log("RuntimeError: boom", is_error=True)
        run_screen._on_complete(1)
        await pilot.pause()

        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert str(reason.content) == "종료 사유: RuntimeError: boom"


@pytest.mark.asyncio
async def test_run_screen_shows_kill_button_for_metrics_port_pid_error(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()
    killed: list[tuple[int, signal.Signals]] = []

    def _fake_kill(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr("benchmarks.tui.screens.run.os.kill", _fake_kill)

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._append_log(
            "error: Metrics port 9091 is already in use(PID 1234). Stop it.",
            is_error=True,
        )
        run_screen._on_complete(1)
        await pilot.pause()

        kill_button = run_screen.query_one("#kill-metrics-port-button", Button)
        assert kill_button.display is True
        assert str(kill_button.label) == "Kill PID 1234?"

        await pilot.click("#kill-metrics-port-button")
        await pilot.pause()

        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert killed == [(1234, signal.SIGTERM)]
    assert str(reason.content) == "종료 신호 전송: PID 1234"


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

        modal_screen = _results_modal_screen(app)
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        run_screen = app.screen
        report_button = run_screen.query_one("#cancel-button", Button)
        settings_button = run_screen.query_one("#settings-button", Button)
        exit_button = run_screen.query_one("#exit-button", Button)

    assert str(report_button.label) == "View results"
    assert str(settings_button.label) == "Back to settings"
    assert str(exit_button.label) == "Exit"


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

        modal_screen = _results_modal_screen(app)
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        await _run_screen(app).action_cancel()
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

        modal_screen = _results_modal_screen(app)
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        await _run_screen(app).action_exit()

    assert len(exit_calls) == 1
