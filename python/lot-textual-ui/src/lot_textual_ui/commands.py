"""The command navigator, palette dispatch and form entry points (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from textual import events, work

from .command_nav import RESERVED_CTRL_LETTERS, CommandNav, CommandNavScreen
from .forms import NewThingResult, NewThingScreen, NewUpdateScreen
from .lot_cli import LotError
from .palette import LeafCommand


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
        if key[5] in RESERVED_CTRL_LETTERS or len(self.screen_stack) > 1:
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
          the Thing, is the one the user is looking at);
          ``("settings", "set", "theme")`` opens the theme picker
          (:meth:`action_switch_theme`), whose selection both sets and persists
          the theme — what the command does (the fuzzy palette hides this leaf,
          since the *Switch theme* internal command already offers it — see
          :data:`~lot_textual_ui.palette.PALETTE_DUPLICATE_LEAVES` — so it now
          arrives here only via the command navigator); other input-needing
          commands (e.g. ``update path``) still fall through to a placeholder
          toast until their own form work items land.
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
            if command.path == ("settings", "set", "theme"):
                # `settings set theme <name>` needs a theme name; the theme
                # picker *is* its form — and it already applies the choice live
                # and persists it (see :meth:`action_switch_theme`), which is
                # exactly what the command does, so route it there instead of a
                # dead-end "no form" toast.
                self.action_switch_theme()
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
        """Push the new-Thing form; on submit, select the created Thing.

        The reusable entry point for creating a Thing: the palette's ``thing
        new`` leaf calls it with no arguments (a top-level Thing), and the
        create-child-Things work item calls it with ``parent_id`` set (and a
        fitting ``title``) to seed the parent. The
        :class:`~lot_textual_ui.forms.NewThingScreen` dismisses with a
        :class:`~lot_textual_ui.forms.NewThingResult` on success (or ``None`` on
        cancel), handled by :meth:`_new_thing_created`.
        """
        self.push_screen(
            NewThingScreen(parent_id=parent_id, title=title),
            self._new_thing_created,
        )

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

    @work(exclusive=False, group="new-thing-select")
    async def _new_thing_created(self, result: NewThingResult | None) -> None:
        """Reload the vault and jump the view to a freshly created Thing.

        Called with the form's dismiss value. ``None`` means the form was
        cancelled — nothing to do. Otherwise the vault is reloaded first (the
        live ``lot watch`` stream would bring the node in eventually, but a
        reload avoids the race) and only then is the view moved, so the target id
        is already in the index. If the node is somehow still unknown the move is
        skipped rather than selecting a phantom id.

        A new top-level Thing is a root, so it becomes the left selection
        directly. A new child is a leaf, which the left tree does not show (only
        roots and branches); its parent — now a branch — becomes the left
        selection, rooting the centre column there, and the new child is made the
        centre's active item so it is highlighted and shown in the detail pane.

        Finally, when the form was submitted with **Create and send**
        (:attr:`~lot_textual_ui.forms.NewThingResult.send`), the Claude stage is
        opened on the new Thing — the selection now points at it, so the command
        navigator (and the ``claude send`` it leads to) targets the right Thing.
        """
        if result is None:
            return
        new_id = result.thing_id
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
        if result.send:
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
        """Push the type-fixed new-Update form; on submit, refresh the detail.

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

        The :class:`~lot_textual_ui.forms.NewUpdateScreen` dismisses with the new
        update's ``lot:`` id on success or ``None`` on cancel; the result is
        handled by :meth:`_update_created`.
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
        self.push_screen(
            NewUpdateScreen(
                thing_id=target,
                thing_label=thing.name if thing is not None else target,
                kind=kind,
            ),
            self._update_created,
        )

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

    @work(exclusive=False, group="new-update-reload")
    async def _update_created(self, new_id: str | None) -> None:
        """Reload the vault so a freshly added Update shows in the detail pane.

        Called with the form's dismiss value. ``None`` means the form was
        cancelled — nothing to do. Otherwise the vault is reloaded: the Update
        landed on the selected Thing, so :meth:`_reload_vault` repaints the trees
        (its status marker may have changed, e.g. ``done``) and forces the detail
        pane to re-render the selected Thing's thread with the new Update. The
        live ``lot watch`` stream would deliver the change too, but reloading
        here avoids the race.
        """
        if new_id is None:
            return
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
