"""The command navigator, palette dispatch and form entry points (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from functools import partial

from textual import events, work

from .batch import TOP_LEVEL, ConfirmScreen, ThingPickerScreen
from .command_nav import RESERVED_CTRL_LETTERS, CommandNav, CommandNavScreen
from .detail import DetailPane, InlineNewThingForm, InlineUpdateForm
from .forms import (
    CommandFormScreen,
    CommandResultScreen,
)
from .lot_cli import LotError
from .palette import LeafCommand

# The ``lot`` leaf commands classified as **read-only**: they inspect the vault
# and print a result but never mutate it. They route through the generic
# :class:`~lot_textual_ui.forms.CommandFormScreen` collector and show their
# stdout in a :class:`~lot_textual_ui.forms.CommandResultScreen` — and, being
# read-only, they never trigger a vault reload afterwards. Kept as an explicit,
# documented classification (rather than inferred) so mutation commands, which
# get their own bespoke branches and *do* reload, are never accidentally run
# blind through the read-only path.
_READ_ONLY_COMMANDS: frozenset[tuple[str, ...]] = frozenset(
    {
        ("thing", "get"),
        ("thing", "path"),
        ("thing", "updates"),
        ("update", "path"),
    }
)


class CommandsMixin:
    """Running ``lot`` commands from the navigator, the palette, and keys.

    Two fronts feed one seam. The hierarchical command navigator
    (:mod:`lot_textual_ui.command_nav`): ``space`` opens it at the top level
    of the discovered ``lot`` command tree, and ``ctrl+<first letter of a
    top-level command>`` opens it already inside that command (``ctrl+t`` →
    ``lot thing``, then ``n`` runs ``lot thing new``). The fuzzy palette
    (``ctrl+p``, Textual's native one, fed by
    :mod:`lot_textual_ui.palette`). A picked leaf runs through
    :meth:`run_lot_command` either way, which dispatches input-needing
    commands to their forms (new Thing, new Update, send to Claude) and runs
    the rest directly.

    Also home to ``check_action``'s modal gating: base-screen-only actions
    (see ``_BASE_SCREEN_ACTIONS``) are disabled while any screen is pushed —
    and its column-context gating, which hides the fold/copy-selection and
    copy-Thing hints (see ``_DETAIL_COLUMN_ACTIONS``/``_CURRENT_THING_ACTIONS``)
    from the footer unless their context is actually active.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it uses the app's shared ``self._lot_cli``, the ``_help_tree``
    cache, the ``_require_current_thing`` guard and the reload path.
    """

    # App actions that only make sense on the base (browser) screen. Their
    # non-priority bindings would otherwise still fire while a modal is up
    # (whenever the modal's focused widget doesn't consume the key), so
    # ``check_action`` disables them there — a stray ``d`` in a picker must
    # not queue a batch archive behind the modal, and a stray ``n`` must not
    # stack a new-Thing form on top of it. Every action that mutates state or
    # opens a screen is listed; pure navigation (``cursor_*``/``focus_*``) and
    # ``quit`` stay live.
    _BASE_SCREEN_ACTIONS = frozenset(
        {
            "toggle_mark",
            "toggle_mark_siblings",
            "clear_marks",
            "batch_move",
            "batch_archive",
            "batch_update",
            "new_thing",
            "new_child_thing",
            "copy_thing_uri",
            "copy_thing_path",
            "copy_selection",
            "toggle_update",
        }
    )

    # Actions whose footer hint (and, per ``check_action``'s contract, the key
    # itself) only make sense while the detail/updates column holds focus. All
    # four belong to that column: ``toggle_update`` (fold/unfold) acts on
    # whichever update is focused; ``copy_selection`` copies a mouse
    # text-selection that only spans the detail pane's own widgets; and
    # ``copy_thing_uri``/``copy_thing_path`` copy the in-view Thing's id/path —
    # kept in this same column so all three copy hints (and fold) surface
    # together, only in the updates column, never from the trees.
    _DETAIL_COLUMN_ACTIONS = frozenset(
        {"toggle_update", "copy_selection", "copy_thing_uri", "copy_thing_path"}
    )

    # A further guard on the copy-Thing actions: even in the detail column the
    # pane can be empty (nothing selected), and there is nothing to copy then,
    # so their hint is additionally suppressed unless a Thing is actually in
    # view (:attr:`~lot_textual_ui.app.LotTextualApp.current_thing_id`).
    _CURRENT_THING_ACTIONS = frozenset({"copy_thing_uri", "copy_thing_path"})

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate base-screen-only actions while any modal screen is on top.

        ``command_nav``'s binding is ``priority=True`` so it beats the focused
        :class:`~textual.widgets.Tree`'s own space-to-toggle — but priority
        bindings also fire while a modal (a form with text inputs, the
        navigator itself) is on top, where a typed space must stay a space. So
        the action is disabled whenever any screen is pushed. The multi-select
        and batch actions are gated the same way (see
        :data:`_BASE_SCREEN_ACTIONS`).

        Beyond the modal gate, a few actions are also context-gated so their
        footer hint (and the key itself) only appears while relevant —
        returning ``False`` here makes :attr:`Screen.active_bindings` both
        disable the action *and* drop it from the footer entirely (not just
        dim it), which is exactly the "hide unless active" behaviour wanted:
        fold and all three copy hints (:data:`_DETAIL_COLUMN_ACTIONS` —
        ``toggle_update``/``copy_selection``/``copy_thing_uri``/
        ``copy_thing_path``) stay hidden unless the detail/updates column holds
        focus, and the copy-Thing pair is additionally hidden while no Thing is
        in view (:data:`_CURRENT_THING_ACTIONS`). Every other action passes
        through untouched.
        """
        if len(self.screen_stack) > 1 and (
            action == "command_nav" or action in self._BASE_SCREEN_ACTIONS
        ):
            return False
        # An inline form (e.g. the new-Update form in the detail pane) is a plain
        # widget, not a pushed screen, so ``screen_stack`` stays 1 while one is
        # open. Gate the same base-screen actions off so a ``space`` typed into
        # its body editor stays a space (``command_nav`` is priority-bound) and a
        # stray ``n``/``d`` cannot fire while the user is filling the form in.
        if self._inline_form_open() and (
            action == "command_nav" or action in self._BASE_SCREEN_ACTIONS
        ):
            return False
        if action in self._DETAIL_COLUMN_ACTIONS and not self._detail_column_focused():
            return False
        if action in self._CURRENT_THING_ACTIONS and self.current_thing_id is None:
            return False
        return super().check_action(action, parameters)

    def action_command_nav(self) -> None:
        """Open the command navigator at the top level (the ``space`` leader)."""
        self._open_command_nav(None)

    def on_key(self, event: events.Key) -> None:
        """Treat ``ctrl+<letter>`` as a shortcut into a top-level command.

        Handled here rather than as bindings because the shortcut set is
        derived at runtime from the discovered command tree's first letters.
        Only unclaimed keys reach this handler, and the reserved set
        (:data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS`) keeps
        quit/palette/suspend combinations out; a letter matching no top-level
        command does nothing (see :meth:`_open_command_nav`).
        """
        key = event.key
        if not (key.startswith("ctrl+") and len(key) == 6 and key[5].isalpha()):
            return
        if (
            key[5] in RESERVED_CTRL_LETTERS
            or len(self.screen_stack) > 1
            or self._inline_form_open()
        ):
            return
        event.stop()
        self._open_command_nav(key[5])

    @work(exclusive=True, group="command-nav")
    async def _open_command_nav(self, letter: str | None) -> None:
        """Open the navigator, optionally pre-navigated by a shortcut letter.

        Discovers (and caches) the command tree first. A ``letter`` behaves
        exactly as if typed with the navigator open: a unique top-level match
        lands inside that command — a leaf runs immediately, without the
        navigator ever showing — a first-letter collision opens it with the
        chooser up, and a letter matching no top-level command does nothing.
        """
        if self._help_tree is None:
            try:
                self._help_tree = await self._lot_cli.help_yaml()
            except LotError as error:
                self.notify(str(error), title="Commands", severity="error")
                return
        nav = CommandNav(self._help_tree)
        if letter is not None:
            outcome = nav.on_letter(letter)
            if isinstance(outcome, LeafCommand):
                self.run_lot_command(outcome)
                return
            if not nav.path and nav.chooser is None:
                return
        self.push_screen(CommandNavScreen(nav), self._command_nav_done)

    def _command_nav_done(self, command: LeafCommand | None) -> None:
        """Run the navigator's pick (``None`` = cancelled) via the forms seam."""
        if command is not None:
            self.run_lot_command(command)

    # --- command palette ---------------------------------------------------
    #
    # The palette (``ctrl+p``) is Textual's native fuzzy palette, fed by the
    # providers in :mod:`lot_textual_ui.palette`. Two entry points land here:
    # picking a ``lot`` leaf command calls :meth:`run_lot_command`, and the
    # internal "Refresh vault" command calls :meth:`action_refresh_vault`.

    def run_lot_command(self, command: LeafCommand) -> None:
        """Run a ``lot`` leaf command chosen in the palette.

        .. _run-lot-command-seam:

        **Forms seam** (see :ref:`lot_textual_ui.palette <palette-forms-seam>`).
        A ``lot`` leaf command falls into one of two buckets:

        * **No input needed** (``command.needs_input`` is ``False`` — every
          argument is optional and defaulted): the command is run as-is through
          the shared :class:`LotCli` and the vault view is refreshed.
        * **Input needed** (a required positional, a value-taking flag, content
          on stdin, …): dispatch on ``command.path`` to the matching handler.
          ``("thing", "new")`` opens :meth:`open_new_thing_form`;
          ``("update", <type>)`` — for any *creatable* update type in the
          loaded config, custom types included (see
          :meth:`creatable_update_types`) — is **type-specific**: a
          body-taking type opens :meth:`open_new_update_form` fixed to it,
          while a bodyless type (``done``-likes) runs straight away on the
          in-view Thing via :meth:`add_bodyless_update`, no form at all;
          ``("claude", "send", <model>)`` launches a background Claude session
          on the in-view Thing via :meth:`send_to_claude` (its only argument,
          the Thing, is the one the user is looking at), and
          ``("claude", "coordinate", <model>, <skill>)`` likewise launches a
          coordinator session on it via :meth:`coordinate_with_claude` (the
          model and workflow are already fixed by the chosen leaf);
          ``("settings", "set", "theme")`` opens the theme picker
          (:meth:`action_switch_theme`), whose selection both sets and persists
          the theme — what the command does (the fuzzy palette hides this leaf,
          since the *Switch theme* internal command already offers it — see
          :data:`~lot_textual_ui.palette.PALETTE_DUPLICATE_LEAVES` — so it now
          arrives here only via the command navigator); ``("thing", "move")``
          and ``("thing", "archive")`` act on the in-view Thing through their own
          bespoke modals — a destination
          :class:`~lot_textual_ui.batch.ThingPickerScreen` (:meth:`move_thing`)
          and a destructive-confirm :class:`~lot_textual_ui.batch.ConfirmScreen`
          (:meth:`archive_thing`) — rather than the generic form, since a
          mutually-exclusive parent/root destination and an irreversible archive
          are far better served by a picker and a confirmation than by raw text
          fields; a **read-only**
          command (:data:`_READ_ONLY_COMMANDS` — ``thing get``/``path``/``updates``
          and ``update path``) opens the generic
          :class:`~lot_textual_ui.forms.CommandFormScreen` collector, runs the
          assembled ``argv``, and shows the stdout in a
          :class:`~lot_textual_ui.forms.CommandResultScreen`
          (:meth:`open_command_form`); ``("vault", "new")`` collects a ``<path>``
          through the same generic form and creates a new vault on disk
          (:meth:`create_vault`). Every input-needing leaf command now has a
          handler, so the placeholder toast below is effectively unreachable — it
          stays only as a defensive fallback for any command added later without
          one.
        """
        if command.needs_input:
            if command.path == ("thing", "new"):
                self.open_new_thing_form()
                return
            if command.path[:1] == ("update",) and len(command.path) == 2:
                update_type = next(
                    (
                        t
                        for t in self.creatable_update_types()
                        if t.name == command.path[1]
                    ),
                    None,
                )
                # Only real update types — `update path` is a leaf here too.
                if update_type is not None:
                    if update_type.takes_body:
                        self.open_new_update_form(kind=update_type.name)
                    else:
                        self.add_bodyless_update(update_type.name)
                    return
            if command.path[:2] == ("claude", "send") and len(command.path) == 3:
                self.send_to_claude(command.path[2])
                return
            if command.path[:2] == ("claude", "coordinate") and len(command.path) == 4:
                self.coordinate_with_claude(command.path[2], command.path[3])
                return
            if command.path == ("settings", "set", "theme"):
                # `settings set theme <name>` needs a theme name; the theme
                # picker *is* its form — and it already applies the choice live
                # and persists it (see :meth:`action_switch_theme`), which is
                # exactly what the command does, so route it there instead of a
                # dead-end "no form" toast.
                self.action_switch_theme()
                return
            if command.path == ("thing", "move"):
                # Deliberate deviation from the generic CommandFormScreen: the
                # destination is a mutually-exclusive `--parent <id>` / `--root`
                # choice over the vault tree, which a Thing *picker* expresses far
                # better than two raw text fields — so `thing move` reuses the
                # existing batch-move picker rather than routing through the
                # generic form (and, as a mutation, gets its own reload+toast).
                self.move_thing()
                return
            if command.path == ("thing", "archive"):
                # Deliberate deviation from the generic CommandFormScreen: archive
                # is destructive (it removes the Thing and its whole subtree from
                # the working tree), so it wants a confirmation dialog, not a
                # fire-on-submit form — it reuses the batch-archive ConfirmScreen
                # (and, as a mutation, gets its own reload+toast).
                self.archive_thing()
                return
            if command.path == ("vault", "new"):
                self.create_vault(command)
                return
            if command.path in _READ_ONLY_COMMANDS:
                self.open_command_form(command)
                return
            self.notify(
                f"'lot {command.label}' needs input — a form for it is coming "
                "in a later phase.",
                title="Not available yet",
                severity="warning",
            )
            return
        self._run_leaf_command(command)

    def open_new_thing_form(
        self, parent_id: str | None = None, title: str = "New Thing"
    ) -> None:
        """Open the new-Thing form inline, over the detail pane.

        The reusable entry point for creating a Thing: the palette's ``thing
        new`` leaf calls it with no arguments (a top-level Thing), and the
        create-child action calls it with ``parent_id`` set (and a fitting
        ``title``) to seed the parent.

        Rather than a centred modal, the form is an
        :class:`~lot_textual_ui.detail.InlineNewThingForm` mounted in the detail
        column, hiding the update thread while open — the new Thing lands in that
        pane once the selection jumps to it. It collects and validates the
        fields, then hands them back through :meth:`submit_inline_new_thing`
        (success) or :meth:`close_inline_new_thing_form` (cancel).
        """
        self.close_inline_new_thing_form()
        parent = self.thing_by_id(parent_id) if parent_id is not None else None
        form = InlineNewThingForm(
            parent_id=parent_id,
            parent_label=parent.name if parent is not None else None,
            title=title,
        )
        # Cover the detail pane with the form: the pane is restored on close.
        self.query_one(DetailPane).display = False
        self.query_one("#detail").mount(form)

    def submit_inline_new_thing(
        self,
        form: InlineNewThingForm,
        name: str,
        body: str,
        send: bool,
    ) -> None:
        """Create the inline form's Thing, then close it and jump to the Thing.

        The submit hook the :class:`~lot_textual_ui.detail.InlineNewThingForm`
        calls once it has a validated non-empty name. Runs in a worker so the
        ``lot`` subprocess never blocks the event loop; on a CLI failure
        (:class:`LotError`) it toasts and re-enables the form (via
        :meth:`~lot_textual_ui.detail.InlineNewThingForm.submit_failed`) so the
        input is not lost. On success the form is removed, the vault reloaded, and
        the selection jumped to the new Thing (opening the Claude stage when
        ``send``) — see :meth:`_new_thing_created`.
        """
        self._submit_inline_new_thing(form, name, body, send)

    @work(exclusive=False, group="new-thing-create")
    async def _submit_inline_new_thing(
        self,
        form: InlineNewThingForm,
        name: str,
        body: str,
        send: bool,
    ) -> None:
        try:
            new_id = await self._lot_cli.thing_new(name, body, parent=form.parent_id)
        except LotError as error:
            self.notify(str(error), title="Could not create Thing", severity="error")
            form.submit_failed()
            return
        self.close_inline_new_thing_form()
        await self._new_thing_created(new_id, send)

    def close_inline_new_thing_form(self) -> None:
        """Tear down the inline new-Thing form and restore the detail pane.

        The discard-guard's close hook, and the success path's teardown. Removes
        the form if one is mounted and un-hides the
        :class:`~lot_textual_ui.detail.DetailPane` it was covering (a no-op when
        no form is open).
        """
        forms = self.query(InlineNewThingForm)
        if not forms:
            return
        forms.first().remove()
        detail = self.query_one(DetailPane)
        detail.display = True
        detail.focus()

    def action_new_thing(self) -> None:
        """Create a new top-level Thing (keyboard/palette entry point).

        A thin wrapper over :meth:`open_new_thing_form` with no parent, giving
        the top-level create a first-class app action (bound in
        :mod:`lot_textual_ui.keys`) alongside the ``thing new`` palette leaf.
        """
        self.open_new_thing_form()

    def action_new_child_thing(self) -> None:
        """Create a new Thing as a child of the in-view Thing.

        Seeds :meth:`open_new_thing_form` with the in-view Thing's id (the centre
        column's active item) as the parent, so the created Thing lands under the
        Thing the user is looking at (and the reload path jumps the selection to
        the new child, which the centre column then shows). With nothing selected
        there is no parent to hang it under, so it notifies and does nothing
        rather than opening a form that would create a stray root.
        """
        parent_id = self._require_current_thing(
            "Select a Thing first to add a child to it.", title="No Thing selected"
        )
        if parent_id is None:
            return
        self.open_new_thing_form(parent_id=parent_id, title="New child Thing")

    async def _new_thing_created(self, new_id: str, send: bool) -> None:
        """Reload the vault and jump the view to a freshly created Thing.

        Called from the create worker with the new Thing's id. The vault is
        reloaded first (the live ``lot watch`` stream would bring the node in
        eventually, but a reload avoids the race) and only then is the view
        moved, so the target id is already in the index. If the node is somehow
        still unknown the move is skipped rather than selecting a phantom id.

        A new top-level Thing is a root, so it becomes the left selection
        directly. A new child is a leaf, which the left tree does not show (only
        roots and branches); its parent — now a branch — becomes the left
        selection, rooting the centre column there, and the new child is made the
        centre's active item so it is highlighted and shown in the detail pane.

        Finally, when the form was submitted with **Create and send** (``send``),
        the Claude stage is opened on the new Thing — the selection now points at
        it, so the command navigator (and the ``claude send`` it leads to)
        targets the right Thing.
        """
        await self._reload_vault()
        if new_id not in self._index.by_id:
            return
        container = self._index.left_visible_id(new_id)
        # Assigning selected_id fires watch_selected_id (re-rooting the centre at
        # the container and resetting active_id); a same-id no-op leaves the
        # already-current centre in place. Either way, point the active item at
        # the new Thing so the centre highlights it and the detail pane shows it.
        self.selected_id = container
        self.active_id = new_id
        if send:
            self._open_claude_stage()

    def _open_claude_stage(self) -> None:
        """Open the command navigator parked at the ``claude`` command.

        The **Create and send** follow-up. Rather than firing ``claude send``
        blind, it drops the user *inside* the ``claude`` command — the same
        place typing ``c`` in the navigator lands — so they pick how to hand the
        Thing to Claude (today ``send`` and its model; other ``claude`` actions
        later). The navigator acts on the in-view Thing, which
        :meth:`_new_thing_created` has just pointed at the new Thing.
        """
        self._open_command_nav("c")

    def open_new_update_form(self, kind: str, thing_id: str | None = None) -> None:
        """Open the type-fixed new-Update form inline, at the foot of the thread.

        The reusable entry point for adding a **body-taking** Update. Each
        ``update <type>`` leaf (palette or command navigator) — one per
        configured type — calls it with its own ``kind`` and no
        ``thing_id``, so it defaults to the in-view Thing
        (:attr:`current_thing_id`, the centre column's active item) — "add an
        update" almost always means "to the Thing I'm looking at" on the
        right. Other flows may pass an explicit ``thing_id``. With no target
        available (nothing selected and no id given) it notifies and does
        nothing rather than opening a form that cannot submit. Bodyless types
        never come here — :meth:`add_bodyless_update` runs them without a
        form.

        Rather than a centred modal, the form is an
        :class:`~lot_textual_ui.detail.InlineUpdateForm` mounted at the bottom of
        the detail pane's update thread — where the new Update will land. It
        collects and validates the body, then hands it back through
        :meth:`submit_inline_update` (success) or
        :meth:`close_inline_update_form` (cancel).
        """
        target = (
            thing_id
            if thing_id is not None
            else self._require_current_thing(
                "Select a Thing first to add an update to it.",
                title="No Thing selected",
            )
        )
        if target is None:
            return
        thing = self.thing_by_id(target)
        self.query_one(DetailPane).open_update_form(
            kind=kind,
            thing_id=target,
            thing_label=thing.name if thing is not None else target,
        )

    def submit_inline_update(
        self, form: InlineUpdateForm, body: str, preamble: str | None = None
    ) -> None:
        """Append the inline form's Update, then close it and reload.

        The submit hook the :class:`~lot_textual_ui.detail.InlineUpdateForm`
        calls once it has a validated non-empty body. Runs in a worker so the
        ``lot`` subprocess never blocks the event loop; on a CLI failure
        (:class:`LotError`) it toasts and re-enables the form (via
        :meth:`~lot_textual_ui.detail.InlineUpdateForm.submit_failed`) so the
        input is not lost. On success the form is removed and the vault reloaded,
        so :meth:`_reload_vault` re-renders the thread with the new Update.

        ``preamble`` is the form's YAML box, already reduced to ``None`` when the
        user left it carrying no fields.
        """
        self._submit_inline_update(form, body, preamble)

    @work(exclusive=True, group="new-update-create")
    async def _submit_inline_update(
        self, form: InlineUpdateForm, body: str, preamble: str | None
    ) -> None:
        try:
            await self._lot_cli.add_update(form.kind, form.thing_id, body, preamble)
        except LotError as error:
            self.notify(str(error), title="Could not add Update", severity="error")
            form.submit_failed()
            return
        self.query_one(DetailPane).close_update_form(refocus=True)
        await self._reload_vault()

    def close_inline_update_form(self) -> None:
        """Tear down the inline new-Update form (the discard-guard's close hook)."""
        self.query_one(DetailPane).close_update_form(refocus=True)

    def _inline_form_open(self) -> bool:
        """Whether an inline form is mounted on the base screen.

        Inline forms (today the new-Update form) carry the ``inline-form`` CSS
        class as a marker; :meth:`check_action` and the ``ctrl+<letter>``
        shortcut handler gate the app's own single-key bindings while one is open,
        the same way ``screen_stack`` gates them while a modal is up.
        """
        return bool(self.query(".inline-form"))

    def add_bodyless_update(self, kind: str) -> None:
        """Append a bodyless Update (``done``-likes) to the in-view Thing.

        A bodyless type carries nothing but its marker, so there is no form to
        fill in: picking ``update done`` (palette, or ``ctrl+u`` ``d`` in the
        command navigator) — or any custom ``takes-body = false`` type — lands
        here and runs ``lot update <kind>`` straight away on the in-view Thing
        (:attr:`current_thing_id`). With nothing selected it notifies and does
        nothing.
        """
        target = self._require_current_thing(
            "Select a Thing first to add an update to it.", title="No Thing selected"
        )
        if target is None:
            return
        thing = self.thing_by_id(target)
        label = thing.name if thing is not None else target
        self._add_bodyless_update(kind, target, label)

    @work(exclusive=False, group="new-update-reload")
    async def _add_bodyless_update(self, kind: str, thing_id: str, label: str) -> None:
        """Run the bodyless ``lot update`` and refresh; toast either outcome.

        A success is toasted (there was no form, so the toast is the only
        feedback that the key press landed) and the vault reloaded so the
        Thing's status marker repaints; a failure surfaces the CLI's error.
        """
        try:
            await self._lot_cli.add_update(kind, thing_id, None)
        except LotError as error:
            self.notify(str(error), title="Could not add Update", severity="error")
            return
        self.notify(f"{kind} recorded on {label}.", title="Update added")
        await self._reload_vault()

    # --- send to Claude ----------------------------------------------------
    #
    # The ``claude send <model>`` leaves (sonnet/opus/fable) launch a background
    # ``claude`` session working on a Thing via ``lot claude send`` (readme §5.3).
    # Their only argument is the Thing, so — like the update actions — they act
    # on the Thing the user is looking at (:attr:`current_thing_id`) rather than
    # opening a form; the id is passed explicitly so the CLI never falls back to
    # ``LOT_THING_ID``.

    def send_to_claude(self, model: str) -> None:
        """Send the in-view Thing to a background Claude session (palette/nav).

        Backs the ``claude send <model>`` command leaves. ``model`` is the model
        sub-command (``sonnet``/``opus``/``fable``). Targets the centre column's
        active item (:attr:`current_thing_id`) — "send this Thing" almost always
        means the one on the right — passing its id explicitly. With nothing
        selected there is no Thing to send, so it notifies and does nothing.
        """
        target = self._require_current_thing(
            "Select a Thing first to send it to Claude.", title="No Thing selected"
        )
        if target is None:
            return
        self._send_to_claude(model, target)

    @work(exclusive=False, group="claude-send")
    async def _send_to_claude(self, model: str, thing_id: str) -> None:
        """Run ``lot claude send`` in a worker, then reload so the launch shows.

        Kept off the event loop because ``lot claude send`` spawns the ``claude``
        CLI; a failure (e.g. ``claude`` not installed) surfaces as an error toast
        rather than crashing. On success the vault is reloaded so the ``work``
        update the CLI records for the launch appears in the detail pane without
        waiting for the live ``lot watch`` stream.
        """
        try:
            await self._lot_cli.claude_send(model, thing_id)
        except LotError as error:
            self.notify(str(error), title="Send to Claude failed", severity="error")
            return
        await self._reload_vault()
        self.notify(
            f"Launched a background Claude session (model: {model}).",
            title="Sent to Claude",
        )

    # --- coordinate with Claude --------------------------------------------
    #
    # The ``claude coordinate <model> <skill>`` leaves launch a *coordinator*
    # session: one that drives the in-view Thing's subtree of child Things
    # across worker sessions. The extra level over ``claude send`` is the
    # workflow — ``decide`` (plan, then hand back for sign-off), ``plan`` (plan
    # and execute autonomously), or ``act`` (execute an existing plan) — which
    # the CLI exposes as sub-commands, so the leaf the user picked has already
    # chosen it and there is nothing left to collect but the Thing.

    def coordinate_with_claude(self, model: str, skill: str) -> None:
        """Coordinate the in-view Thing in a background Claude session.

        Backs the ``claude coordinate <model> <skill>`` command leaves, mirroring
        :meth:`send_to_claude`: the Thing is the centre column's active item
        (:attr:`current_thing_id`), passed explicitly so the CLI never falls back
        to ``LOT_THING_ID``. With nothing selected it notifies and does nothing.
        """
        target = self._require_current_thing(
            "Select a Thing first to coordinate it.", title="No Thing selected"
        )
        if target is None:
            return
        self._coordinate_with_claude(model, skill, target)

    @work(exclusive=False, group="claude-coordinate")
    async def _coordinate_with_claude(
        self, model: str, skill: str, thing_id: str
    ) -> None:
        """Run ``lot claude coordinate`` in a worker, then reload so the launch shows.

        Off the event loop for the same reason as :meth:`_send_to_claude`: the
        CLI spawns the ``claude`` binary, and a failure (e.g. ``claude`` not
        installed) should surface as an error toast rather than crash the UI.
        """
        try:
            await self._lot_cli.claude_coordinate(model, skill, thing_id)
        except LotError as error:
            self.notify(
                str(error), title="Coordinate with Claude failed", severity="error"
            )
            return
        await self._reload_vault()
        self.notify(
            f"Launched a background Claude coordinator (model: {model}, "
            f"workflow: {skill}).",
            title="Coordinating with Claude",
        )

    # --- generic read-only command form -----------------------------------
    #
    # The read-only leaf commands (:data:`_READ_ONLY_COMMANDS`) reuse the
    # ArgSpec-driven :class:`~lot_textual_ui.forms.CommandFormScreen` collector:
    # it renders a field per argument the user must supply, and dismisses with an
    # assembled ``argv`` the app runs through the generic ``run_command`` seam,
    # showing the stdout in a :class:`~lot_textual_ui.forms.CommandResultScreen`.
    # Being read-only, none of them reload the vault.

    def open_command_form(self, command: LeafCommand) -> None:
        """Push the generic form for a read-only leaf command, then show its output.

        The entry point for :data:`_READ_ONLY_COMMANDS`. The form's fields are
        prefilled from the in-view context (:meth:`_command_form_prefill`) — the
        Thing/Update the user is looking at — so the common case is one keypress
        away. The :class:`~lot_textual_ui.forms.CommandFormScreen` dismisses with
        the assembled ``argv`` (or ``None`` on cancel); :meth:`_command_form_done`
        runs it and surfaces the result.
        """
        self.push_screen(
            CommandFormScreen(command, self._command_form_prefill(command)),
            partial(self._command_form_done, command),
        )

    def _command_form_prefill(self, command: LeafCommand) -> dict[str, str | None]:
        """Seed the generic form's id fields from the in-view Thing/Update.

        The ``thing`` positional is prefilled with the centre column's active
        Thing (:attr:`current_thing_id`) and the ``update`` positional with the
        detail pane's current Update id — exactly the ids the clipboard actions
        use — so a read-only lookup targets what the user is looking at by
        default. A ``None`` (nothing in view) simply leaves the field blank for
        the user to type or paste an id.
        """
        prefill: dict[str, str | None] = {}
        for arg in command.args:
            if arg.long is not None:
                continue  # only positional id fields are seeded from context
            if arg.name == "thing":
                prefill[arg.name] = self.current_thing_id
            elif arg.name == "update":
                prefill[arg.name] = self.query_one(DetailPane).current_update_id
        return prefill

    def _command_form_done(self, command: LeafCommand, argv: list[str] | None) -> None:
        """Run the collected ``argv`` (``None`` = cancelled) and show the result."""
        if argv is None:
            return
        self._run_read_only_command(command, argv)

    @work(exclusive=False, group="command-form-run")
    async def _run_read_only_command(
        self, command: LeafCommand, argv: list[str]
    ) -> None:
        """Run a read-only ``lot`` command and show its stdout in a modal.

        Kept in a background worker so the ``lot`` subprocess never blocks the
        event loop; a failure surfaces as an error toast. On success the stdout
        is shown in a :class:`~lot_textual_ui.forms.CommandResultScreen`. The
        command is read-only, so the vault is deliberately *not* reloaded.
        """
        try:
            output = await self._lot_cli.run_command(*argv)
        except LotError as error:
            self.notify(str(error), title="Command failed", severity="error")
            return
        self.push_screen(CommandResultScreen(f"lot {command.label}", output))

    # --- create a new vault -----------------------------------------------
    #
    # ``vault new`` takes a single required ``<path>`` and creates a fresh LoT
    # vault on disk there. It reuses the generic CommandFormScreen (one required
    # text field, no prefill — there is no in-view path), but not the read-only
    # result flow: on success it toasts rather than showing stdout.

    def create_vault(self, command: LeafCommand) -> None:
        """Collect a ``<path>`` and create a new vault on disk there.

        Backs the ``vault new`` leaf. Pushes the generic
        :class:`~lot_textual_ui.forms.CommandFormScreen` (no prefill — a new
        vault's path is unrelated to anything in view), which renders and
        validates the single required ``path`` field; on submit
        :meth:`_create_vault_done` runs ``lot vault new <path>``.

        **Design decision — deliberately minimal.** This only *creates* the vault
        on disk; it does **not** register the new vault in the user config and
        does **not** switch/reload the running UI to it. Switching vaults is a
        separate, explicit flow (:meth:`action_switch_vault_picker` /
        :meth:`~lot_textual_ui.lot_cli.LotCli.set_vault_path`), and silently
        jumping the session into a brand-new empty vault mid-use would be
        surprising; the current vault is left untouched (so there is nothing to
        reload). Offering to switch could be a later follow-up prompt, but the
        minimal correct behaviour is create-on-disk plus a confirming toast.
        """
        self.push_screen(CommandFormScreen(command), self._create_vault_done)

    def _create_vault_done(self, argv: list[str] | None) -> None:
        """Run the collected ``vault new`` argv (``None`` = cancelled)."""
        if argv is None:
            return
        self._run_vault_new(argv)

    @work(exclusive=False, group="vault-new")
    async def _run_vault_new(self, argv: list[str]) -> None:
        """Run ``lot vault new <path>`` in a worker and toast the outcome.

        Kept off the event loop like the other command workers. The CLI reports
        its own failures — the path already exists, an unwritable location — which
        surface as an error toast. On success only a confirming toast is shown:
        per :meth:`create_vault`'s design decision the running UI is neither
        switched to nor reloaded (the current vault is unchanged). The ``<path>``
        is the last element of the assembled argv (``["vault", "new", <path>]``).
        """
        try:
            await self._lot_cli.run_command(*argv)
        except LotError as error:
            self.notify(str(error), title="Could not create vault", severity="error")
            return
        self.notify(f"Created a new vault at {argv[-1]}.", title="Vault created")

    # --- mutation commands on the in-view Thing (move / archive) -----------
    #
    # ``thing move`` and ``thing archive`` are mutations, so — unlike the
    # read-only commands above — they run through their own bespoke modals and
    # reload the vault afterwards (a picker/confirm, not the generic form: a
    # mutually-exclusive parent/root destination and a destructive confirm are
    # far better expressed that way). Both act on the in-view Thing, resolved
    # via :meth:`_require_current_thing` exactly like :meth:`send_to_claude`.

    def move_thing(self) -> None:
        """Move the in-view Thing under a picked destination (palette/nav).

        Backs the ``thing move`` leaf. Targets the centre column's active item
        (:attr:`current_thing_id`); with nothing in view
        :meth:`_require_current_thing` toasts and no picker opens. Opens the
        shared :class:`~lot_textual_ui.batch.ThingPickerScreen` over the whole
        vault tree — excluding the Thing itself, which can never be its own
        destination (the exact pattern :meth:`action_batch_move` uses) — plus a
        "Top level" entry (``--root``). The picker only *collects* the
        destination; :meth:`_move_thing_target_chosen` runs the move.
        """
        target = self._require_current_thing(
            "Select a Thing first to move it.", title="No Thing selected"
        )
        if target is None:
            return
        thing = self.thing_by_id(target)
        name = thing.name if thing is not None else target
        self.push_screen(
            ThingPickerScreen(
                self._index.roots,
                exclude={target},
                title=f"Move {name} to…",
            ),
            partial(self._move_thing_target_chosen, target, name),
        )

    def _move_thing_target_chosen(
        self, target: str, name: str, destination: str | None
    ) -> None:
        """Run the move to the picker's destination (``None`` = cancelled).

        :data:`~lot_textual_ui.batch.TOP_LEVEL` maps to ``--root`` (the Thing
        moves to the vault's top level); any other value is a destination Thing's
        id and maps to ``--parent <id>``.
        """
        if destination is None:
            return
        parent = None if destination == TOP_LEVEL else destination
        self._run_thing_move(target, name, parent)

    @work(exclusive=False, group="thing-mutate")
    async def _run_thing_move(self, target: str, name: str, parent: str | None) -> None:
        """Run ``lot thing move`` in a worker, then reload and toast the outcome.

        ``parent`` is ``None`` for a move to the vault root (``--root``) or a
        destination Thing's id (``--parent <id>``). Kept off the event loop; the
        CLI reports its own failures — a destination inside the moved subtree (a
        cycle), a name collision at the destination, a no-op move, an unknown id
        — which surface as an error toast (no pre-validation here). On success
        the vault is reloaded so the trees repaint at the new location.
        """
        try:
            if parent is None:
                await self._lot_cli.thing_move(target, root=True)
            else:
                await self._lot_cli.thing_move(target, parent=parent)
        except LotError as error:
            self.notify(str(error), title="Could not move Thing", severity="error")
            return
        await self._reload_vault()
        self.notify(f"Moved {name}.", title="Thing moved")

    def archive_thing(self) -> None:
        """Archive the in-view Thing after a confirming dialog (palette/nav).

        Backs the ``thing archive`` leaf. Targets the centre column's active item
        (:attr:`current_thing_id`); with nothing in view
        :meth:`_require_current_thing` toasts and no dialog opens. Archiving
        removes the Thing *and all its descendants* from the working tree (history
        stays in git), so — mirroring :meth:`action_batch_archive` but singular —
        it opens a :class:`~lot_textual_ui.batch.ConfirmScreen` first;
        :meth:`_archive_thing_confirmed` runs the archive once confirmed.
        """
        target = self._require_current_thing(
            "Select a Thing first to archive it.", title="No Thing selected"
        )
        if target is None:
            return
        thing = self.thing_by_id(target)
        name = thing.name if thing is not None else target
        self.push_screen(
            ConfirmScreen(
                f"Archive {name}? It is removed from the vault together with all "
                "of its descendant Things (history is preserved in git).",
                title="Archive Thing",
                confirm_label="Archive",
            ),
            partial(self._archive_thing_confirmed, target, name),
        )

    def _archive_thing_confirmed(
        self, target: str, name: str, confirmed: bool | None
    ) -> None:
        """Run the archive once the dialog confirms it (falsy = cancelled)."""
        if not confirmed:
            return
        self._run_thing_archive(target, name)

    @work(exclusive=False, group="thing-mutate")
    async def _run_thing_archive(self, target: str, name: str) -> None:
        """Run ``lot thing archive`` in a worker, then reload and toast the outcome.

        Kept off the event loop; the CLI refuses when ``vault.auto-commit`` is
        ``false`` (history cannot be preserved without commits), which surfaces
        as an error toast. On success the vault is reloaded — the archived Thing
        was the in-view selection, so the guarded :meth:`_reload_vault`
        re-resolves the now-gone selection rather than crashing.
        """
        try:
            await self._lot_cli.thing_archive(target)
        except LotError as error:
            self.notify(str(error), title="Could not archive Thing", severity="error")
            return
        await self._reload_vault()
        self.notify(f"Archived {name}.", title="Thing archived")

    @work(exclusive=False, group="palette-run")
    async def _run_leaf_command(self, command: LeafCommand) -> None:
        """Run a no-input leaf command, then refresh the vault view.

        Kept in a background worker so the ``lot`` subprocess never blocks the
        event loop; failures surface as an error toast rather than crashing.
        """
        try:
            await self._lot_cli.run_command(*command.path)
        except LotError as error:
            self.notify(str(error), title="Command failed", severity="error")
            return
        await self._reload_vault()
        self.notify(f"Ran 'lot {command.label}'.")

    @work(exclusive=False, group="palette-run")
    async def action_refresh_vault(self) -> None:
        """Reload the whole vault from disk and repaint (palette "Refresh")."""
        await self._reload_vault()
