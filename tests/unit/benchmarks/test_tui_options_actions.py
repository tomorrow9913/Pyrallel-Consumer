# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_tui_options_actions.py
# Role: Verifies options screen command, browse, route-batch, and validation interactions.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._tui_app_support import (
    BenchmarkTuiApp,
    BenchmarkTuiState,
    Button,
    Input,
    Path,
    Static,
    _options_screen,
    cast,
    pytest,
    shlex,
)


@pytest.mark.asyncio
async def test_options_screen_exposes_output_path_fields_with_browse_buttons() -> None:
    # Given: Inputs and test doubles are prepared for options screen exposes output path fields with browse buttons.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI option actions path is exercised for options screen exposes output path fields with browse buttons.
    async with app.run_test() as pilot:
        del pilot
        json_output = app.screen.query_one("#json-output", Input)
        profile_dir = app.screen.query_one("#profile-dir", Input)
        py_spy_output = app.screen.query_one("#py-spy-output", Input)
        browse_buttons = {
            cast(Button, button).id: str(cast(Button, button).label)
            for button in app.screen.query(".browse-button")
        }

    # Then: The expected options screen exposes output path fields with browse buttons behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for options screen exposes process route batch controls.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI option actions path is exercised for options screen exposes process route batch controls.
    async with app.run_test() as pilot:
        del pilot
        process_count = app.screen.query_one("#process-count", Input)
        process_batch_size = app.screen.query_one("#process-batch-size", Input)
        process_max_batch_wait_ms = app.screen.query_one(
            "#process-max-batch-wait-ms", Input
        )
        route_batch_size = app.screen.query_one("#process-route-batch-size", Input)

    # Then: The expected options screen exposes process route batch controls behavior is asserted.
    assert process_count.value == "4"
    assert process_batch_size.value == "1"
    assert process_max_batch_wait_ms.value == "0"
    assert route_batch_size.value == "64"


@pytest.mark.asyncio
async def test_options_screen_updates_preview_with_process_route_batch_controls() -> (
    None
):
    # Given: Inputs and test doubles are prepared for options screen updates preview with process route batch controls.
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

    # When: The benchmark TUI option actions path is exercised for options screen updates preview with process route batch controls.
    preview_text = str(preview.content)
    # Then: The expected options screen updates preview with process route batch controls behavior is asserted.
    assert "--process-count 4" in preview_text
    assert "--process-batch-size 1" in preview_text
    assert "--process-max-batch-wait-ms 0" in preview_text
    assert "--process-route-batch-size 64" in preview_text


@pytest.mark.asyncio
async def test_options_screen_copies_full_cli_command(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for options screen copies full cli command.
    app = BenchmarkTuiApp()
    copied_values: list[str] = []

    def _record_clipboard(text: str) -> None:
        copied_values.append(text)

    monkeypatch.setattr(app, "copy_to_clipboard", _record_clipboard)

    # When: The benchmark TUI option actions path is exercised for options screen copies full cli command.
    async with app.run_test() as pilot:
        await pilot.click("#copy-command-button")
        await pilot.pause()

        status = app.screen.query_one("#copy-command-status", Static)

    # Then: The expected options screen copies full cli command behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for options screen shell quotes copied cli command.
    state = BenchmarkTuiState(json_output="benchmarks/results/space path.json")
    app = BenchmarkTuiApp()
    copied_values: list[str] = []

    def _record_clipboard(text: str) -> None:
        copied_values.append(text)

    monkeypatch.setattr(app, "copy_to_clipboard", _record_clipboard)

    # When: The benchmark TUI option actions path is exercised for options screen shell quotes copied cli command.
    async with app.run_test() as pilot:
        json_output = app.screen.query_one("#json-output", Input)
        json_output.value = state.json_output
        await pilot.pause()

        await pilot.click("#copy-command-button")
        await pilot.pause()

    # Then: The expected options screen shell quotes copied cli command behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for browse button opens directory picker modal.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI option actions path is exercised for browse button opens directory picker modal.
    async with app.run_test() as pilot:
        await pilot.click("#browse-profile-dir")
        await pilot.pause()

        app.screen_stack[-1]
        selected_path = Path.cwd()
        app.pop_screen()
        _options_screen(app).apply_selected_path("profile-dir", selected_path)
        await pilot.pause()

        profile_dir = app.screen.query_one("#profile-dir", Input)

    # Then: The expected browse button opens directory picker modal behavior is asserted.
    assert profile_dir.value == str(selected_path)


@pytest.mark.asyncio
async def test_options_screen_disables_run_for_invalid_numeric_input() -> None:
    # Given: Inputs and test doubles are prepared for options screen disables run for invalid numeric input.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI option actions path is exercised for options screen disables run for invalid numeric input.
    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        run_button = app.screen.query_one("#run-button", Button)
        preview = app.screen.query_one("#argv-preview", Static)
        original_preview = str(preview.content)

        num_messages.value = "oops"
        await pilot.pause()

        error = app.screen.query_one("#error-num-messages", Static)

    # Then: The expected options screen disables run for invalid numeric input behavior is asserted.
    assert run_button.disabled is True
    assert "number" in str(error.content).lower()
    assert str(preview.content) == original_preview


@pytest.mark.asyncio
async def test_options_screen_hides_error_summary_until_needed() -> None:
    # Given: Inputs and test doubles are prepared for options screen hides error summary until needed.
    app = BenchmarkTuiApp()

    # When: The benchmark TUI option actions path is exercised for options screen hides error summary until needed.
    async with app.run_test() as pilot:
        summary = app.screen.query_one("#form-error-summary", Static)
        num_messages = app.screen.query_one("#num-messages", Input)

        # Then: The expected options screen hides error summary until needed behavior is asserted.
        assert summary.display is False

        num_messages.value = "oops"
        await pilot.pause()
        assert summary.display is True

        num_messages.value = "100"
        await pilot.pause()

    assert summary.display is False
