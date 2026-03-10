from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.screen import ModalScreen
from textual.widgets import DataTable, ProgressBar, Static

from benchmarks.tui.app import BenchmarkTuiApp, ResultsSummaryModalScreen, RunScreen
from benchmarks.tui.log_parser import BenchmarkProgressSnapshot
from benchmarks.tui.results_report import render_results_summary
from benchmarks.tui.state import BenchmarkTuiState


def test_render_results_summary_builds_report_table(tmp_path: Path) -> None:
    results_path = tmp_path / "benchmark-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-baseline",
                        "run_type": "baseline",
                        "workload": "sleep",
                        "topic": "demo-sleep-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 2.5,
                        "throughput_tps": 40.0,
                        "avg_processing_ms": 1.25,
                        "p99_processing_ms": 2.5,
                    },
                    {
                        "run_name": "sleep-pyrallel-process",
                        "run_type": "pyrallel",
                        "workload": "sleep",
                        "topic": "demo-sleep-process",
                        "messages_processed": 100,
                        "total_time_sec": 1.25,
                        "throughput_tps": 80.0,
                        "avg_processing_ms": 0.75,
                        "p99_processing_ms": 1.5,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = render_results_summary(results_path)

    assert "Run" in summary
    assert "Workload" in summary
    assert "sleep-baseline" in summary
    assert "sleep-pyrallel-process" in summary


def test_results_modal_renders_per_workload_winner_cards(tmp_path: Path) -> None:
    results_path = tmp_path / "benchmark-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-baseline",
                        "run_type": "baseline",
                        "workload": "sleep",
                        "topic": "demo-sleep-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 2.5,
                        "throughput_tps": 40.0,
                        "avg_processing_ms": 1.25,
                        "p99_processing_ms": 2.5,
                    },
                    {
                        "run_name": "sleep-pyrallel-async",
                        "run_type": "async",
                        "workload": "sleep",
                        "topic": "demo-sleep-async",
                        "messages_processed": 100,
                        "total_time_sec": 1.5,
                        "throughput_tps": 66.0,
                        "avg_processing_ms": 1.0,
                        "p99_processing_ms": 2.0,
                    },
                    {
                        "run_name": "cpu-pyrallel-process",
                        "run_type": "process",
                        "workload": "cpu",
                        "topic": "demo-cpu-process",
                        "messages_processed": 100,
                        "total_time_sec": 1.2,
                        "throughput_tps": 80.0,
                        "avg_processing_ms": 0.8,
                        "p99_processing_ms": 1.2,
                    },
                    {
                        "run_name": "io-baseline",
                        "run_type": "baseline",
                        "workload": "io",
                        "topic": "demo-io-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 3.0,
                        "throughput_tps": 30.0,
                        "avg_processing_ms": 1.4,
                        "p99_processing_ms": 3.4,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    modal = ResultsSummaryModalScreen(
        summary_text="unused", output_path=str(results_path)
    )

    assert "sleep 1등" in modal._winner_card_text("sleep")
    assert "async" in modal._winner_card_text("sleep")
    assert "66.00 TPS" in modal._winner_card_text("sleep")
    assert "1.50s" in modal._winner_card_text("sleep")
    assert "cpu 1등" in modal._winner_card_text("cpu")
    assert "process" in modal._winner_card_text("cpu")
    assert "io 1등" in modal._winner_card_text("io")
    assert "baseline" in modal._winner_card_text("io")


def test_results_modal_defaults_to_centered_layout() -> None:
    assert "align: center middle" in ResultsSummaryModalScreen.DEFAULT_CSS


def test_results_modal_cards_use_centered_content_alignment() -> None:
    from benchmarks.tui.app import BenchmarkTuiApp

    assert "content-align: center middle" in BenchmarkTuiApp.CSS


@pytest.mark.asyncio
async def test_run_screen_opens_results_modal_after_completion(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-baseline",
                        "run_type": "baseline",
                        "workload": "sleep",
                        "topic": "demo-sleep-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 2.5,
                        "throughput_tps": 40.0,
                        "avg_processing_ms": 1.25,
                        "p99_processing_ms": 2.5,
                    },
                    {
                        "run_name": "sleep-pyrallel-async",
                        "run_type": "pyrallel",
                        "workload": "sleep",
                        "topic": "demo-sleep-async",
                        "messages_processed": 100,
                        "total_time_sec": 1.5,
                        "throughput_tps": 66.0,
                        "avg_processing_ms": 1.0,
                        "p99_processing_ms": 2.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            self._on_progress = on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_progress(
                BenchmarkProgressSnapshot(
                    output_path=str(results_path),
                    total_runs=3,
                    completed_runs=3,
                )
            )
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

        summary_screen = app.screen
        assert isinstance(summary_screen, ModalScreen)
        summary_table = summary_screen.query_one("#results-table", DataTable)

        sleep_winner = summary_screen.query_one("#results-winner-sleep", Static)
        cpu_winner = summary_screen.query_one("#results-winner-cpu", Static)
        io_winner = summary_screen.query_one("#results-winner-io", Static)
        output_path = summary_screen.query_one("#results-output-path", Static)
        detail_title = summary_screen.query_one("#results-detail-title", Static)
        assert summary_table.get_row_at(0) == [
            "sleep-baseline",
            "baseline",
            "sleep",
            "100",
            "40.00",
            "1.250",
            "2.500",
        ]
        assert summary_table.get_row_at(1) == [
            "sleep-pyrallel-async",
            "pyrallel",
            "sleep",
            "100",
            "66.00",
            "1.000",
            "2.000",
        ]
    assert "sleep 1등" in str(sleep_winner.content)
    assert "async" in str(sleep_winner.content)
    assert "cpu 1등" in str(cpu_winner.content)
    assert "io 1등" in str(io_winner.content)
    assert str(results_path) in str(output_path.content)
    assert "Detailed report" in str(detail_title.content)


@pytest.mark.asyncio
async def test_run_screen_keeps_run_screen_visible_on_failure(monkeypatch) -> None:
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
        app.push_screen(RunScreen(BenchmarkTuiState()))
        await pilot.pause()
        await pilot.pause()

        assert isinstance(app.screen, RunScreen)
        status = app.screen.query_one("#run-status", Static)

    assert "failed" in str(status.content).lower()


@pytest.mark.asyncio
async def test_run_screen_completes_progress_before_showing_success_modal(
    monkeypatch, tmp_path: Path
) -> None:
    results_path = tmp_path / "results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-baseline",
                        "run_type": "baseline",
                        "workload": "sleep",
                        "topic": "demo-sleep-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 2.5,
                        "throughput_tps": 40.0,
                        "avg_processing_ms": 1.25,
                        "p99_processing_ms": 2.5,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class _CompletedController:
        def __init__(self, *, state, on_output, on_progress, on_complete) -> None:
            del state
            del on_output
            self._on_progress = on_progress
            self._on_complete = on_complete

        async def run(self) -> None:
            self._on_progress(
                BenchmarkProgressSnapshot(
                    output_path=str(results_path),
                    total_runs=3,
                    completed_runs=2,
                    progress_value=2.0,
                )
            )
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
        assert isinstance(modal_screen, ModalScreen)

        run_screen = app.screen_stack[-2]
        progress_bar = run_screen.query_one("#run-progress", ProgressBar)

    assert progress_bar.progress == 3
