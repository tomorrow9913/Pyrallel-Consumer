# -*- coding: utf-8 -*-
# File: tests/unit/benchmarks/test_tui_run_screen_dashboard.py
# Role: Verifies run screen dashboard widgets, progress, status formatting, and active-run controls.
# Extend here for focused benchmark regression coverage in this area.

from tests.unit.benchmarks._tui_app_support import (
    Any,
    BenchmarkProgressSnapshot,
    BenchmarkTuiApp,
    BenchmarkTuiState,
    Button,
    DataTable,
    ProgressBar,
    RunScreen,
    Static,
    Text,
    _ancestor_ids,
    _assert_text_cell,
    _FakeController,
    _results_modal_screen,
    _run_screen,
    pytest,
    signal,
)


@pytest.mark.asyncio
async def test_run_screen_back_stays_on_active_benchmark(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for run screen back stays on active benchmark.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen back stays on active benchmark.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState()))
        await pilot.pause()

        await _run_screen(app).action_back()
        await pilot.pause()

        # Then: The expected run screen back stays on active benchmark behavior is asserted.
        assert _FakeController.instances[0].cancel_called is False
        assert app.screen.__class__.__name__ == "RunScreen"


@pytest.mark.asyncio
async def test_run_screen_preserves_cancelled_status(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for run screen preserves cancelled status.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen preserves cancelled status.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState()))
        await pilot.pause()

        run_screen = _run_screen(app)
        await run_screen.action_cancel()
        await pilot.pause()

        status = run_screen.query_one("#run-status", Static)
        # Then: The expected run screen preserves cancelled status behavior is asserted.
        assert "취소" in str(status.content)


@pytest.mark.asyncio
async def test_run_screen_mounts_dashboard_widgets(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for run screen mounts dashboard widgets.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen mounts dashboard widgets.
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
        # Then: The expected run screen mounts dashboard widgets behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen updates progress bar and summary table.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen updates progress bar and summary table.
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

    # Then: The expected run screen updates progress bar and summary table behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen marks results below workload baseline average yellow.
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
    # When: The benchmark TUI run dashboard path is exercised for run screen marks results below workload baseline average yellow.
    _assert_text_cell(
        summary_table.get_cell("sleep-key_hash", "process"),
        "150.00 TPS",
        "bold bright_green",
    )
    # Then: The expected run screen marks results below workload baseline average yellow behavior is asserted.
    _assert_text_cell(
        summary_table.get_cell("sleep-partition", "async"),
        "151.00 TPS",
        "bold bright_green",
    )


@pytest.mark.asyncio
async def test_run_screen_keeps_baseline_comparison_for_comma_formatted_tps(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run screen keeps baseline comparison for comma formatted tps.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen keeps baseline comparison for comma formatted tps.
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
        # Then: The expected run screen keeps baseline comparison for comma formatted tps behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen formats ordering status for readability.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen formats ordering status for readability.
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

    # Then: The expected run screen formats ordering status for readability behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen spotlight uses single progress semantics and selected rows only.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen spotlight uses single progress semantics and selected rows only.
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

    # Then: The expected run screen spotlight uses single progress semantics and selected rows only behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen uses lifecycle progress value.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen uses lifecycle progress value.
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

    # Then: The expected run screen uses lifecycle progress value behavior is asserted.
    assert progress_bar.progress == 0


@pytest.mark.asyncio
async def test_run_screen_formats_status_and_tps_cells_for_readability(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run screen formats status and tps cells for readability.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen formats status and tps cells for readability.
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

    # Then: The expected run screen formats status and tps cells for readability behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen back does not leave screen while running.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen back does not leave screen while running.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = _run_screen(app)
        await run_screen.action_back()
        await pilot.pause()

        # Then: The expected run screen back does not leave screen while running behavior is asserted.
        assert app.screen is run_screen
        assert _FakeController.instances[0].cancel_called is False


@pytest.mark.asyncio
async def test_run_screen_marks_failed_cell_in_soft_red(monkeypatch) -> None:
    # Given: Inputs and test doubles are prepared for run screen marks failed cell in soft red.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen marks failed cell in soft red.
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

    # Then: The expected run screen marks failed cell in soft red behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen animates running summary cell without loading widget.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen animates running summary cell without loading widget.
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

    # Then: The expected run screen animates running summary cell without loading widget behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen spinner refresh updates only active summary cell.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen spinner refresh updates only active summary cell.
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
        if run_screen._spinner_timer is not None:
            run_screen._spinner_timer.stop()
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

    # Then: The expected run screen spinner refresh updates only active summary cell behavior is asserted.
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
    # Given: Inputs and test doubles are prepared for run screen surfaces last error line in failure status.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen surfaces last error line in failure status.
    async with app.run_test() as pilot:
        app.push_screen(RunScreen(BenchmarkTuiState(workloads=("sleep",))))
        await pilot.pause()

        run_screen = _run_screen(app)
        run_screen._append_log("RuntimeError: boom", is_error=True)
        run_screen._on_complete(1)
        await pilot.pause()

        reason = run_screen.query_one("#run-terminal-reason", Static)

    # Then: The expected run screen surfaces last error line in failure status behavior is asserted.
    assert str(reason.content) == "종료 사유: RuntimeError: boom"


@pytest.mark.asyncio
async def test_run_screen_shows_kill_button_for_metrics_port_pid_error(
    monkeypatch,
) -> None:
    # Given: Inputs and test doubles are prepared for run screen shows kill button for metrics port pid error.
    monkeypatch.setattr(
        "benchmarks.tui.app.BenchmarkProcessController", _FakeController
    )
    _FakeController.instances.clear()
    killed: list[tuple[int, signal.Signals]] = []

    def _fake_kill(pid: int, sig: signal.Signals) -> None:
        killed.append((pid, sig))

    monkeypatch.setattr("benchmarks.tui.screens.run.os.kill", _fake_kill)

    app = BenchmarkTuiApp()

    # When: The benchmark TUI run dashboard path is exercised for run screen shows kill button for metrics port pid error.
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
        # Then: The expected run screen shows kill button for metrics port pid error behavior is asserted.
        assert kill_button.display is True
        assert str(kill_button.label) == "Kill PID 1234?"

        await pilot.click("#kill-metrics-port-button")
        await pilot.pause()

        reason = run_screen.query_one("#run-terminal-reason", Static)

    assert killed == [(1234, signal.SIGTERM)]
    assert str(reason.content) == "종료 신호 전송: PID 1234"
