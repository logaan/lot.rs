"""Modal input forms for mutating (and inspecting) the vault.

The command palette (see :mod:`lot_textual_ui.palette`) discovers ``lot`` leaf
commands and, for those that need user input, routes to a form screen instead of
running the command blind. This module holds those screens:

* :class:`NewUpdateScreen` — one ``update <type>`` leaf (built-in or
  config-defined custom type alike), the form fixed to that type; there is no
  general "pick a type" update form. It carries a body editor and hands its
  fields to a *typed* ``LotCli`` method. It survives as the base of the modal
  :class:`BatchUpdateScreen`; the single-Thing new-Update and new-Thing forms are
  now inline widgets (:class:`~lot_textual_ui.detail.InlineUpdateForm` and
  :class:`~lot_textual_ui.detail.InlineNewThingForm`), not modals.
* :class:`CommandFormScreen` — a reusable, :class:`~lot_textual_ui.palette.ArgSpec`
  -driven collector for *any* leaf command that needs a value the user must
  supply (an id, a choice, a flag). It renders one field per needed argument
  and dismisses with the assembled ``argv`` for the app to run through the
  generic :meth:`~lot_textual_ui.lot_cli.LotCli.run_command` seam.
* :class:`CommandResultScreen` — a scrollable, read-only modal that shows a
  command's stdout (e.g. a ``thing updates`` thread or a resolved path).

All ``lot`` invocation stays inside :class:`~lot_textual_ui.lot_cli.LotCli`; a
form only collects fields and hands them to the app — via a typed ``LotCli``
method, or (for :class:`CommandFormScreen`) an assembled ``argv`` — then reports
the result back through its :class:`~textual.screen.Screen` ``dismiss`` value.
The app stays the sole executor of ``lot``.
"""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding, BindingsMap
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Checkbox, Input, Label, Select, TextArea

from .batch import ConfirmScreen
from .editor import RunEditor, edit_in_editor
from .mnemonics import assign_mnemonic
from .webmode import is_web_mode

if TYPE_CHECKING:
    from .palette import ArgSpec, LeafCommand

# The addressable id of the new-Thing body editor. The ``$EDITOR`` escape-hatch
# work item targets this widget to swap its contents for an editor round-trip,
# so it is a named constant rather than an inline string.
BODY_TEXTAREA_ID = "new-thing-body"

# The addressable id of the new-Update body editor — the sibling seam the
# ``$EDITOR`` escape-hatch work item targets on the Update form (mirrors
# :data:`BODY_TEXTAREA_ID`).
UPDATE_BODY_TEXTAREA_ID = "new-update-body"

# The addressable id of the preamble editor — the small YAML box the Update
# forms show *below* the body, carrying extra frontmatter for the update about
# to be written (see :func:`preamble_preview`). The new-Thing form has no such
# box: it collects a name and a body and nothing else.
UPDATE_PREAMBLE_TEXTAREA_ID = "new-update-preamble"

_EMPTY_NAME_MESSAGE = "A name is required."
_EMPTY_BODY_MESSAGE = "A body is required for this update type."

# The label above each preamble editor.
_PREAMBLE_LABEL = "Preamble (YAML)"


def preamble_preview(kind: str) -> str:
    """The text an Update form's preamble editor is seeded with.

    Shows the frontmatter ``lot`` will write for this update — ``status``, the
    ids, and the ``<kind>-at`` timestamp — as YAML **comments**, so the user can
    see what the preamble will be while the box still parses as "no extra
    fields". Those keys are precisely the ones ``lot`` manages and ``--preamble``
    rejects, so they must not be seeded as live YAML. Below them sits a commented
    example of the kind of field a user *may* add.

    ``kind`` is the update type being written (``work``, ``info``, …); every
    form that shows a preamble box is fixed to one type, so it is always known.

    Uncommenting nothing and submitting sends a comment-only document, which
    ``lot`` reads as an empty mapping — the no-op the untouched form wants.
    """
    return "\n".join(
        (
            "# lot writes these itself; they cannot be set here:",
            f"#   status: {kind}",
            f"#   task-id, update-id, {kind}-at",
            "#",
            "# Add your own fields below, e.g.:",
            "# claude-model: opus",
            "",
        )
    )


