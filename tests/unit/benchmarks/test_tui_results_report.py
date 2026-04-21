from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.containers import VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, ProgressBar, Static, Tabs

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
                        "ordering": "key_hash",
                        "topic": "demo-sleep-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 2.5,
                        "throughput_tps": 40.0,
                        "avg_processing_ms": 1.25,
                        "p99_processing_ms": 2.5,
                        "window_size_messages": 100,
                        "tps_p50_window": 39.0,
                        "tps_p10_window": 35.0,
                        "tps_min_window": 30.0,
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
                        "window_size_messages": 100,
                        "tps_p50_window": 78.0,
                        "tps_p10_window": 60.0,
                        "tps_min_window": 50.0,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = render_results_summary(results_path)

    assert "실행" in summary
    assert "워크로드" in summary
    assert "TPS P50 (100)" in summary
    assert "sleep-baseline" in summary
    assert "sleep-pyrallel-process" in summary


def test_render_results_summary_includes_performance_improvement_analysis(
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "benchmark-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "performance_improvements": [
                    {
                        "comparison": "adaptive_on_vs_off",
                        "workload": "sleep",
                        "ordering": "key_hash",
                        "run_type": "async",
                        "candidate_run_name": (
                            "sleep-key_hash-pyrallel-async-adaptive-on"
                        ),
                        "reference_run_name": (
                            "sleep-key_hash-pyrallel-async-adaptive-off"
                        ),
                        "candidate_throughput_tps": 125.0,
                        "reference_throughput_tps": 100.0,
                        "throughput_tps_delta": 25.0,
                        "throughput_tps_delta_pct": 25.0,
                        "improvement_ratio": 1.25,
                    }
                ],
                "results": [
                    {
                        "run_name": "sleep-key_hash-pyrallel-async-adaptive-off",
                        "run_type": "async",
                        "workload": "sleep",
                        "ordering": "key_hash",
                        "topic": "demo-sleep-key_hash-async-adaptive-off",
                        "messages_processed": 100,
                        "total_time_sec": 1.0,
                        "throughput_tps": 100.0,
                        "avg_processing_ms": 1.0,
                        "p99_processing_ms": 2.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    summary = render_results_summary(results_path)

    assert "성능 개선" in summary
    assert "adaptive_on_vs_off" in summary
    assert "sleep/key_hash/async" in summary
    assert "+25.00 TPS" in summary
    assert "+25.00%" in summary


def test_results_modal_renders_ordering_grouped_winner_sections(tmp_path: Path) -> None:
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
                        "ordering": "key_hash",
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
                        "ordering": "key_hash",
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
                        "ordering": "key_hash",
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
                        "ordering": "partition",
                        "topic": "demo-io-baseline",
                        "messages_processed": 100,
                        "total_time_sec": 3.0,
                        "throughput_tps": 30.0,
                        "avg_processing_ms": 1.4,
                        "p99_processing_ms": 3.4,
                    },
                    {
                        "run_name": "io-pyrallel-async",
                        "run_type": "async",
                        "workload": "io",
                        "ordering": "partition",
                        "topic": "demo-io-async",
                        "messages_processed": 100,
                        "total_time_sec": 1.7,
                        "throughput_tps": 55.0,
                        "avg_processing_ms": 1.1,
                        "p99_processing_ms": 2.1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    modal = ResultsSummaryModalScreen(
        summary_text="unused", output_path=str(results_path)
    )

    key_hash_text = modal._winner_section_text("key_hash")
    partition_text = modal._winner_section_text("partition")

    assert "정렬: key_hash" in key_hash_text
    assert "sleep" in key_hash_text
    assert "async" in key_hash_text
    assert "평균 1.000ms" in key_hash_text
    assert "cpu" in key_hash_text
    assert "process" in key_hash_text
    assert "정렬: partition" in partition_text
    assert "io" in partition_text
    assert "async" in partition_text


@pytest.mark.asyncio
async def test_results_modal_compresses_ordering_summary_with_tabs(
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "benchmark-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-pyrallel-async",
                        "run_type": "async",
                        "workload": "sleep",
                        "ordering": "key_hash",
                        "topic": "demo-sleep-async",
                        "messages_processed": 100,
                        "total_time_sec": 1.5,
                        "throughput_tps": 66.0,
                        "avg_processing_ms": 1.0,
                        "p99_processing_ms": 2.0,
                    },
                    {
                        "run_name": "io-pyrallel-process",
                        "run_type": "process",
                        "workload": "io",
                        "ordering": "partition",
                        "topic": "demo-io-process",
                        "messages_processed": 100,
                        "total_time_sec": 1.7,
                        "throughput_tps": 55.0,
                        "avg_processing_ms": 1.1,
                        "p99_processing_ms": 2.1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            ResultsSummaryModalScreen(
                summary_text="unused", output_path=str(results_path)
            )
        )
        await pilot.pause()

        ordering_tabs = app.screen.query_one("#results-ordering-tabs", Tabs)
        ordering_summary = app.screen.query_one("#results-ordering-summary", Static)
        settings_button = app.screen.query_one("#results-modal-settings", Button)
        close_button = app.screen.query_one("#results-modal-close", Button)

        assert ordering_tabs.active == "ordering-tab-key_hash"
        assert ordering_tabs.region.height == 2
        assert "정렬: key_hash" in str(ordering_summary.content)
        assert "평균 1.000ms" in str(ordering_summary.content)
        assert "P99 2.000ms" in str(ordering_summary.content)
        assert "partition" not in str(ordering_summary.content)
        assert settings_button.region.width == close_button.region.width
        assert settings_button.region.y == close_button.region.y
        assert (
            ordering_summary.region.y
            <= ordering_tabs.region.y + ordering_tabs.region.height + 1
        )

        ordering_tabs.active = "ordering-tab-partition"
        await pilot.pause()

    assert "정렬: partition" in str(ordering_summary.content)
    assert "io" in str(ordering_summary.content)
    assert "process" in str(ordering_summary.content)


def test_results_modal_hides_unselected_workload_winner_cards(tmp_path: Path) -> None:
    results_path = tmp_path / "benchmark-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {},
                "results": [
                    {
                        "run_name": "sleep-pyrallel-async",
                        "run_type": "async",
                        "workload": "sleep",
                        "ordering": "key_hash",
                        "topic": "demo-sleep-async",
                        "messages_processed": 100,
                        "total_time_sec": 1.5,
                        "throughput_tps": 66.0,
                        "avg_processing_ms": 1.0,
                        "p99_processing_ms": 2.0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    modal = ResultsSummaryModalScreen(
        summary_text="unused", output_path=str(results_path)
    )

    assert modal._visible_orderings == ("key_hash",)
    assert "sleep" in modal._winner_section_text("key_hash")
    assert "cpu" not in modal._winner_section_text("key_hash")
    assert "io" not in modal._winner_section_text("key_hash")


def test_results_modal_infers_default_ordering_from_legacy_results_json(
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "legacy-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {"workload": "all", "json_output": str(results_path)},
                "results": [
                    {
                        "run_name": "baseline",
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
                ],
            }
        ),
        encoding="utf-8",
    )

    modal = ResultsSummaryModalScreen(
        summary_text="unused", output_path=str(results_path)
    )

    assert modal._visible_orderings == ("key_hash",)
    assert "정렬: key_hash" in modal._winner_section_text("key_hash")
    assert "sleep" in modal._winner_section_text("key_hash")
    assert "async" in modal._winner_section_text("key_hash")


def test_results_modal_infers_process_label_from_legacy_pyrallel_run_name(
    tmp_path: Path,
) -> None:
    results_path = tmp_path / "legacy-process-results.json"
    results_path.write_text(
        json.dumps(
            {
                "options": {"workload": "all", "json_output": str(results_path)},
                "results": [
                    {
                        "run_name": "cpu-key_hash-pyrallel-process",
                        "run_type": "pyrallel",
                        "workload": "cpu",
                        "ordering": "key_hash",
                        "topic": "demo-cpu-process",
                        "messages_processed": 100,
                        "total_time_sec": 1.2,
                        "throughput_tps": 80.0,
                        "avg_processing_ms": 0.8,
                        "p99_processing_ms": 1.2,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    modal = ResultsSummaryModalScreen(
        summary_text="unused", output_path=str(results_path)
    )

    assert "cpu" in modal._winner_section_text("key_hash")
    assert "process" in modal._winner_section_text("key_hash")
    assert "pyrallel" not in modal._winner_section_text("key_hash")


def test_results_modal_defaults_to_centered_layout() -> None:
    assert "align: center middle" in ResultsSummaryModalScreen.DEFAULT_CSS


def test_results_modal_cards_use_centered_content_alignment() -> None:
    from benchmarks.tui.app import BenchmarkTuiApp

    assert ".results-order-section" in BenchmarkTuiApp.CSS


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
                        "ordering": "key_hash",
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
        overview_row = summary_screen.query_one("#results-overview-text", Static)

        ordering_summary = summary_screen.query_one("#results-ordering-summary", Static)
        output_path = summary_screen.query_one("#results-output-path", Static)
        detail_title = summary_screen.query_one("#results-detail-title", Static)
        assert summary_table.get_row_at(0) == [
            "sleep-baseline",
            "baseline",
            "sleep",
            "100",
            "40.00",
            "—",
            "—",
            "—",
            "1.250",
            "2.500",
        ]
        assert summary_table.get_row_at(1) == [
            "sleep-pyrallel-async",
            "pyrallel",
            "sleep",
            "100",
            "66.00",
            "—",
            "—",
            "—",
            "1.000",
            "2.000",
        ]
        assert summary_table.size.height >= 6
    assert "정렬: key_hash" in str(ordering_summary.content)
    assert "sleep" in str(ordering_summary.content)
    assert "async" in str(ordering_summary.content)
    assert "실행 2건" in str(overview_row.content)
    assert str(results_path) in str(output_path.content)
    assert "상세 결과" in str(detail_title.content)


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

    assert "실패" in str(status.content)


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


@pytest.mark.asyncio
async def test_results_modal_wraps_content_in_vertical_scroll() -> None:
    app = BenchmarkTuiApp()

    async with app.run_test() as pilot:
        app.push_screen(
            ResultsSummaryModalScreen(summary_text="summary", output_path=None)
        )
        await pilot.pause()
        scroll = app.screen.query_one("#results-modal-scroll", VerticalScroll)

    assert scroll.id == "results-modal-scroll"
