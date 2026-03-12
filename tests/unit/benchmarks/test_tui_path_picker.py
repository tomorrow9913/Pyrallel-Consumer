from __future__ import annotations

from pathlib import Path

import pytest
from textual.app import App, ComposeResult
from textual.widgets import DirectoryTree, Static

from benchmarks.tui.path_picker import DirectoryPickerScreen


class _DirectoryPickerHarness(App[None]):
    def __init__(self, start_path: Path) -> None:
        super().__init__()
        self._start_path = start_path
        self.selected_path: str | None = "not-set"

    def compose(self) -> ComposeResult:
        yield Static("", id="picker-host")

    def on_mount(self) -> None:
        self.push_screen(
            DirectoryPickerScreen(self._start_path),
            callback=self._capture_result,
        )

    def _capture_result(self, selected_path: str | None) -> None:
        self.selected_path = selected_path


@pytest.mark.asyncio
async def test_directory_picker_renders_directory_tree(tmp_path: Path) -> None:
    app = _DirectoryPickerHarness(tmp_path)

    async with app.run_test() as pilot:
        del pilot
        tree = app.screen.query_one(DirectoryTree)

    assert Path(str(tree.path)) == tmp_path


@pytest.mark.asyncio
async def test_directory_picker_confirm_returns_selected_directory(
    tmp_path: Path,
) -> None:
    app = _DirectoryPickerHarness(tmp_path)

    async with app.run_test() as pilot:
        await pilot.click("#directory-picker-confirm")
        await pilot.pause()

    assert app.selected_path == str(tmp_path)


@pytest.mark.asyncio
async def test_directory_picker_cancel_returns_none(tmp_path: Path) -> None:
    app = _DirectoryPickerHarness(tmp_path)

    async with app.run_test() as pilot:
        await pilot.click("#directory-picker-cancel")
        await pilot.pause()

    assert app.selected_path is None
