from __future__ import annotations

from textual.app import App

from benchmarks.tui.controller import BenchmarkProcessController
from benchmarks.tui.results_modal import ResultsSummaryModalScreen
from benchmarks.tui.screens.options import OptionsScreen, _ValidationResult
from benchmarks.tui.screens.run import RunScreen, _format_elapsed

__all__ = [
    "BenchmarkTuiApp",
    "BenchmarkProcessController",
    "OptionsScreen",
    "ResultsSummaryModalScreen",
    "RunScreen",
    "_ValidationResult",
    "_format_elapsed",
]


class BenchmarkTuiApp(App[None]):
    """Represent benchmark tui app data used by benchmark TUI."""

    TITLE = "Pyrallel Benchmark TUI"
    CSS = """
    #options-layout, #run-layout {
        layout: vertical;
        height: 1fr;
    }

    #options-screen, #run-screen {
        padding: 1 2;
        height: 1fr;
    }

    #options-footer {
        height: auto;
        border-top: solid $surface-lighten-1;
        padding: 1 2;
        background: $surface;
    }

    #argv-preview, #form-error-summary {
        border: round $accent;
        padding: 1;
        margin-bottom: 1;
    }

    #argv-preview {
        padding: 0 1;
        max-height: 25vh;
        overflow-y: auto;
        text-wrap: wrap;
        text-overflow: clip;
    }

    .workload-option-group {
        height: auto;
    }

    .workload-option-group-title {
        margin-top: 0;
        margin-bottom: 0;
        color: $text-muted;
    }

    .workload-option-row {
        layout: horizontal;
        height: auto;
        margin-bottom: 0;
    }

    .workload-option-row .field-label {
        width: 28;
        margin-top: 0;
        margin-bottom: 0;
    }

    .workload-option-row Input, .workload-option-row Select {
        width: 1fr;
        margin-bottom: 0;
    }

    .workload-option-row Switch {
        margin-bottom: 0;
    }

    #form-error-summary {
        color: $warning;
        display: block;
    }

    #copy-command-status {
        display: none;
        margin-bottom: 0;
    }

    #copy-command-status.has-status {
        display: block;
        margin-bottom: 1;
    }

    Input, Select, Switch, Static, Log, Collapsible, ProgressBar, DataTable, LoadingIndicator {
        margin-bottom: 1;
    }

    .screen-title {
        text-style: bold;
        color: $accent;
    }

    .field-label {
        margin-top: 1;
    }

    .option-block {
        height: auto;
    }

    .option-section {
        height: auto;
        border: round $surface-lighten-1;
        padding: 0 1 1 1;
        margin-bottom: 1;
    }

    .option-section-title {
        text-style: bold;
        color: $accent;
        margin-top: 1;
    }

    .option-section-description {
        color: $text-muted;
        margin-bottom: 1;
    }

    .option-help {
        color: $text-muted;
        margin-top: -1;
        margin-bottom: 1;
    }

    .field-error {
        color: $error;
        display: none;
        margin-bottom: 0;
        min-height: 0;
    }

    .field-error.has-error {
        display: block;
        margin-top: -1;
        margin-bottom: 1;
        min-height: 1;
    }

    .input-with-browse {
        layout: horizontal;
        height: auto;
    }

    .input-with-browse Input {
        width: 1fr;
    }

    .browse-button {
        width: 12;
        margin-left: 1;
    }

    #run-spotlight-card {
        border: round $accent;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #run-spotlight {
        text-style: bold;
        margin-bottom: 0;
    }

    #run-chip-row {
        height: auto;
        margin-bottom: 0;
    }

    .status-chip {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: auto;
        text-style: bold;
        margin-bottom: 0;
        background: transparent;
    }

    .status-chip.is-waiting {
        color: $text-muted;
        border: round $surface-lighten-2;
    }

    .status-chip.is-running {
        color: $success;
        border: round $success;
    }

    .status-chip.is-done {
        color: $success;
        border: round $success;
    }

    .status-chip.is-failed {
        color: $error;
        border: round $error;
    }

    #run-phase-meta, #run-overall-meta {
        height: auto;
        margin-bottom: 0;
    }

    .status-badge {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-right: 1;
        width: auto;
        margin-bottom: 0;
    }

    #run-terminal-reason, #run-output-path, #results-output-path {
        color: $text-muted;
    }

    #phase-progress, #run-progress {
        width: 100%;
        height: 1;
        margin-bottom: 1;
    }

    #run-terminal-reason {
        display: none;
    }

    #run-terminal-reason.is-failed {
        color: $error;
        text-style: bold;
    }

    #run-terminal-reason.is-cancelled {
        color: $warning;
        text-style: bold;
    }

    #run-terminal-reason.is-complete {
        color: $success;
    }

    #run-log-header {
        height: auto;
        margin-bottom: 0;
        align-vertical: middle;
    }

    #run-log-title {
        width: 1fr;
        text-style: bold;
    }

    #run-summary {
        height: auto;
    }

    #run-log {
        height: 16;
    }

    #run-actions, #options-actions {
        height: auto;
        width: 100%;
        layout: horizontal;
    }

    #run-actions {
        border-top: solid $surface-lighten-1;
        padding: 1 2;
        background: $surface;
        dock: bottom;
    }

    #run-actions Button, #options-actions Button, #results-modal-actions Button {
        margin-right: 1;
    }

    #results-modal {
        width: 80%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #results-modal-scroll {
        height: 1fr;
    }

    #results-modal-header {
        height: auto;
        margin-bottom: 1;
    }

    #results-modal-title, #results-detail-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #results-modal-subtitle {
        color: $text-muted;
        margin-bottom: 1;
    }

    .results-order-section {
        border: round $surface-lighten-1;
        padding: 0 1;
        margin-bottom: 1;
        height: auto;
    }

    #results-table {
        border: round $accent;
        height: 12;
        margin-bottom: 1;
    }

    #results-modal-actions {
        height: auto;
        align-horizontal: right;
        layout: horizontal;
    }

    #results-modal-actions Button {
        width: 24;
    }

    #results-ordering-tabs {
        margin-bottom: 0;
    }

    #results-ordering-tabs Tab {
        padding: 0 2;
    }

    #results-ordering-summary {
        margin-top: 0;
    }
    """

    def on_mount(self) -> None:
        """Handle on mount within benchmark TUI."""
        self.push_screen(OptionsScreen())
