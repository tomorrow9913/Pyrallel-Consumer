from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Container, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Static, Tab, Tabs

from benchmarks.tui.results_report import (
    load_results_table_data,
    summarize_results_overview,
    summarize_workload_winners,
)

_WORKLOAD_NAMES = ("sleep", "cpu", "io")


class ResultsSummaryModalScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "close", "Close"), ("s", "settings", "Settings")]
    DEFAULT_CSS = """
    ResultsSummaryModalScreen {
        align: center middle;
    }
    """

    def __init__(self, summary_text: str, output_path: str | None = None) -> None:
        super().__init__()
        self._summary_text = summary_text
        self._output_path = output_path
        self._overview = (
            summarize_results_overview(output_path) if output_path is not None else None
        )
        self._winners = (
            summarize_workload_winners(output_path) if output_path is not None else {}
        )
        self._visible_orderings = tuple(
            ordering
            for ordering in ("key_hash", "partition", "unordered")
            if ordering in self._winners
        )
        self._selected_ordering = (
            self._visible_orderings[0] if self._visible_orderings else None
        )
        self._table_data = (
            load_results_table_data(output_path) if output_path is not None else None
        )

    def compose(self) -> ComposeResult:
        with Container(id="results-modal"):
            with VerticalScroll(id="results-modal-scroll"):
                with Container(id="results-modal-header"):
                    yield Static("벤치마크 결과", id="results-modal-title")
                    yield Static(
                        "정렬 방식별 요약을 확인하고 다음 실험으로 돌아가세요.",
                        id="results-modal-subtitle",
                    )
                yield Static(self._overview_text(), id="results-overview-text")
                yield Static(self._output_path_text(), id="results-output-path")
                if self._selected_ordering is not None:
                    yield Tabs(
                        *[
                            Tab(ordering, id="ordering-tab-%s" % ordering)
                            for ordering in self._visible_orderings
                        ],
                        active="ordering-tab-%s" % self._selected_ordering,
                        id="results-ordering-tabs",
                    )
                yield Static(
                    "",
                    id="results-ordering-summary",
                    classes="results-order-section",
                )
                yield Static("상세 결과", id="results-detail-title")
                yield DataTable(id="results-table")
            with Container(id="results-modal-actions"):
                yield Button("Back to settings", id="results-modal-settings")
                yield Button("Close", id="results-modal-close", variant="primary")

    def on_mount(self) -> None:
        table = self.query_one("#results-table", DataTable)
        table.cursor_type = "row"
        if self._table_data is None:
            table.add_column("요약")
            table.add_row(self._summary_text)
        else:
            for header in self._table_data.headers:
                table.add_column(header)
            for row in self._table_data.rows:
                table.add_row(*row)
        self._refresh_ordering_summary()

    def on_tabs_tab_activated(self, event: Tabs.TabActivated) -> None:
        if event.tabs.id == "results-ordering-tabs":
            tab_id = event.tab.id
            if tab_id is None:
                return
            self._selected_ordering = tab_id.removeprefix("ordering-tab-")
            self._refresh_ordering_summary()

    def _refresh_ordering_summary(self) -> None:
        summary = self.query_one("#results-ordering-summary", Static)
        if self._selected_ordering is None:
            summary.update(self._summary_text)
            return
        summary.update(self._winner_section_text(self._selected_ordering))

    def _winner_section_text(self, ordering: str) -> str:
        winners = self._winners.get(ordering, {})
        lines = ["정렬: %s" % ordering]
        for workload in _WORKLOAD_NAMES:
            winner = winners.get(workload)
            if winner is None:
                continue
            lines.append(
                "%s · %s · %s TPS · 평균 %sms · P99 %sms"
                % (
                    workload,
                    winner.run_type,
                    winner.throughput_tps,
                    winner.avg_processing_ms,
                    winner.p99_processing_ms,
                )
            )
        return "\n".join(lines)

    def _output_path_text(self) -> str:
        if self._overview is None:
            return "결과 파일을 찾을 수 없습니다."
        return "결과 파일: %s" % self._overview.output_path

    def _overview_text(self) -> str:
        if self._overview is None:
            return "요약을 만들 수 없습니다."
        return "실행 %d건 | workload: %s | 최고 TPS: %s (%s TPS)" % (
            self._overview.total_runs,
            ", ".join(self._overview.workloads) or "unknown",
            self._overview.best_run_name,
            self._overview.best_tps,
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "results-modal-settings":
            self.dismiss("settings")
        elif event.button.id == "results-modal-close":
            self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)

    def action_settings(self) -> None:
        self.dismiss("settings")
