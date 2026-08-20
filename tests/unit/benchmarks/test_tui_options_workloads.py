# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_tui_options_workloads.py
# Role: Verifies workload selection, dynamic workload options, preview updates, and registry handling.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._tui_app_support import (
    BenchmarkTuiApp,
    BenchmarkTuiState,
    Button,
    Input,
    Label,
    OptionsScreen,
    RunScreen,
    SelectionList,
    SimpleNamespace,
    Static,
    dataclass,
    field,
    pytest,
)


@pytest.mark.asyncio
async def test_options_screen_updates_preview_from_execution_mode_selection() -> None:
    # Given: Inputs and test doubles are prepared for options screen updates preview from execution mode selection.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen updates preview from execution mode selection.
    async with app.run_test() as pilot:
        execution = app.screen.query_one("#execution-modes", SelectionList)
        execution.deselect("process")
        await pilot.pause()

        preview = app.screen.query_one("#argv-preview", Static)

    # Then: The expected options screen updates preview from execution mode selection behavior is asserted.
    assert "--skip-process" in str(preview.content)


@pytest.mark.asyncio
async def test_options_screen_renders_selected_workload_option_controls() -> None:
    # Given: Inputs and test doubles are prepared for options screen renders selected workload option controls.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen renders selected workload option controls.
    async with app.run_test() as pilot:
        del pilot
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        labels = [str(label.render()) for label in app.screen.query(Label)]

    # Then: The expected options screen renders selected workload option controls behavior is asserted.
    assert sleep_option.value == "0.5"
    assert "Sleep per message" in labels


@pytest.mark.asyncio
async def test_options_screen_does_not_render_legacy_builtin_workload_option_inputs() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen does not render legacy builtin workload option inputs.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen does not render legacy builtin workload option inputs.
    async with app.run_test() as pilot:
        del pilot
        dynamic_sleep = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        legacy_sleep = list(app.screen.query("#worker-sleep-ms"))
        legacy_cpu = list(app.screen.query("#worker-cpu-iterations"))
        legacy_io = list(app.screen.query("#worker-io-sleep-ms"))

    # Then: The expected options screen does not render legacy builtin workload option inputs behavior is asserted.
    assert dynamic_sleep.value == "0.5"
    assert legacy_sleep == []
    assert legacy_cpu == []
    assert legacy_io == []


@pytest.mark.asyncio
async def test_options_screen_updates_preview_with_dynamic_workload_option() -> None:
    # Given: Inputs and test doubles are prepared for options screen updates preview with dynamic workload option.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen updates preview with dynamic workload option.
    async with app.run_test() as pilot:
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        sleep_option.value = "1.25"
        await pilot.pause()

        preview = app.screen.query_one("#argv-preview", Static)

    # Then: The expected options screen updates preview with dynamic workload option behavior is asserted.
    assert "--worker-sleep-ms 1.25" in str(preview.content)


@pytest.mark.asyncio
async def test_options_screen_updates_workload_option_controls_when_selection_changes() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen updates workload option controls when selection changes.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen updates workload option controls when selection changes.
    async with app.run_test() as pilot:
        workloads = app.screen.query_one("#workloads", SelectionList)
        workloads.select("cpu")
        await pilot.pause()

        cpu_option = app.screen.query_one("#workload-option-cpu-iterations", Input)

    # Then: The expected options screen updates workload option controls when selection changes behavior is asserted.
    assert cpu_option.value == "1000"


@pytest.mark.asyncio
async def test_options_screen_workload_option_refresh_preserves_unrelated_inputs() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen workload option refresh preserves unrelated inputs.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen workload option refresh preserves unrelated inputs.
    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        num_messages.value = "12345"
        workloads = app.screen.query_one("#workloads", SelectionList)
        workloads.select("cpu")
        await pilot.pause()

        restored_messages = app.screen.query_one("#num-messages", Input)
        preview = app.screen.query_one("#argv-preview", Static)

    # Then: The expected options screen workload option refresh preserves unrelated inputs behavior is asserted.
    assert restored_messages.value == "12345"
    assert "--num-messages 12345" in str(preview.content)


@pytest.mark.asyncio
async def test_options_screen_workload_option_refresh_preserves_dynamic_draft_when_form_invalid() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen workload option refresh preserves dynamic draft when form invalid.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen workload option refresh preserves dynamic draft when form invalid.
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

    # Then: The expected options screen workload option refresh preserves dynamic draft when form invalid behavior is asserted.
    assert preserved_sleep_option.value == "1.75"


