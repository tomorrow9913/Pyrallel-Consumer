from __future__ import annotations

from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container
from textual.screen import ModalScreen
from textual.widgets import Button, DirectoryTree, Static


def _normalize_start_path(start_path: Path | str) -> Path:
    path = Path(start_path).expanduser()
    if path.exists():
        if path.is_file():
            return path.parent
        return path
    if path.suffix:
        return path.parent if str(path.parent) else Path.cwd()
    return path if str(path) else Path.cwd()


class DirectoryPickerScreen(ModalScreen[str | None]):
    BINDINGS = [("escape", "cancel", "Cancel")]
    DEFAULT_CSS = """
    DirectoryPickerScreen {
        align: center middle;
    }

    #directory-picker-dialog {
        width: 80%;
        height: 80%;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }

    #directory-picker-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #directory-picker-tree {
        height: 1fr;
        margin-bottom: 1;
    }

    #directory-picker-selection {
        color: $text-muted;
        margin-bottom: 1;
    }

    #directory-picker-actions {
        layout: horizontal;
        height: auto;
    }

    #directory-picker-actions Button {
        margin-right: 1;
    }
    """

    def __init__(self, start_path: Path | str) -> None:
        super().__init__()
        self._start_path = _normalize_start_path(start_path)
        self._selected_path = self._start_path

    def compose(self) -> ComposeResult:
        with Container(id="directory-picker-dialog"):
            yield Static("Select output directory", id="directory-picker-title")
            yield DirectoryTree(self._start_path, id="directory-picker-tree")
            yield Static(str(self._selected_path), id="directory-picker-selection")
            with Container(id="directory-picker-actions"):
                yield Button(
                    "Confirm",
                    id="directory-picker-confirm",
                    variant="primary",
                )
                yield Button("Cancel", id="directory-picker-cancel")

    def on_directory_tree_directory_selected(
        self, event: DirectoryTree.DirectorySelected
    ) -> None:
        self._selected_path = event.path
        self.query_one("#directory-picker-selection", Static).update(
            str(self._selected_path)
        )

    def on_directory_tree_file_selected(
        self, event: DirectoryTree.FileSelected
    ) -> None:
        self._selected_path = event.path.parent
        self.query_one("#directory-picker-selection", Static).update(
            str(self._selected_path)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "directory-picker-confirm":
            self.dismiss(str(self._selected_path))
        elif event.button.id == "directory-picker-cancel":
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)
