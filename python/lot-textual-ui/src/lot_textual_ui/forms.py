"""Modal input forms for mutating the vault (Things and Updates).

The command palette (see :mod:`lot_textual_ui.palette`) discovers ``lot`` leaf
commands and, for those that need user input, routes to a form screen instead of
running the command blind. This module holds those screens:
:class:`NewThingScreen` (``thing new``) and :class:`NewUpdateScreen` (one
``update <type>`` leaf — built-in or config-defined custom type alike — with
the form fixed to that type; there is no general "pick a type" update form).

All ``lot`` invocation stays inside :class:`~lot_textual_ui.lot_cli.LotCli`; a
form only collects fields and hands them to a typed ``LotCli`` method, then
reports the result back to the app through its :class:`~textual.screen.Screen`
``dismiss`` value.
"""

from __future__ import annotations

import dataclasses

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, TextArea

from .editor import RunEditor, edit_in_editor
from .mnemonics import assign_mnemonic
from .webmode import is_web_mode

# The addressable id of the new-Thing body editor. The ``$EDITOR`` escape-hatch
# work item targets this widget to swap its contents for an editor round-trip,
# so it is a named constant rather than an inline string.
BODY_TEXTAREA_ID = "new-thing-body"

# The addressable id of the new-Update body editor — the sibling seam the
# ``$EDITOR`` escape-hatch work item targets on the Update form (mirrors
# :data:`BODY_TEXTAREA_ID`).
UPDATE_BODY_TEXTAREA_ID = "new-update-body"

_EMPTY_NAME_MESSAGE = "A name is required."
_EMPTY_BODY_MESSAGE = "A body is required for this update type."


@dataclasses.dataclass(frozen=True)
class NewThingResult:
    """The outcome of a successful :class:`NewThingScreen` submit.

    The form used to dismiss with a bare ``str`` id; it now carries the id
    *plus* which button was pressed, so the app can tell "Create" from "Create
    and send" without a second channel.

    Args:
        thing_id: The ``lot:`` id of the freshly created Thing.
        send: ``True`` when the user chose **Create and send** — after the app
            selects the new Thing it kicks off the Claude stage on it (see
            :meth:`~lot_textual_ui.commands.CommandsMixin._new_thing_created`).
            ``False`` for a plain **Create**.
    """

    thing_id: str
    send: bool


# The shared binding both forms expose for the ``$EDITOR`` escape hatch. It is
# ``priority=True`` so the shortcut always fires regardless of which widget
# has focus — a priority screen binding is checked *before* the focused
# widget — which matters because the body editor is usually focused when a
# user reaches for it. (``ctrl+e`` was tried first, but a focused
# :class:`~textual.widgets.TextArea` already binds that to cursor-to-line-end,
# hence ``ctrl+o``.)
_EDIT_IN_EDITOR_BINDING = Binding(
    "ctrl+o", "edit_body", "Edit in $EDITOR", show=True, priority=True
)

# Letters both forms already spend on their own screen-local bindings, seeded
# into :func:`assign_mnemonic`'s ``used`` set so a button mnemonic never
# collides with them: ``s`` (``ctrl+s`` submit), ``e`` (``ctrl+e`` the
# ``$EDITOR`` hatch above). ``o`` is reserved pre-emptively too — a sibling,
# not-yet-merged work item rebinds the hatch from ``ctrl+e`` to ``ctrl+o``,
# and this reservation is meant to still hold once that lands.
_SCREEN_RESERVED_LETTERS = frozenset("seo")

# The toast shown when the ``$EDITOR`` hatch is pressed in web mode. The editor
# would have to run on the *server's* terminal — which the app has no way to
# hand over while serving a browser session — so the hatch is disabled there
# (see :func:`lot_textual_ui.webmode.is_web_mode`).
WEB_EDITOR_NOTICE = (
    "$EDITOR needs a local terminal, so it is not available "
    "when the app is served to a browser."
)