@pytest.mark.asyncio
async def test_options_screen_renders_custom_workload_option_schema(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for options screen renders custom workload option schema.
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

    # When: The benchmark TUI workload options path is exercised for options screen renders custom workload option schema.
    async with app.run_test() as pilot:
        del pilot
        custom_option = app.screen.query_one("#workload-option-custom-retries", Input)
        labels = [str(label.render()) for label in app.screen.query(Label)]

    # Then: The expected options screen renders custom workload option schema behavior is asserted.
    assert custom_option.value == "3"
    assert "Retry count" in labels


@pytest.mark.asyncio
async def test_options_screen_invalid_dynamic_workload_option_blocks_run() -> None:
    # Given: Inputs and test doubles are prepared for options screen invalid dynamic workload option blocks run.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI workload options path is exercised for options screen invalid dynamic workload option blocks run.
    async with app.run_test() as pilot:
        sleep_option = app.screen.query_one("#workload-option-sleep-sleep_ms", Input)
        sleep_option.value = "nan"
        await pilot.pause()

        run_button = app.screen.query_one("#run-button", Button)
        error = app.screen.query_one("#error-workload-option-sleep-sleep_ms", Static)

    # Then: The expected options screen invalid dynamic workload option blocks run behavior is asserted.
    assert run_button.disabled is True
    assert "sleep.sleep_ms" in str(error.content)


@pytest.mark.asyncio
async def test_options_screen_validates_dynamic_workload_options_together(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for options screen validates dynamic workload options together.
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

    # When: The benchmark TUI workload options path is exercised for options screen validates dynamic workload options together.
    async with app.run_test() as pilot:
        lower = app.screen.query_one("#workload-option-range-lower", Input)
        upper = app.screen.query_one("#workload-option-range-upper", Input)
        lower.value = "7"
        upper.value = "5"
        await pilot.pause()

        run_button = app.screen.query_one("#run-button", Button)
        error = app.screen.query_one("#error-workload-option-range-lower", Static)

    # Then: The expected options screen validates dynamic workload options together behavior is asserted.
    assert run_button.disabled is True
    assert "lower must not exceed upper" in str(error.content)


def test_options_screen_builds_workloads_from_registry(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for options screen builds workloads from registry.
    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(name="sleep", available=True, error=None),
            SimpleNamespace(name="custom", available=True, error=None),
            SimpleNamespace(name="broken", available=False, error="import failed"),
        ),
    )

    # When: The benchmark TUI workload options path is exercised for options screen builds workloads from registry.
    selections = OptionsScreen._workload_selections(
        BenchmarkTuiState(workloads=("custom", "broken"))
    )

    # Then: The expected options screen builds workloads from registry behavior is asserted.
    assert selections == [
        ("sleep", "sleep", False),
        ("custom", "custom", True),
        ("broken (unavailable)", "broken", True),
    ]
    assert OptionsScreen._unavailable_workload_reasons() == {"broken": "import failed"}


def test_options_screen_deduplicates_duplicate_unavailable_workload_records(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for options screen deduplicates duplicate unavailable workload records.
    monkeypatch.setattr(
        "benchmarks.tui.screens.options.all_records",
        lambda: (
            SimpleNamespace(name="dupe", available=False, error="duplicate name"),
            SimpleNamespace(name="dupe", available=False, error="duplicate name"),
            SimpleNamespace(name="sleep", available=True, error=None),
        ),
    )

    # When: The benchmark TUI workload options path is exercised for options screen deduplicates duplicate unavailable workload records.
    selections = OptionsScreen._workload_selections(BenchmarkTuiState(workloads=()))

    # Then: The expected options screen deduplicates duplicate unavailable workload records behavior is asserted.
    assert selections == [
        ("dupe (unavailable)", "dupe", False),
        ("sleep", "sleep", False),
    ]
    assert OptionsScreen._unavailable_workload_reasons() == {"dupe": "duplicate name"}


def test_run_screen_uses_custom_selected_workloads() -> None:
    # Given: Inputs and test doubles are prepared for run screen uses custom selected workloads.
    # When: The benchmark TUI workload options path is exercised for run screen uses custom selected workloads.
    screen = RunScreen(BenchmarkTuiState(workloads=("custom",)))

    # Then: The expected run screen uses custom selected workloads behavior is asserted.
    assert screen._active_workloads == ("custom",)
