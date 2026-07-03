"""Basic smoke tests for the scaffold."""

from lot_textual_ui import __version__
from lot_textual_ui.app import LotTextualApp


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_app_constructs() -> None:
    app = LotTextualApp()
    assert app.TITLE == "LoT"