class _BodyEditorMixin:
    """Give a form's body :class:`~textual.widgets.TextArea` the ``$EDITOR`` hatch.

    A form opts in by (1) mixing this in, (2) setting :attr:`_BODY_TEXTAREA_ID`
    to its body editor's id, and (3) adding :data:`_EDIT_IN_EDITOR_BINDING` to
    its ``BINDINGS``. The ``edit_body`` action then dumps the TextArea's current
    text into the user's editor (see :mod:`lot_textual_ui.editor`) and writes the
    result back — the reusable seam future text inputs plug into.

    **Web mode** (:func:`~lot_textual_ui.webmode.is_web_mode`): the hatch needs
    a local terminal to suspend to, which a browser session does not have, so it
    is disabled — the binding is hidden from the footer (the key stays bound so
    a press can explain itself) and :meth:`action_edit_body` shows
    :data:`WEB_EDITOR_NOTICE` instead of ever launching an editor.

    Tests substitute the editor-launch by setting :attr:`_run_editor` on the
    pushed screen to a fake; production leaves it ``None`` (a real subprocess).
    """

    # The id of the multi-line body editor the escape hatch targets. Subclasses
    # set this to their body TextArea's id.
    _BODY_TEXTAREA_ID: str
    # Injectable editor-launch seam; ``None`` uses the real subprocess. Tests set
    # this on the pushed screen to substitute a fake that "edits" the temp file.
    _run_editor: RunEditor | None = None

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        if is_web_mode():
            self._hide_editor_binding()

    def _hide_editor_binding(self) -> None:
        """Hide the ``$EDITOR`` binding from this screen's footer (web mode).

        Only the footer advertisement goes: the key itself stays bound, so a
        user pressing the chord anyway (muscle memory from the terminal app)
        gets :data:`WEB_EDITOR_NOTICE` from :meth:`action_edit_body` rather
        than silence. ``BINDINGS`` is class-level, so a **fresh**
        :class:`~textual.binding.BindingsMap` is built for this instance —
        Textual caches the merged class bindings, and mutating those shared
        lists in place would hide the binding on every later instance too.
        """
        hidden = {
            key: [
                dataclasses.replace(binding, show=False)
                if binding.action == _EDIT_IN_EDITOR_BINDING.action
                else binding
                for binding in bindings
            ]
            for key, bindings in self._bindings.key_to_bindings.items()
        }
        self._bindings = BindingsMap.from_keys(hidden)

    def action_edit_body(self) -> None:
        """Open the body text in ``$EDITOR`` and load the result back.

        Runs synchronously: the app is suspended and the editor owns the
        terminal until it exits (see :func:`lot_textual_ui.editor.edit_in_editor`).
        Cancelling in the editor (a non-zero exit) leaves the body unchanged.
        In web mode there is no local terminal to suspend to, so this notifies
        (:data:`WEB_EDITOR_NOTICE`) and never launches an editor.
        """
        if is_web_mode():
            self.app.notify(
                WEB_EDITOR_NOTICE, title="Edit in $EDITOR", severity="warning"
            )
            return
        textarea = self.query_one(f"#{self._BODY_TEXTAREA_ID}", TextArea)
        edited = edit_in_editor(self.app, textarea.text, run_editor=self._run_editor)
        textarea.load_text(edited)
        textarea.focus()


