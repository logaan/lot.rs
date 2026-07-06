"""Modal input forms for mutating the vault (Things and Updates).

The command palette (see :mod:`lot_textual_ui.palette`) discovers ``lot`` leaf
commands and, for those that need user input, routes to a form screen instead of
running the command blind. This module holds those screens:
:class:`NewThingScreen` (``thing new``) and :class:`NewUpdateScreen` (``update
<type>`` for every creatable type — built-ins and config-defined custom types
alike, discovered via ``lot settings get``).

All ``lot`` invocation stays inside :class:`~lot_textual_ui.lot_cli.LotCli`; a
form only collects fields and hands them to a typed ``LotCli`` method, then
reports the result back to the app through its :class:`~textual.screen.Screen`
``dismiss`` value.
"""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, TextArea

from .editor import RunEditor, edit_in_editor
from .models import UpdateType, builtin_update_types, creatable_update_types

# The addressable id of the new-Thing body editor. The ``$EDITOR`` escape-hatch
# work item targets this widget to swap its contents for an editor round-trip,
# so it is a named constant rather than an inline string.
BODY_TEXTAREA_ID = "new-thing-body"

# The addressable id of the new-Update body editor — the sibling seam the
# ``$EDITOR`` escape-hatch work item targets on the Update form (mirrors
# :data:`BODY_TEXTAREA_ID`).
UPDATE_BODY_TEXTAREA_ID = "new-update-body"

# The tag appended to a *terminal* type's radio label so it is obvious the
# type retires the Thing's status (readme §1.3). A named constant so the form
# tests and any restyling have one place to look.
TERMINAL_TAG = "terminal"

_EMPTY_NAME_MESSAGE = "A name is required."
_EMPTY_BODY_MESSAGE = "A body is required for this update type."


