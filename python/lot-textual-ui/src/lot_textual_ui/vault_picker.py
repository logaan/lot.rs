"""The vault picker: a modal list of configured vaults to switch between.

The TUI can be pointed at any of the vaults declared in config (``[[tui.vaults]]``,
surfaced by ``lot config get`` as :attr:`EffectiveConfig.vaults
<lot_textual_ui.models.EffectiveConfig.vaults>`). The "Switch vault" palette
command opens this modal; choosing an entry dismisses with that vault's **path**,
which the app hands to
:meth:`~lot_textual_ui.app.LotTextualApp.action_switch_vault`.

Like the form screens in :mod:`lot_textual_ui.forms`, this screen only *collects*
a choice — it never spawns ``lot`` or touches the vault itself. All the switching
work (retargeting the shared :class:`~lot_textual_ui.lot_cli.LotCli`, reloading,
restarting the watcher) lives on the app.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList
from textual.widgets.option_list import Option

from .models import VaultEntry


class VaultPickerScreen(ModalScreen[str | None]):
    """Modal single-select list of configured vaults.

    Args:
        vaults: The configured vaults to offer, in config order. Each is shown by
            its :attr:`~lot_textual_ui.models.VaultEntry.name` when set, else its
            :attr:`~lot_textual_ui.models.VaultEntry.path`.
        active_path: The currently-active vault path (``config.vault_path``); the
            matching entry is marked so it is obvious which vault is in use. May
            be empty when unknown, in which case nothing is marked.

    On choose the screen ``dismiss``\\es with the selected vault's **path** (a
    ``str``); on cancel (``escape``) it dismisses with ``None``. The caller (the
    app) does the actual switch — the screen itself is inert.
    """

    DEFAULT_CSS = """
    VaultPickerScreen {
        align: center middle;
    }

    VaultPickerScreen > #vault-picker-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    VaultPickerScreen #vault-picker-title {
        text-style: bold;
        margin-bottom: 1;
    }

    VaultPickerScreen #vault-picker-list {
        height: auto;
        max-height: 20;
    }
    """

    # Screen-local bindings only (app-level keys stay in keys.py). ``escape``
    # cancels; the OptionList handles up/down + ``enter`` to choose itself.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(
        self,
        vaults: list[VaultEntry],
        active_path: str = "",
    ) -> None:
        super().__init__()
        self._vaults = list(vaults)
        self._active_path = active_path

    def compose(self) -> ComposeResult:
        with Vertical(id="vault-picker-dialog"):
            yield Label("Switch vault", id="vault-picker-title")
            yield OptionList(id="vault-picker-list")

    def on_mount(self) -> None:
        option_list = self.query_one("#vault-picker-list", OptionList)
        for entry in self._vaults:
            label = entry.name or entry.path
            marker = " (active)" if entry.path == self._active_path else ""
            option_list.add_option(Option(f"{label}{marker}"))
        option_list.focus()

    @on(OptionList.OptionSelected, "#vault-picker-list")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen vault's path (indexed back to its entry)."""
        entry = self._vaults[event.option_index]
        self.dismiss(entry.path)

    def action_cancel(self) -> None:
        """Close the picker without switching."""
        self.dismiss(None)