class NewThingScreen(_BodyEditorMixin, ModalScreen[NewThingResult | None]):
    """Modal form that creates a Thing via ``lot thing new``.

    A single-line :class:`~textual.widgets.Input` collects the Thing's **name**
    and a multi-line :class:`~textual.widgets.TextArea` (id
    :data:`BODY_TEXTAREA_ID`) collects the markdown **body**. There are two ways
    to submit, both of which validate the name client-side and then run ``lot
    thing new`` through the shared :class:`~lot_textual_ui.lot_cli.LotCli` with
    the body piped on stdin:

    * **Create** (``ctrl+s`` or the Create button) just creates the Thing.
    * **Create and send** (the Create and send button) creates it and then asks
      the app to kick off the Claude stage on the new Thing — the create-and-
      hand-to-Claude shortcut. The two differ only in the :attr:`send` flag of
      the :class:`NewThingResult` they dismiss with; the form never talks to
      Claude itself.

    Cancelling (``escape`` or the Cancel button) closes the form without
    touching the vault.

    The screen is *reusable and parametrised* so later work items can drive it:

    Args:
        parent_id: When given, the Thing is created as a child of this Thing
            (passed through to ``lot thing new --parent <id>``). The
            create-child-Things work item invokes the form with a preset parent;
            ``None`` (the default) creates a top-level Thing.
        title: The window title shown at the top of the modal. Defaults to
            ``"New Thing"``; a caller creating a child can pass e.g.
            ``"New child Thing"``.

    On success the screen ``dismiss``\\es with a :class:`NewThingResult` (the new
    Thing's ``lot:`` id and the "and send" flag); on cancel it dismisses with
    ``None``. The caller (the app) decides what to do with it — the form itself
    never touches the selection or reloads the vault. The body TextArea is left
    addressable by :data:`BODY_TEXTAREA_ID` so the ``$EDITOR`` escape-hatch work
    item can target it without restructuring the form.
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
    # ``ctrl+o`` opens the body in ``$EDITOR`` (see :class:`_BodyEditorMixin`).
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
        # Mnemonics are assigned in this order (see :func:`assign_mnemonic`):
        # Create first, then Cancel, then Create and send. Create keeps its
        # ``r`` and Cancel its ``a`` regardless of the new third button —
        # assigning "Create and send" last leaves those two chords stable —
        # so it falls through to ``ctrl+t``.
        used_letters = set(_SCREEN_RESERVED_LETTERS)
        self._create_key, self._create_label = assign_mnemonic("Create", used_letters)
        self._cancel_key, self._cancel_label = assign_mnemonic("Cancel", used_letters)
        self._send_key, self._send_label = assign_mnemonic(
            "Create and send", used_letters
        )
        self._bindings.bind(self._create_key, "submit", show=False, priority=True)
        self._bindings.bind(self._cancel_key, "cancel", show=False, priority=True)
        self._bindings.bind(
            self._send_key, "submit_and_send", show=False, priority=True
        )

    def compose(self) -> ComposeResult:
        with Vertical(id="new-thing-dialog"):
            yield Label(self._title, id="new-thing-title")
            yield Label("Name", classes="new-thing-field-label")
            yield Input(placeholder="Thing name", id="new-thing-name")
            yield Label("Body (markdown)", classes="new-thing-field-label")
            yield TextArea(id=BODY_TEXTAREA_ID)
            yield Label("", id="new-thing-error")
            with Horizontal(id="new-thing-buttons"):
                yield Button(
                    self._cancel_label, variant="default", id="new-thing-cancel"
                )
                yield Button(
                    self._create_label, variant="default", id="new-thing-create"
                )
                yield Button(self._send_label, variant="primary", id="new-thing-send")

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

    @on(Button.Pressed, "#new-thing-send")
    def _send_button(self) -> None:
        self.action_submit_and_send()

    def action_cancel(self) -> None:
        """Close the form without creating anything."""
        self.dismiss(None)

    def action_submit(self) -> None:
        """Create the Thing (plain **Create**), then dismiss with its id."""
        self._submit(send=False)

    def action_submit_and_send(self) -> None:
        """Create the Thing and ask the app to send it to Claude next.

        The **Create and send** path: identical create, but the dismissed
        :class:`NewThingResult` carries ``send=True`` so the app kicks off the
        Claude stage on the new Thing once the selection has jumped to it.
        """
        self._submit(send=True)

    def _submit(self, *, send: bool) -> None:
        """Validate, then create the Thing in a worker (shared by both buttons).

        An empty (or whitespace-only) name is rejected in-form with a friendly
        message and no CLI call. Otherwise the create runs in a worker so the
        ``lot`` subprocess never blocks the event loop; a CLI failure
        (:class:`~lot_textual_ui.lot_cli.LotError`) is surfaced as an error toast
        and the form stays open so the input is not lost. ``send`` is threaded
        through to the :class:`NewThingResult` the create dismisses with.
        """
        name = self.query_one("#new-thing-name", Input).value.strip()
        error = self.query_one("#new-thing-error", Label)
        if not name:
            error.update(_EMPTY_NAME_MESSAGE)
            self.query_one("#new-thing-name", Input).focus()
            return
        error.update("")
        body = self.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text
        self._create(name, body, send)

    @work(exclusive=True, group="new-thing-create")
    async def _create(self, name: str, body: str, send: bool) -> None:
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
        self.dismiss(NewThingResult(thing_id=new_id, send=send))


class NewUpdateScreen(_BodyEditorMixin, ModalScreen[str | None]):
    """Modal form that appends one **type-fixed** Update via ``lot update <kind>``.

    The update type is decided *before* the form opens — ``ctrl+u`` ``w`` / the
    ``update work`` palette entry open a ``work`` form, ``update info`` an
    ``info`` form, a custom body-taking type (readme §1.3) its own form — so
    the screen shows no type selector: just a multi-line
    :class:`~textual.widgets.TextArea` (id :data:`UPDATE_BODY_TEXTAREA_ID`) for
    the markdown **body**. Bodyless types (``done``, or a custom
    ``takes-body = false`` type) never open a form at all — the app runs them
    straight away (see
    :meth:`~lot_textual_ui.app.LotTextualApp.add_bodyless_update`) — so
    submitting here (``ctrl+s`` or the Add button) always requires a non-empty
    body, then runs :meth:`~lot_textual_ui.lot_cli.LotCli.add_update` with the
    body piped on stdin. Cancelling (``escape`` or the Cancel button) closes
    the form without touching the vault.

    The screen targets a specific Thing and is *reusable*:

    Args:
        thing_id: The Thing the Update is appended to (``lot update <kind>
            --thing <id>``). The palette seeds this with the app's current
            selection; other flows can pass an explicit id.
        thing_label: A human label for the target Thing, shown in the form so it
            is obvious which Thing the Update lands on. Falls back to
            ``thing_id`` when omitted.
        kind: The update type this form appends — any body-taking type name
            from the vault's configured set.
        title: The modal window title. Defaults to ``"New <kind> update"``.

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
    # ``ctrl+o`` opens the body in ``$EDITOR`` (see :class:`_BodyEditorMixin`).
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
        *,
        kind: str,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._thing_id = thing_id
        self._thing_label = thing_label or thing_id
        self._kind = kind
        self._title = title if title is not None else f"New {kind} update"
        # Add is the primary action, so it picks its mnemonic first (see
        # :func:`assign_mnemonic`); Cancel gets whatever is left.
        used_letters = set(_SCREEN_RESERVED_LETTERS)
        self._add_key, self._add_label = assign_mnemonic("Add", used_letters)
        self._cancel_key, self._cancel_label = assign_mnemonic("Cancel", used_letters)
        self._bindings.bind(self._add_key, "submit", show=False, priority=True)
        self._bindings.bind(self._cancel_key, "cancel", show=False, priority=True)

    def compose(self) -> ComposeResult:
        with Vertical(id="new-update-dialog"):
            yield Label(self._title, id="new-update-title")
            yield Label(f"On: {self._thing_label}", id="new-update-target")
            yield from self._compose_kind_field()
            yield Label(
                "Body (markdown)",
                id="new-update-body-label",
                classes="new-update-field-label",
            )
            yield TextArea(id=UPDATE_BODY_TEXTAREA_ID)
            yield Label("", id="new-update-error")
            with Horizontal(id="new-update-buttons"):
                yield Button(
                    self._cancel_label, variant="default", id="new-update-cancel"
                )
                yield Button(self._add_label, variant="primary", id="new-update-add")

    def on_mount(self) -> None:
        self._set_body_visible(self._kind_takes_body(self._selected_kind()))
        self._focus_initial()

    # --- the type seam ------------------------------------------------------
    #
    # Both this screen and its batch subclass fix the type before opening (the
    # batch's is chosen in the command navigator, mirroring ``ctrl+u``), so
    # these stay trivial — a seam kept for any future type-choosing variant.

    def _compose_kind_field(self) -> ComposeResult:
        """Widgets for choosing the type — none: the type is fixed."""
        yield from ()

    def _selected_kind(self) -> str:
        """The update type this form appends (fixed at construction)."""
        return self._kind

    def _kind_takes_body(self, kind: str) -> bool:
        """Whether ``kind`` carries a body — always true here: the app never
        opens this form for a bodyless type (it runs those directly)."""
        return True

    def _focus_initial(self) -> None:
        """Land the cursor in the body editor so typing starts immediately."""
        self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).focus()

    # --- actions / events --------------------------------------------------

    def _set_body_visible(self, visible: bool) -> None:
        """Show/hide the body label + editor (hidden for bodyless marker types)."""
        self.query_one("#new-update-body-label", Label).display = visible
        self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).display = visible

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
        in-form with a friendly message and no CLI call. Neither the single nor
        the batch form is ever opened for a ``takes-body = false`` type (the app
        records those directly, no form), so the bodyless branch here is purely
        defensive: it sends ``None`` rather than an ignored body. The create
        runs in a worker so the
        ``lot`` subprocess never blocks the event loop; a CLI failure
        (:class:`~lot_textual_ui.lot_cli.LotError`) surfaces as an error toast
        and the form stays open so the input is not lost.
        """
        kind = self._selected_kind()
        takes_body = self._kind_takes_body(kind)
        error = self.query_one("#new-update-error", Label)
        body = self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text
        if takes_body and not body.strip():
            error.update(_EMPTY_BODY_MESSAGE)
            self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).focus()
            return
        error.update("")
        self._create(kind, body if takes_body else None)

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
    """Body-only batch collector for a pre-chosen body-taking update type.

    The batch update type is chosen *before* this form opens: the ``U`` /
    "Update marked Things" entry point runs the command navigator first —
    exactly the type-select step of the single-Thing ``ctrl+u`` flow — and a
    bodyless type (``done``-likes) skips the form entirely. So, like
    :class:`NewUpdateScreen`, this shows **no type selector**: just the body
    field with its ``$EDITOR`` hatch. Unlike it, it is a pure *collector* —
    submitting never touches the vault; it ``dismiss``\\es with the validated
    ``(kind, body)`` pair and the app applies that one Update to every marked
    Thing sequentially (with per-item error reporting). Cancelling dismisses
    with ``None``, exactly like the parent.

    Args:
        count: How many Things are marked; shown in the "On:" line so it is
            obvious the Update will land on all of them.
        kind: The (body-taking) update type chosen before the form opened.
    """

    def __init__(self, count: int, *, kind: str) -> None:
        label = f"{count} marked Thing{'s' if count != 1 else ''}"
        super().__init__(thing_id="", thing_label=label, kind=kind)

    def _create(self, kind: str, body: str | None) -> None:  # type: ignore[override]
        """Dismiss with the collected fields; the app runs the batch."""
        self.dismiss((kind, body))
