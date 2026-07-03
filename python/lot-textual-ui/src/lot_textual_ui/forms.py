"""Modal input forms for mutating the vault (Things and Updates).

The command palette (see :mod:`lot_textual_ui.palette`) discovers ``lot`` leaf
commands and, for those that need user input, routes to a form screen instead of
running the command blind. This module holds those screens:
:class:`NewThingScreen` (``thing new``) and :class:`NewUpdateScreen` (``update
work``/``info``/``done``).

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
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, TextArea

from .editor import RunEditor, edit_in_editor

# The addressable id of the new-Thing body editor. The ``$EDITOR`` escape-hatch
# work item targets this widget to swap its contents for an editor round-trip,
# so it is a named constant rather than an inline string.
BODY_TEXTAREA_ID = "new-thing-body"

# The addressable id of the new-Update body editor — the sibling seam the
# ``$EDITOR`` escape-hatch work item targets on the Update form (mirrors
# :data:`BODY_TEXTAREA_ID`).
UPDATE_BODY_TEXTAREA_ID = "new-update-body"

# The Update types the form offers, in display order. ``work``/``info`` carry a
# markdown body; ``done`` is a bare retirement marker with no body (readme §4),
# so selecting it hides the body field. Kept a small explicit tuple rather than
# discovering it from the help tree: Phase 7's custom-type discovery is a
# separate work item.
UPDATE_KINDS: tuple[str, ...] = ("work", "info", "done")

_EMPTY_NAME_MESSAGE = "A name is required."
_EMPTY_BODY_MESSAGE = "A body is required for work and info updates."

# The shared binding both forms expose for the ``$EDITOR`` escape hatch. It is
# ``priority=True`` on purpose: a focused :class:`~textual.widgets.TextArea`
# already binds ``ctrl+e`` (cursor-to-line-end), and a priority screen binding
# is checked *before* the focused widget, so the escape hatch wins while the
# body editor has focus — which is exactly when a user reaches for it.
_EDIT_IN_EDITOR_BINDING = Binding(
    "ctrl+e", "edit_body", "Edit in $EDITOR", show=True, priority=True
)


class _BodyEditorMixin:
    """Give a form's body :class:`~textual.widgets.TextArea` the ``$EDITOR`` hatch.

    A form opts in by (1) mixing this in, (2) setting :attr:`_BODY_TEXTAREA_ID`
    to its body editor's id, and (3) adding :data:`_EDIT_IN_EDITOR_BINDING` to
    its ``BINDINGS``. The ``edit_body`` action then dumps the TextArea's current
    text into the user's editor (see :mod:`lot_textual_ui.editor`) and writes the
    result back — the reusable seam future text inputs plug into.

    Tests substitute the editor-launch by setting :attr:`_run_editor` on the
    pushed screen to a fake; production leaves it ``None`` (a real subprocess).
    """

    # The id of the multi-line body editor the escape hatch targets. Subclasses
    # set this to their body TextArea's id.
    _BODY_TEXTAREA_ID: str
    # Injectable editor-launch seam; ``None`` uses the real subprocess. Tests set
    # this on the pushed screen to substitute a fake that "edits" the temp file.
    _run_editor: RunEditor | None = None

    def action_edit_body(self) -> None:
        """Open the body text in ``$EDITOR`` and load the result back.

        Runs synchronously: the app is suspended and the editor owns the
        terminal until it exits (see :func:`lot_textual_ui.editor.edit_in_editor`).
        Cancelling in the editor (a non-zero exit) leaves the body unchanged.
        """
        textarea = self.query_one(f"#{self._BODY_TEXTAREA_ID}", TextArea)
        edited = edit_in_editor(self.app, textarea.text, run_editor=self._run_editor)
        textarea.load_text(edited)
        textarea.focus()


class NewThingScreen(_BodyEditorMixin, ModalScreen[str | None]):
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
    # ``ctrl+e`` opens the body in ``$EDITOR`` (see :class:`_BodyEditorMixin`).
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Create", show=True),
        _EDIT_IN_EDITOR_BINDING,
    ]

    # The body editor the ``$EDITOR`` escape hatch (mixin) targets.
    _BODY_TEXTAREA_ID = BODY_TEXTAREA_ID

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


class NewUpdateScreen(_BodyEditorMixin, ModalScreen[str | None]):
    """Modal form that appends an Update to a Thing via ``lot update``.

    A :class:`~textual.widgets.RadioSet` picks the update **type**
    (``work``/``info``/``done``, defaulting to ``work``) and a multi-line
    :class:`~textual.widgets.TextArea` (id :data:`UPDATE_BODY_TEXTAREA_ID`)
    collects the markdown **body**. ``done`` is a bare retirement marker with no
    body (readme §4), so selecting it hides the body field. Submitting
    (``ctrl+s`` or the Add button) validates client-side — ``work``/``info``
    require a non-empty body; ``done`` requires none — then runs the matching
    :class:`~lot_textual_ui.lot_cli.LotCli` method with the body piped on stdin
    (``work``/``info``) or with no stdin (``done``). Cancelling (``escape`` or
    the Cancel button) closes the form without touching the vault.

    The screen targets a specific Thing and is *reusable*:

    Args:
        thing_id: The Thing the Update is appended to (``lot update <kind>
            --thing <id>``). The palette seeds this with the app's current
            selection; batch/other flows can pass an explicit id.
        thing_label: A human label for the target Thing, shown in the form so it
            is obvious which Thing the Update lands on. Falls back to
            ``thing_id`` when omitted.
        kind: The update type initially selected. The palette opens the form
            pre-set to the leaf it came from (``update work`` → ``"work"``, …);
            defaults to ``"work"``. Must be one of :data:`UPDATE_KINDS`.
        title: The modal window title. Defaults to ``"New Update"``.

    On success the screen ``dismiss``\\es with the new update's ``lot:`` id (a
    ``str``); on cancel it dismisses with ``None``. The caller (the app) decides
    what to do next — the form itself never touches the selection or reloads the
    vault. The body TextArea stays addressable by :data:`UPDATE_BODY_TEXTAREA_ID`
    so the ``$EDITOR`` escape-hatch work item can target it without restructuring
    the form.
    """

    DEFAULT_CSS = """
    NewUpdateScreen {
        align: center middle;
    }

    NewUpdateScreen > #new-update-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    NewUpdateScreen #new-update-title {
        text-style: bold;
        margin-bottom: 1;
    }

    NewUpdateScreen #new-update-target {
        color: $text-muted;
        margin-bottom: 1;
    }

    NewUpdateScreen .new-update-field-label {
        margin-top: 1;
        color: $text-muted;
    }

    NewUpdateScreen #new-update-type {
        width: 1fr;
    }

    NewUpdateScreen #new-update-body {
        width: 1fr;
        height: 12;
    }

    NewUpdateScreen #new-update-error {
        color: $error;
        height: auto;
        margin-top: 1;
    }

    NewUpdateScreen #new-update-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    NewUpdateScreen #new-update-buttons Button {
        margin-left: 2;
    }
    """

    # Screen-local bindings only (app-level keys stay in keys.py). ``escape``
    # cancels; ``ctrl+s`` submits — the TextArea captures plain ``enter`` for
    # newlines, so submission is an explicit chord, mirroring NewThingScreen.
    # ``ctrl+e`` opens the body in ``$EDITOR`` (see :class:`_BodyEditorMixin`).
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Add", show=True),
        _EDIT_IN_EDITOR_BINDING,
    ]

    # The body editor the ``$EDITOR`` escape hatch (mixin) targets.
    _BODY_TEXTAREA_ID = UPDATE_BODY_TEXTAREA_ID

    def __init__(
        self,
        thing_id: str,
        thing_label: str | None = None,
        kind: str = "work",
        title: str = "New Update",
    ) -> None:
        super().__init__()
        self._thing_id = thing_id
        self._thing_label = thing_label or thing_id
        self._initial_kind = kind if kind in UPDATE_KINDS else "work"
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="new-update-dialog"):
            yield Label(self._title, id="new-update-title")
            yield Label(f"On: {self._thing_label}", id="new-update-target")
            yield Label("Type", classes="new-update-field-label")
            with RadioSet(id="new-update-type"):
                for kind in UPDATE_KINDS:
                    yield RadioButton(kind, value=(kind == self._initial_kind))
            yield Label(
                "Body (markdown)",
                id="new-update-body-label",
                classes="new-update-field-label",
            )
            yield TextArea(id=UPDATE_BODY_TEXTAREA_ID)
            yield Label("", id="new-update-error")
            with Horizontal(id="new-update-buttons"):
                yield Button("Cancel", variant="default", id="new-update-cancel")
                yield Button("Add", variant="primary", id="new-update-add")

    def on_mount(self) -> None:
        # Hide the body field up front when the initial type is ``done``.
        self._set_body_visible(self._initial_kind != "done")
        self.query_one("#new-update-type", RadioSet).focus()

    # --- actions / events --------------------------------------------------

    def _selected_kind(self) -> str:
        """The update type currently chosen in the radio set."""
        index = self.query_one("#new-update-type", RadioSet).pressed_index
        if 0 <= index < len(UPDATE_KINDS):
            return UPDATE_KINDS[index]
        return self._initial_kind

    def _set_body_visible(self, visible: bool) -> None:
        """Show/hide the body label + editor (hidden for the bodyless ``done``)."""
        self.query_one("#new-update-body-label", Label).display = visible
        self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).display = visible

    @on(RadioSet.Changed, "#new-update-type")
    def _type_changed(self) -> None:
        # ``done`` takes no body, so hide the field the moment it is chosen.
        self._set_body_visible(self._selected_kind() != "done")

    @on(Button.Pressed, "#new-update-cancel")
    def _cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#new-update-add")
    def _add_button(self) -> None:
        self.action_submit()

    def action_cancel(self) -> None:
        """Close the form without adding anything."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Validate, append the Update, and dismiss with its id.

        ``work``/``info`` require a non-empty body — an empty one is rejected
        in-form with a friendly message and no CLI call. ``done`` takes no body,
        so an empty body field is fine. The create runs in a worker so the
        ``lot`` subprocess never blocks the event loop; a CLI failure
        (:class:`~lot_textual_ui.lot_cli.LotError`) surfaces as an error toast
        and the form stays open so the input is not lost.
        """
        kind = self._selected_kind()
        error = self.query_one("#new-update-error", Label)
        body = self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text
        if kind != "done" and not body.strip():
            error.update(_EMPTY_BODY_MESSAGE)
            self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).focus()
            return
        error.update("")
        self._create(kind, body)

    @work(exclusive=True, group="new-update-create")
    async def _create(self, kind: str, body: str) -> None:
        # Import here to avoid a module import cycle (app imports this module).
        from .lot_cli import LotError

        try:
            if kind == "done":
                new_id = await self.app.lot_cli.update_done(self._thing_id)
            elif kind == "info":
                new_id = await self.app.lot_cli.update_info(self._thing_id, body)
            else:
                new_id = await self.app.lot_cli.update_work(self._thing_id, body)
        except LotError as error:
            self.app.notify(str(error), title="Could not add Update", severity="error")
            return
        self.dismiss(new_id)
