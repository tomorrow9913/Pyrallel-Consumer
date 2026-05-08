# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/_tui_app_support.py
# Role: Provides shared imports, fake controller, and screen helpers for benchmark TUI tests.
# Extend here when split benchmark TUI tests need shared app helpers or fake controllers.
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

__all__ = (
    "Any",
    "BenchmarkProgressSnapshot",
    "BenchmarkTuiApp",
    "BenchmarkTuiState",
    "Button",
    "DataTable",
    "Input",
    "Label",
    "OptionsScreen",
    "Path",
    "ProgressBar",
    "ResultsSummaryModalScreen",
    "RunScreen",
    "SelectionList",
    "SimpleNamespace",
    "Static",
    "Switch",
    "Text",
    "_FakeController",
    "_ancestor_ids",
    "_assert_text_cell",
    "_block_child_types",
    "_options_screen",
    "_results_modal_screen",
    "_run_screen",
    "asyncio",
    "cast",
    "dataclass",
    "field",
    "pytest",
    "shlex",
    "signal",
)


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
