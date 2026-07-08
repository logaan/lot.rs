"""Modal screens for the batch operations over the marked set.

Multi-select (see the app's ``toggle_mark``/``clear_marks`` actions) builds a
set of marked Things; the batch actions collect their remaining input through
the screens in this module:

* :class:`ThingPickerScreen` — the batch-**move** destination: one Thing out of
  the whole vault tree, or the vault's top level.
* :class:`ConfirmScreen` — a generic confirm/cancel dialog; batch-**archive**
  uses it to show how many Things are about to be irreversibly removed from the
  working tree.

(The batch-**update** form lives in :mod:`lot_textual_ui.forms` as
:class:`~lot_textual_ui.forms.BatchUpdateScreen`, next to the single-Thing form
it reuses.)

Like the other modals, these screens only *collect* a choice — they never spawn
``lot`` or touch the vault. All the execution (the per-item ``lot thing move``
/ ``archive`` / ``update`` calls, progress, error collection) lives on the app.
"""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, OptionList
from textual.widgets.option_list import Option

from .mnemonics import assign_mnemonic
from .models import Thing

# The dismiss value ThingPickerScreen uses for "the vault's top level" (the
# ``lot thing move --root`` destination). Deliberately not a valid ``lot:`` id
# so it can never collide with a real Thing.
TOP_LEVEL = "__top-level__"


def flatten_things(
    roots: list[Thing], exclude: frozenset[str] | set[str] = frozenset()
) -> list[tuple[Thing, int]]:
    """Flatten a Thing tree into ``(thing, depth)`` rows, in tree order.

    The pure helper behind the picker's option list. ``exclude`` drops a Thing
    from the rows (its descendants still appear, shifted one level up is *not*
    done — they keep their real depth so the tree shape stays readable). The
    batch-move flow excludes the marked Things themselves: a Thing can never be
    its own destination, and offering it would only manufacture a guaranteed
    cycle error. Descendants of marked Things are *not* excluded — with several
    marked Things a destination may be a valid target for some and a cycle for
    others, and the per-item error reporting handles the latter.
    """
    rows: list[tuple[Thing, int]] = []

    def walk(things: list[Thing], depth: int) -> None:
        for thing in things:
            if thing.id not in exclude:
                rows.append((thing, depth))
            walk(thing.children, depth + 1)

    walk(roots, 0)
    return rows


class ThingPickerScreen(ModalScreen[str | None]):
    """Modal single-select list of destination Things for a batch move.

    Args:
        roots: The vault's top-level Things (the app's freshest index); the
            whole tree is offered, indented by depth.
        exclude: Thing ids to leave out of the list — the marked Things
            themselves, which cannot be their own destination.
        title: The dialog title; defaults to naming the batch-move purpose.

    On choose the screen ``dismiss``\\es with the selected Thing's ``lot:`` id,
    or :data:`TOP_LEVEL` for the always-present "Top level (vault root)" entry;
    on cancel (``escape``) it dismisses with ``None``. The caller (the app)
    runs the actual moves — the screen itself is inert.
    """

    DEFAULT_CSS = """
    ThingPickerScreen {
        align: center middle;
    }

    ThingPickerScreen > #thing-picker-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    ThingPickerScreen #thing-picker-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ThingPickerScreen #thing-picker-list {
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
        roots: list[Thing],
        exclude: set[str] | frozenset[str] = frozenset(),
        title: str = "Move marked Things to…",
    ) -> None:
        super().__init__()
        self._rows = flatten_things(roots, exclude=exclude)
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="thing-picker-dialog"):
            yield Label(self._title, id="thing-picker-title")
            yield OptionList(id="thing-picker-list")

    def on_mount(self) -> None:
        option_list = self.query_one("#thing-picker-list", OptionList)
        # The top level first: always a valid destination (`--root`).
        option_list.add_option(Option("Top level (vault root)"))
        for thing, depth in self._rows:
            option_list.add_option(Option(f"{'  ' * depth}{thing.name}"))
        option_list.focus()

    @on(OptionList.OptionSelected, "#thing-picker-list")
    def _chosen(self, event: OptionList.OptionSelected) -> None:
        """Dismiss with the chosen destination (index 0 is the top level)."""
        if event.option_index == 0:
            self.dismiss(TOP_LEVEL)
            return
        thing, _depth = self._rows[event.option_index - 1]
        self.dismiss(thing.id)

    def action_cancel(self) -> None:
        """Close the picker without moving anything."""
        self.dismiss(None)


class ConfirmScreen(ModalScreen[bool]):
    """A generic modal confirmation dialog (dismisses ``True`` on confirm).

    Args:
        message: The question to put to the user (e.g. the batch-archive count).
        title: The dialog title.
        confirm_label: The confirming button's label.

    ``enter``/the confirm button dismiss ``True``; ``escape``/Cancel dismiss
    ``False``. The initial focus is on Cancel so a stray Enter cannot confirm a
    destructive batch unseen.
    """

    DEFAULT_CSS = """
    ConfirmScreen {
        align: center middle;
    }

    ConfirmScreen > #confirm-dialog {
        width: 60%;
        max-width: 80;
        height: auto;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    ConfirmScreen #confirm-title {
        text-style: bold;
        margin-bottom: 1;
    }

    ConfirmScreen #confirm-message {
        height: auto;
    }

    ConfirmScreen #confirm-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    ConfirmScreen #confirm-buttons Button {
        margin-left: 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
    ]

    def __init__(
        self,
        message: str,
        title: str = "Are you sure?",
        confirm_label: str = "Confirm",
    ) -> None:
        super().__init__()
        self._message = message
        self._title = title
        self._confirm_label = confirm_label
        # Cancel picks its mnemonic *first* on every modal screen (see
        # :func:`~lot_textual_ui.mnemonics.assign_mnemonic`): that pins Cancel
        # to the same chord (``ctrl+n``) as the other dialogs and guarantees
        # the *destructive* confirm can never land on it. The confirm button
        # then takes whatever is left — and ``confirm_label`` is a runtime value
        # (e.g. "Archive"), so this has to run live rather than being baked in.
        used_letters: set[str] = set()
        self._cancel_key, self._cancel_markup = assign_mnemonic("Cancel", used_letters)
        self._confirm_key, self._confirm_markup = assign_mnemonic(
            confirm_label, used_letters
        )
        self._bindings.bind(self._cancel_key, "cancel", show=False, priority=True)
        self._bindings.bind(self._confirm_key, "confirm", show=False, priority=True)

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(self._title, id="confirm-title")
            yield Label(self._message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button(
                    self._cancel_markup, variant="default", id="confirm-cancel"
                )
                yield Button(
                    self._confirm_markup, variant="error", id="confirm-confirm"
                )

    def on_mount(self) -> None:
        # Cancel holds the initial focus: confirming a destructive batch takes
        # a deliberate move (tab/arrow to the confirm button, or a click).
        self.query_one("#confirm-cancel", Button).focus()

    @on(Button.Pressed, "#confirm-cancel")
    def _cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#confirm-confirm")
    def _confirm_button(self) -> None:
        self.action_confirm()

    def action_cancel(self) -> None:
        """Close the dialog without confirming."""
        self.dismiss(False)

    def action_confirm(self) -> None:
        """Close the dialog, confirming the action."""
        self.dismiss(True)
