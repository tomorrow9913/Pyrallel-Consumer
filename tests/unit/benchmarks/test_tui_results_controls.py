# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_tui_results_controls.py
# Role: Verifies result modal, terminal report, and exit controls after benchmark completion.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._tui_app_support import (
    BenchmarkTuiApp,
    BenchmarkTuiState,
    Button,
    Input,
    Path,
    RunScreen,
    _results_modal_screen,
    _run_screen,
    pytest,
)


@pytest.mark.asyncio
async def test_success_modal_returns_to_options_with_existing_values(
    monkeypatch, tmp_path: Path
) -> None:
    # Given: Inputs and test doubles are prepared for success modal returns to options with existing values.
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

    # When: The benchmark TUI result controls path is exercised for success modal returns to options with existing values.
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

    # Then: The expected success modal returns to options with existing values behavior is asserted.
    assert restored_messages.value == "42"
    assert restored_output.value == str(results_path)


@pytest.mark.asyncio
async def test_failed_run_returns_to_options_with_existing_values(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for failed run returns to options with existing values.
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

    # When: The benchmark TUI result controls path is exercised for failed run returns to options with existing values.
    async with app.run_test() as pilot:
        num_messages = app.screen.query_one("#num-messages", Input)
        num_messages.value = "4242"
        await pilot.pause()

        await pilot.click("#run-button")
        await pilot.pause()
        await pilot.pause()

        await _run_screen(app).action_settings()

        restored_messages = app.screen.query_one("#num-messages", Input)

    # Then: The expected failed run returns to options with existing values behavior is asserted.
    assert restored_messages.value == "4242"


@pytest.mark.asyncio
async def test_run_screen_exposes_report_and_exit_controls_after_success(
    monkeypatch, tmp_path: Path
) -> None:
    # Given: Inputs and test doubles are prepared for run screen exposes report and exit controls after success.
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

    # When: The benchmark TUI result controls path is exercised for run screen exposes report and exit controls after success.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = _results_modal_screen(app)
        # Then: The expected run screen exposes report and exit controls after success behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen reopens results modal from terminal report button.
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

    # When: The benchmark TUI result controls path is exercised for run screen reopens results modal from terminal report button.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = _results_modal_screen(app)
        # Then: The expected run screen reopens results modal from terminal report button behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen exit control quits app after success.
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

    # When: The benchmark TUI result controls path is exercised for run screen exit control quits app after success.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(json_output=str(results_path))))
        await pilot.pause()
        await pilot.pause()

        modal_screen = _results_modal_screen(app)
        # Then: The expected run screen exit control quits app after success behavior is asserted.
        assert hasattr(modal_screen, "action_close")
        modal_screen.action_close()
        await pilot.pause()

        await _run_screen(app).action_exit()

    assert len(exit_calls) == 1
