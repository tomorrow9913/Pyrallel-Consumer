from __future__ import annotations

import asyncio

import pytest
from textual.widgets import Button, Static

from benchmarks.tui.app import BenchmarkTuiApp, RunScreen
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


@pytest.mark.asyncio
async def test_benchmark_tui_app_mounts_with_run_button() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        del pilot
        run_button = app.screen.query_one("#run-button", Button)
        assert str(run_button.label) == "Run benchmark"


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
