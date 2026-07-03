"""Modal input forms for mutating the vault (currently: creating a Thing).

The command palette (see :mod:`lot_textual_ui.palette`) discovers ``lot`` leaf
commands and, for those that need user input, routes to a form screen instead of
running the command blind. This module holds those screens. Right now that is
:class:`NewThingScreen`; the new-Update form is a sibling work item that will add
its own screen here.

All ``lot`` invocation stays inside :class:`~lot_textual_ui.lot_cli.LotCli`; a
form only collects fields and hands them to a typed ``LotCli`` method, then
reports the result back to the app through its :class:`~textual.screen.Screen`
``dismiss`` value.
"""

from __future__ import annotations

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

# The addressable id of the body editor. The ``$EDITOR`` escape-hatch work item
# targets this widget to swap its contents for an editor round-trip, so it is a
# named constant rather than an inline string.
BODY_TEXTAREA_ID = "new-thing-body"

_EMPTY_NAME_MESSAGE = "A name is required."


class NewThingScreen(ModalScreen[str | None]):
    """Modal form that creates a Thing via ``lot thing new``.

    A single-line :class:`~textual.widgets.Input` collects the Thing's **name**
    and a multi-line :class:`~textual.widgets.TextArea` (id
    :data:`BODY_TEXTAREA_ID`) collects the markdown **body**. Submitting
    (``ctrl+s`` or the Create button) validates the name client-side, then runs
    ``lot thing new`` through the shared :class:`~lot_textual_ui.lot_cli.LotCli`
    with the body piped on stdin. Cancelling (``escape`` or the Cancel button)
    closes the form without touching the vault.

    The screen is *reusable and parametrised* so later work items can drive it:

    Args:
        parent_id: When given, the Thing is created as a child of this Thing
            (passed through to ``lot thing new --parent <id>``). The
            create-child-Things work item invokes the form with a preset parent;
            ``None`` (the default) creates a top-level Thing.
        title: The window title shown at the top of the modal. Defaults to
            ``"New Thing"``; a caller creating a child can pass e.g.
            ``"New child Thing"``.

    On success the screen ``dismiss``\\es with the new Thing's ``lot:`` id (a
    ``str``); on cancel it dismisses with ``None``. The caller (the app) decides
    what to do with the id — the form itself never touches the selection or
    reloads the vault. The body TextArea is left addressable by
    :data:`BODY_TEXTAREA_ID` so the ``$EDITOR`` escape-hatch work item can target
    it without restructuring the form.
    """

    DEFAULT_CSS = """
    NewThingScreen {
        align: center middle;
    }

    NewThingScreen > #new-thing-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    NewThingScreen #new-thing-title {
        text-style: bold;
        margin-bottom: 1;
    }

    NewThingScreen .new-thing-field-label {
        margin-top: 1;
        color: $text-muted;
    }

    NewThingScreen #new-thing-name {
        width: 1fr;
    }

    NewThingScreen #new-thing-body {
        width: 1fr;
        height: 12;
    }

    NewThingScreen #new-thing-error {
        color: $error;
        height: auto;
        margin-top: 1;
    }

    NewThingScreen #new-thing-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    NewThingScreen #new-thing-buttons Button {
        margin-left: 2;
    }
    """

    # Screen-local bindings only (app-level keys stay in keys.py per its
    # contract). ``escape`` cancels; ``ctrl+s`` submits. The TextArea captures
    # plain ``enter`` for newlines, so submission is an explicit chord.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Create", show=True),
    ]

    def __init__(
        self,
        parent_id: str | None = None,
        title: str = "New Thing",
    ) -> None:
        super().__init__()
        self._parent_id = parent_id
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="new-thing-dialog"):
            yield Label(self._title, id="new-thing-title")
            yield Label("Name", classes="new-thing-field-label")
            yield Input(placeholder="Thing name", id="new-thing-name")
            yield Label("Body (markdown)", classes="new-thing-field-label")
            yield TextArea(id=BODY_TEXTAREA_ID)
            yield Label("", id="new-thing-error")
            with Horizontal(id="new-thing-buttons"):
                yield Button("Cancel", variant="default", id="new-thing-cancel")
                yield Button("Create", variant="primary", id="new-thing-create")

    def on_mount(self) -> None:
        # Land the cursor in the name field so typing starts there.
        self.query_one("#new-thing-name", Input).focus()

    # --- actions / events --------------------------------------------------

    @on(Button.Pressed, "#new-thing-cancel")
    def _cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#new-thing-create")
    def _create_button(self) -> None:
        self.action_submit()

    def action_cancel(self) -> None:
        """Close the form without creating anything."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Validate, create the Thing, and dismiss with its id.

        An empty (or whitespace-only) name is rejected in-form with a friendly
        message and no CLI call. Otherwise the create runs in a worker so the
        ``lot`` subprocess never blocks the event loop; a CLI failure
        (:class:`~lot_textual_ui.lot_cli.LotError`) is surfaced as an error toast
        and the form stays open so the input is not lost.
        """
        name = self.query_one("#new-thing-name", Input).value.strip()
        error = self.query_one("#new-thing-error", Label)
        if not name:
            error.update(_EMPTY_NAME_MESSAGE)
            self.query_one("#new-thing-name", Input).focus()
            return
        error.update("")
        body = self.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text
        self._create(name, body)

    @work(exclusive=True, group="new-thing-create")
    async def _create(self, name: str, body: str) -> None:
        # Import here to avoid a module import cycle (app imports this module).
        from .lot_cli import LotError

        try:
            new_id = await self.app.lot_cli.thing_new(
                name, body, parent=self._parent_id
            )
        except LotError as error:
            self.app.notify(
                str(error), title="Could not create Thing", severity="error"
            )
            return
        self.dismiss(new_id)
