"""Minimal Textual application scaffold for the LoT TUI.

This is a placeholder app; later work items build the real UI on top of it.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.widgets import Footer, Header, Label


class LotTextualApp(App[None]):
    """The LoT Textual application."""

    TITLE = "LoT"
    SUB_TITLE = "Lists of Things"

    def compose(self) -> ComposeResult:
        yield Header()
        yield Label("Loading vault…", id="status")
        yield Footer()


def main() -> None:
    """Console-script entry point: run the Textual app."""
    LotTextualApp().run()


if __name__ == "__main__":
    main()