def _default_update_types() -> list[UpdateType]:
    """The types the Update forms offer when the caller passes none.

    The creatable built-ins (``work``/``info``/``done``) — the same set the
    form hardcoded before custom types existed — so a form pushed without
    discovered config still works.
    """
    return creatable_update_types(builtin_update_types())


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

    A :class:`~textual.widgets.RadioSet` picks the update **type** and a
    multi-line :class:`~textual.widgets.TextArea` (id
    :data:`UPDATE_BODY_TEXTAREA_ID`) collects the markdown **body**. The types
    offered are dynamic: the caller passes the effective set discovered from
    ``lot settings get`` (built-ins plus config-defined custom types, readme
    §1.3/§5.5.1); with none passed the form falls back to the creatable
    built-ins (``work``/``info``/``done``). A type with ``takes-body = false``
    is a bare marker like ``done``, so selecting it hides the body field; a
    *terminal* type's radio label carries a dim :data:`TERMINAL_TAG` so it is
    obvious it retires the Thing's status. Submitting (``ctrl+s`` or the Add
    button) validates client-side — a body-taking type requires a non-empty
    body; a bodyless one requires none — then runs
    :meth:`~lot_textual_ui.lot_cli.LotCli.add_update` with the body piped on
    stdin (or ``None`` for a bodyless type). Cancelling (``escape`` or the
    Cancel button) closes the form without touching the vault.

    The screen targets a specific Thing and is *reusable*:

    Args:
        thing_id: The Thing the Update is appended to (``lot update <kind>
            --thing <id>``). The palette seeds this with the app's current
            selection; batch/other flows can pass an explicit id.
        thing_label: A human label for the target Thing, shown in the form so it
            is obvious which Thing the Update lands on. Falls back to
            ``thing_id`` when omitted.
        kind: The name of the update type initially selected. The palette opens
            the form pre-set to the leaf it came from (``update work`` →
            ``"work"``, ``update wont-do`` → ``"wont-do"``, …); defaults to
            ``"work"``. An unknown name falls back to the first offered type.
        title: The modal window title. Defaults to ``"New Update"``.
        update_types: The update types to offer, in display order — the app
            passes the creatable effective set from its loaded config. ``None``
            (or empty) falls back to the creatable built-ins.

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
        update_types: Sequence[UpdateType] | None = None,
    ) -> None:
        super().__init__()
        self._thing_id = thing_id
        self._thing_label = thing_label or thing_id
        self._types = list(update_types) if update_types else _default_update_types()
        names = [t.name for t in self._types]
        self._initial_kind = kind if kind in names else names[0]
        self._title = title

    def compose(self) -> ComposeResult:
        with Vertical(id="new-update-dialog"):
            yield Label(self._title, id="new-update-title")
            yield Label(f"On: {self._thing_label}", id="new-update-target")
            yield Label("Type", classes="new-update-field-label")
            with RadioSet(id="new-update-type"):
                for update_type in self._types:
                    yield RadioButton(
                        self._type_label(update_type),
                        value=(update_type.name == self._initial_kind),
                    )
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

    @staticmethod
    def _type_label(update_type: UpdateType) -> Text:
        """A radio label for the type: its name, plus a dim tag when terminal.

        The :data:`TERMINAL_TAG` hint tells the user this type retires the
        Thing's status (readme §1.3) — it applies to the built-in ``done`` and
        any custom type flagged ``terminal = true`` alike.
        """
        if update_type.terminal:
            return Text.assemble(update_type.name, (f"  · {TERMINAL_TAG}", "dim"))
        return Text(update_type.name)

    def on_mount(self) -> None:
        # Hide the body field up front when the initial type takes no body.
        self._set_body_visible(self._selected_type().takes_body)
        self.query_one("#new-update-type", RadioSet).focus()

    # --- actions / events --------------------------------------------------

    def _selected_type(self) -> UpdateType:
        """The update type currently chosen in the radio set."""
        index = self.query_one("#new-update-type", RadioSet).pressed_index
        if 0 <= index < len(self._types):
            return self._types[index]
        return next(t for t in self._types if t.name == self._initial_kind)

    def _set_body_visible(self, visible: bool) -> None:
        """Show/hide the body label + editor (hidden for bodyless marker types)."""
        self.query_one("#new-update-body-label", Label).display = visible
        self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).display = visible

    @on(RadioSet.Changed, "#new-update-type")
    def _type_changed(self) -> None:
        # A `takes-body = false` type (like `done`) carries no body, so hide
        # the field the moment such a type is chosen.
        self._set_body_visible(self._selected_type().takes_body)

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

        A body-taking type requires a non-empty body — an empty one is rejected
        in-form with a friendly message and no CLI call. A ``takes-body =
        false`` type (like ``done``) carries no body, so the (hidden) body
        field is ignored and ``None`` is sent — the CLI rejects content for
        such types. The create runs in a worker so the ``lot`` subprocess never
        blocks the event loop; a CLI failure
        (:class:`~lot_textual_ui.lot_cli.LotError`) surfaces as an error toast
        and the form stays open so the input is not lost.
        """
        selected = self._selected_type()
        error = self.query_one("#new-update-error", Label)
        body = self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text
        if selected.takes_body and not body.strip():
            error.update(_EMPTY_BODY_MESSAGE)
            self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).focus()
            return
        error.update("")
        self._create(selected.name, body if selected.takes_body else None)

    @work(exclusive=True, group="new-update-create")
    async def _create(self, kind: str, body: str | None) -> None:
        # Import here to avoid a module import cycle (app imports this module).
        from .lot_cli import LotError

        try:
            new_id = await self.app.lot_cli.add_update(kind, self._thing_id, body)
        except LotError as error:
            self.app.notify(str(error), title="Could not add Update", severity="error")
            return
        self.dismiss(new_id)


class BatchUpdateScreen(NewUpdateScreen):
    """The batch variant of the new-Update form: collect once, apply to many.

    Reuses :class:`NewUpdateScreen` wholesale — same layout, dynamic type radio
    set (with the terminal tag and per-type body visibility), body field with
    its ``$EDITOR`` hatch, and client-side validation — but is a pure
    *collector*: submitting never touches the vault. Instead of running ``lot
    update`` itself it ``dismiss``\\es with the validated ``(kind, body)`` pair
    (``body`` is ``None`` for a ``takes-body = false`` type), and the app
    applies that one Update to every marked Thing sequentially (with per-item
    error reporting). Cancelling dismisses with ``None``, exactly like the
    parent.

    Args:
        count: How many Things are marked; shown in the "On:" line so it is
            obvious the Update will land on all of them.
        kind: The update type initially selected (as on the parent).
        update_types: The update types to offer (as on the parent).
    """

    def __init__(
        self,
        count: int,
        kind: str = "work",
        update_types: Sequence[UpdateType] | None = None,
    ) -> None:
        label = f"{count} marked Thing{'s' if count != 1 else ''}"
        super().__init__(
            thing_id="",
            thing_label=label,
            kind=kind,
            title="Update marked Things",
            update_types=update_types,
        )

    def _create(self, kind: str, body: str | None) -> None:  # type: ignore[override]
        """Dismiss with the collected fields; the app runs the batch."""
        self.dismiss((kind, body))