def preamble_argument(text: str) -> str | None:
    """The ``--preamble`` argument for ``text``, or ``None`` to omit the flag.

    A box holding nothing but blank lines and comments carries no fields, so the
    flag is dropped rather than passed as an empty document. (``lot`` would
    accept it either way — it reads a comment-only preamble as an empty mapping —
    but omitting it keeps the command line honest about what was asked for.)

    Anything else is passed through verbatim, comments and all, and it is ``lot``
    that validates it: a non-mapping or a reserved key comes back as a
    :class:`~lot_textual_ui.lot_cli.LotError` the form surfaces as a toast.
    """
    meaningful = [
        line
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return text if meaningful else None


# Shown by :class:`CommandFormScreen` when a required field is left blank. The
# ``{field}`` slot is filled with the argument's help text (its name as a
# fallback) so the message points at the offending field.
_EMPTY_FIELD_MESSAGE = "{field} is required."

# Shown by :class:`CommandResultScreen` when the command printed nothing (an
# empty but successful run), so the modal is never a blank void.
_NO_OUTPUT_MESSAGE = "(no output)"

# The discard-confirmation dialog shown when a user cancels a form they have
# already typed into (see :class:`_DiscardGuardMixin`). An *empty* form closes
# straight away — the confirm only guards content the user would lose. Kept as
# module constants so the copy is asserted in tests without duplicating it.
_DISCARD_TITLE = "Discard this form?"
_DISCARD_MESSAGE = "This form has unsaved content. Discard it?"
_DISCARD_CONFIRM_LABEL = "Discard"


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


class _DiscardGuardMixin:
    """Guard a form's cancel path with a confirm dialog when it has content.

    ``escape`` and the Cancel button/mnemonic both route through
    :meth:`action_cancel`. This mixin makes that path ask before throwing work
    away: a form the user has typed into raises a :class:`ConfirmScreen`, and
    only a positive answer actually closes the form; an *empty* form closes
    immediately, with no prompt (the confirm only exists to protect content).

    This is the second half of the reported data-loss fix — the first (in
    :mod:`lot_textual_ui.mnemonics`) stops a stray edit chord ever *reaching*
    cancel; this stops a *deliberate* cancel from silently discarding a
    half-written form.

    A subclass supplies :meth:`_has_content` (is there anything to lose?) and
    inherits :meth:`action_cancel`; the actual close is delegated to
    :meth:`_discard`, which defaults to ``dismiss(None)`` (what "close without
    saving" means for every current form).
    """

    def _has_content(self) -> bool:
        """Whether the form holds user input worth confirming before discard."""
        raise NotImplementedError

    def _discard(self) -> None:
        """Close the form without saving. Both forms dismiss with ``None``."""
        self.dismiss(None)  # type: ignore[attr-defined]

    def action_cancel(self) -> None:
        """Close the form, confirming first if the user has typed anything.

        An empty form is dismissed straight away. Otherwise a
        :class:`ConfirmScreen` is pushed and the form is only discarded if the
        user confirms — a stray ``escape``/Cancel cannot lose a filled-in form.
        """
        if not self._has_content():
            self._discard()
            return
        self.app.push_screen(  # type: ignore[attr-defined]
            ConfirmScreen(
                _DISCARD_MESSAGE,
                title=_DISCARD_TITLE,
                confirm_label=_DISCARD_CONFIRM_LABEL,
            ),
            self._discard_confirmed,
        )

    def _discard_confirmed(self, discard: bool | None) -> None:
        """Discard the form only if the confirm dialog came back positive."""
        if discard:
            self._discard()


class NewUpdateScreen(_DiscardGuardMixin, _BodyEditorMixin, ModalScreen[str | None]):
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
    the form without touching the vault — but a body the user has typed prompts
    a discard confirmation first (see :class:`_DiscardGuardMixin`); an empty
    form closes with no prompt.

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

    NewUpdateScreen #new-update-preamble {
        width: 1fr;
        height: 7;
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
        # Cancel picks its mnemonic *first* on every modal screen (see
        # :func:`assign_mnemonic`): that pins Cancel to the same chord
        # (``ctrl+l``) everywhere and stops the primary action ever colliding
        # with it. Add then takes whatever is left (``ctrl+d``).
        used_letters = set(_SCREEN_RESERVED_LETTERS)
        self._cancel_key, self._cancel_label = assign_mnemonic("Cancel", used_letters)
        self._add_key, self._add_label = assign_mnemonic("Add", used_letters)
        self._bindings.bind(self._cancel_key, "cancel", show=False, priority=True)
        self._bindings.bind(self._add_key, "submit", show=False, priority=True)

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
            yield Label(
                _PREAMBLE_LABEL,
                id="new-update-preamble-label",
                classes="new-update-field-label",
            )
            # Seeded with a commented preview of the frontmatter this update
            # will carry; the user edits it to add fields like `claude-model`.
            # It stays visible for bodyless marker types — a `done` marker can
            # carry preamble even though it carries no body.
            yield TextArea(preamble_preview(self._kind), id=UPDATE_PREAMBLE_TEXTAREA_ID)
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

    def _has_content(self) -> bool:
        """True if the body or preamble holds anything worth a discard prompt.

        The preamble box opens seeded with a commented preview, so it only
        counts once the user has added a real field (see
        :func:`preamble_argument`).
        """
        body = self.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text
        preamble = self.query_one(f"#{UPDATE_PREAMBLE_TEXTAREA_ID}", TextArea).text
        return bool(body.strip() or preamble_argument(preamble) is not None)

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
        preamble = self.query_one(f"#{UPDATE_PREAMBLE_TEXTAREA_ID}", TextArea).text
        self._create(kind, body if takes_body else None, preamble_argument(preamble))

    @work(exclusive=True, group="new-update-create")
    async def _create(self, kind: str, body: str | None, preamble: str | None) -> None:
        # Import here to avoid a module import cycle (app imports this module).
        from .lot_cli import LotError

        try:
            new_id = await self.app.lot_cli.add_update(
                kind, self._thing_id, body, preamble=preamble
            )
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
    field with its ``$EDITOR`` hatch, and the preamble box. Unlike it, it is a
    pure *collector* — submitting never touches the vault; it ``dismiss``\\es
    with the validated ``(kind, body, preamble)`` triple and the app applies
    that one Update to every marked Thing sequentially (with per-item error
    reporting), so one submit can stamp every marked Thing with, say, the same
    ``claude-model``. Cancelling dismisses with ``None``, exactly like the
    parent.

    Args:
        count: How many Things are marked; shown in the "On:" line so it is
            obvious the Update will land on all of them.
        kind: The (body-taking) update type chosen before the form opened.
    """

    def __init__(self, count: int, *, kind: str) -> None:
        label = f"{count} marked Thing{'s' if count != 1 else ''}"
        super().__init__(thing_id="", thing_label=label, kind=kind)

    def _create(  # type: ignore[override]
        self, kind: str, body: str | None, preamble: str | None
    ) -> None:
        """Dismiss with the collected fields; the app runs the batch."""
        self.dismiss((kind, body, preamble))


class CommandFormScreen(_DiscardGuardMixin, ModalScreen[list[str] | None]):
    """Reusable, ArgSpec-driven collector for a leaf command that needs input.

    Unlike the bespoke :class:`NewThingScreen` / :class:`NewUpdateScreen` (each
    wired to a typed :class:`~lot_textual_ui.lot_cli.LotCli` method with its own
    body editor), this screen is generic: given a
    :class:`~lot_textual_ui.palette.LeafCommand`, it renders one field per
    argument the user must supply — in ``command.args`` order — and, on submit,
    assembles the full ``lot`` argument vector and ``dismiss``\\es with it. It
    is a *pure collector*: it never runs ``lot`` itself. The app pushes it,
    receives the ``argv`` on dismiss, and runs it through the generic
    :meth:`~lot_textual_ui.lot_cli.LotCli.run_command` seam (the read-only
    commands show the result in a :class:`CommandResultScreen`).

    Which arguments become fields is driven by
    :attr:`~lot_textual_ui.palette.ArgSpec.needs_value_from_user`: only
    arguments the user *must* supply for a valid invocation are rendered (a
    defaulted option like ``thing get``'s ``--format`` is left off — the command
    runs with its default). Each rendered field's widget follows the arg's
    metadata:

    * ``possible_values`` non-empty → a :class:`~textual.widgets.Select`,
      pre-selected to ``default`` when the arg has one;
    * a boolean flag (``takes_value`` false) → a :class:`~textual.widgets.Checkbox`;
    * otherwise → a single-line :class:`~textual.widgets.Input`, prefilled from
      the ``prefill`` mapping when a value is passed for the arg (id fields).

    On submit the ``argv`` is ``list(command.path)`` followed by, per rendered
    field: a positional's raw value appended directly; an option's
    ``--<long> <value>`` (or bare ``--<long>`` for a set checkbox). Required /
    value-needing fields are validated non-empty in-form (a friendly message,
    no dismiss) exactly like the other modals. Cancelling (``escape`` / the
    Cancel button) dismisses with ``None`` — but if the user has changed any
    field from what the form opened with, a discard confirmation is shown first
    (see :class:`_DiscardGuardMixin`); an untouched form (including one that only
    carries prefilled ids) closes with no prompt.

    Args:
        command: The leaf command to collect arguments for. Its
            :attr:`~lot_textual_ui.palette.LeafCommand.args` drive the fields and
            its :attr:`~lot_textual_ui.palette.LeafCommand.path` heads the ``argv``.
        prefill: Optional seed values keyed by
            :attr:`~lot_textual_ui.palette.ArgSpec.name` — the app passes the
            in-view Thing/Update id for the ``thing``/``update`` positionals. A
            missing or ``None`` value leaves the field blank.
        title: The modal window title. Defaults to ``"lot <command label>"``.
    """

    DEFAULT_CSS = """
    CommandFormScreen {
        align: center middle;
    }

    CommandFormScreen > #command-form-dialog {
        width: 80%;
        max-width: 100;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    CommandFormScreen #command-form-title {
        text-style: bold;
        margin-bottom: 1;
    }

    CommandFormScreen #command-form-about {
        color: $text-muted;
        margin-bottom: 1;
    }

    CommandFormScreen .command-form-field-label {
        margin-top: 1;
        color: $text-muted;
    }

    CommandFormScreen .command-form-field {
        width: 1fr;
    }

    CommandFormScreen #command-form-error {
        color: $error;
        height: auto;
        margin-top: 1;
    }

    CommandFormScreen #command-form-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }

    CommandFormScreen #command-form-buttons Button {
        margin-left: 2;
    }
    """

    # Screen-local bindings only (app-level keys stay in keys.py). ``escape``
    # cancels; ``ctrl+s`` submits — mirroring the other modals, since a focused
    # Input/Select would otherwise swallow a plain Enter.
    BINDINGS = [
        Binding("escape", "cancel", "Cancel", show=True),
        Binding("ctrl+s", "submit", "Run", show=True),
    ]

    def __init__(
        self,
        command: LeafCommand,
        prefill: dict[str, str | None] | None = None,
        *,
        title: str | None = None,
    ) -> None:
        super().__init__()
        self._command = command
        self._prefill = dict(prefill or {})
        self._title = title if title is not None else f"lot {command.label}"
        # Only the arguments the user must supply become fields; defaulted
        # options (e.g. `thing get --format`) run on their default and are not
        # rendered. Order follows `command.args` so the form mirrors the CLI.
        self._fields: tuple[ArgSpec, ...] = tuple(
            arg for arg in command.args if arg.needs_value_from_user
        )
        # The field values the form opens with, captured on mount; the discard
        # guard compares against this so a form the user has not touched (only
        # prefilled ids) closes with no prompt. ``None`` until mounted.
        self._initial_values: dict[str, str] | None = None
        # Cancel picks its mnemonic *first* on every modal screen (see
        # :func:`assign_mnemonic`): that pins Cancel to the same chord
        # (``ctrl+l``) everywhere and stops the primary action ever colliding
        # with it. Run then takes whatever is left.
        used_letters = set(_SCREEN_RESERVED_LETTERS)
        self._cancel_key, self._cancel_label = assign_mnemonic("Cancel", used_letters)
        self._run_key, self._run_label = assign_mnemonic("Run", used_letters)
        self._bindings.bind(self._cancel_key, "cancel", show=False, priority=True)
        self._bindings.bind(self._run_key, "submit", show=False, priority=True)

    @staticmethod
    def _field_id(index: int) -> str:
        """The addressable id of the ``index``-th rendered field widget.

        Index-based (rather than name-based) so an argument name with spaces or
        punctuation can never produce an invalid CSS id.
        """
        return f"command-form-field-{index}"

    @staticmethod
    def _field_label(arg: ArgSpec) -> str:
        """The human label for ``arg`` — its help text, or its name as fallback."""
        return arg.help or arg.name

    def compose(self) -> ComposeResult:
        with Vertical(id="command-form-dialog"):
            yield Label(self._title, id="command-form-title")
            if self._command.about:
                yield Label(self._command.about, id="command-form-about")
            for index, arg in enumerate(self._fields):
                yield from self._compose_field(index, arg)
            yield Label("", id="command-form-error")
            with Horizontal(id="command-form-buttons"):
                yield Button(
                    self._cancel_label, variant="default", id="command-form-cancel"
                )
                yield Button(self._run_label, variant="primary", id="command-form-run")

    def _compose_field(self, index: int, arg: ArgSpec) -> ComposeResult:
        """Yield the widget(s) for one rendered argument, keyed by its metadata."""
        field_id = self._field_id(index)
        label = self._field_label(arg)
        if arg.possible_values:
            yield Label(label, classes="command-form-field-label")
            yield Select(
                [(value, value) for value in arg.possible_values],
                id=field_id,
                classes="command-form-field",
                value=arg.default if arg.default is not None else Select.BLANK,
                allow_blank=True,
            )
        elif not arg.takes_value:
            # A boolean flag: the checkbox carries its own label, so no
            # preceding Label is emitted.
            yield Checkbox(label, id=field_id, classes="command-form-field")
        else:
            yield Label(label, classes="command-form-field-label")
            prefill = self._prefill.get(arg.name)
            yield Input(
                value=prefill or "",
                placeholder=arg.name,
                id=field_id,
                classes="command-form-field",
            )

    def on_mount(self) -> None:
        # Snapshot the opening field values before any edit, so the discard
        # guard can tell a touched form from an untouched (prefill-only) one.
        self._initial_values = self._field_values()
        # Land the cursor in the first field so typing starts immediately.
        if self._fields:
            self.query_one(f"#{self._field_id(0)}").focus()

    # --- actions / events --------------------------------------------------

    @on(Button.Pressed, "#command-form-cancel")
    def _cancel_button(self) -> None:
        self.action_cancel()

    @on(Button.Pressed, "#command-form-run")
    def _run_button(self) -> None:
        self.action_submit()

    def _field_values(self) -> dict[str, str]:
        """A normalised ``{field_id: value}`` snapshot of every rendered field.

        Text (``Input``/``Select``) values are stripped so pure-whitespace edits
        do not read as content (matching the other forms' empty checks); a
        ``Checkbox`` contributes its boolean state. Used both to capture the
        opening state and to compare the current state in :meth:`_has_content`.
        """
        values: dict[str, str] = {}
        for index in range(len(self._fields)):
            field_id = self._field_id(index)
            widget = self.query_one(f"#{field_id}")
            if isinstance(widget, Checkbox):
                values[field_id] = str(widget.value)
            elif isinstance(widget, Select):
                values[field_id] = (
                    "" if widget.value is Select.BLANK else str(widget.value).strip()
                )
            else:
                assert isinstance(widget, Input)
                values[field_id] = widget.value.strip()
        return values

    def _has_content(self) -> bool:
        """True once any field differs from the value the form opened with."""
        if self._initial_values is None:
            return False
        return self._field_values() != self._initial_values

    def action_submit(self) -> None:
        """Validate the fields, assemble the ``argv``, and dismiss with it.

        Each rendered field contributes to ``list(self._command.path)``: a
        positional appends its raw value, an option appends ``--<long> <value>``
        (or a bare ``--<long>`` for a set checkbox). A required / value-needing
        field left blank is rejected in-form with a friendly message and no
        dismiss, so the collected input is not lost — mirroring the other modals.
        """
        error = self.query_one("#command-form-error", Label)
        argv = list(self._command.path)
        for index, arg in enumerate(self._fields):
            widget = self.query_one(f"#{self._field_id(index)}")
            if isinstance(widget, Checkbox):
                # A boolean flag: emit the bare `--long` only when it is set.
                if widget.value and arg.long is not None:
                    argv.append(f"--{arg.long}")
                continue
            if isinstance(widget, Select):
                value = "" if widget.value is Select.BLANK else str(widget.value)
            else:
                assert isinstance(widget, Input)
                value = widget.value.strip()
            if not value:
                error.update(_EMPTY_FIELD_MESSAGE.format(field=self._field_label(arg)))
                widget.focus()
                return
            if arg.long is not None:
                argv.extend((f"--{arg.long}", value))
            else:
                argv.append(value)
        error.update("")
        self.dismiss(argv)


class CommandResultScreen(ModalScreen[None]):
    """Scrollable, read-only modal that shows a command's stdout.

    The read half of the generic command flow: after a read-only leaf command
    (``thing get``, ``thing path``, ``thing updates``, ``update path``) runs
    through :meth:`~lot_textual_ui.lot_cli.LotCli.run_command`, the app pushes
    this screen with the captured output. The text sits in a read-only,
    selectable :class:`~textual.widgets.TextArea` inside the modal, so a long
    result (a big ``thing updates`` thread) scrolls rather than overflowing, and
    the user can select/copy from it. An empty result still shows the modal (with
    a ``(no output)`` note) rather than nothing. ``escape`` / the Close button
    dismisses; this screen never touches the vault.

    Args:
        title: The heading shown above the output — the app passes
            ``"lot <command label>"`` so it is clear which command produced it.
        output: The command's stdout to display verbatim.
    """

    DEFAULT_CSS = """
    CommandResultScreen {
        align: center middle;
    }

    CommandResultScreen > #command-result-dialog {
        width: 80%;
        max-width: 120;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $panel-lighten-2;
        background: $surface;
    }

    CommandResultScreen #command-result-title {
        text-style: bold;
        margin-bottom: 1;
    }

    CommandResultScreen #command-result-output {
        width: 1fr;
        height: auto;
        max-height: 20;
    }

    CommandResultScreen #command-result-buttons {
        height: auto;
        margin-top: 1;
        align-horizontal: right;
    }
    """

    # Screen-local bindings only. ``escape`` closes; a mnemonic chord (assigned
    # below) is bound to the same action for parity with the other modals.
    BINDINGS = [
        Binding("escape", "close", "Close", show=True),
    ]

    def __init__(self, title: str, output: str) -> None:
        super().__init__()
        self._title = title
        self._output = output
        used_letters = set(_SCREEN_RESERVED_LETTERS)
        self._close_key, self._close_label = assign_mnemonic("Close", used_letters)
        self._bindings.bind(self._close_key, "close", show=False, priority=True)

    def compose(self) -> ComposeResult:
        with Vertical(id="command-result-dialog"):
            yield Label(self._title, id="command-result-title")
            yield TextArea(
                self._output if self._output else _NO_OUTPUT_MESSAGE,
                read_only=True,
                soft_wrap=True,
                id="command-result-output",
            )
            with Horizontal(id="command-result-buttons"):
                yield Button(
                    self._close_label, variant="primary", id="command-result-close"
                )

    def on_mount(self) -> None:
        # Focus the output so the result scrolls / is selectable straight away.
        self.query_one("#command-result-output", TextArea).focus()

    @on(Button.Pressed, "#command-result-close")
    def _close_button(self) -> None:
        self.action_close()

    def action_close(self) -> None:
        """Close the result modal."""
        self.dismiss(None)
