"""The LoT Textual application: a three-column, selection-driven browser.

Layout (left to right):

* **Left** — a :class:`~textual.widgets.Tree` of the selected Thing's ancestor
  chain, the Thing itself, and its siblings.
* **Centre** — a :class:`~textual.widgets.Tree` of the selected Thing's
  descendants.
* **Right** — a container with id ``detail`` holding the
  :class:`~lot_textual_ui.detail.DetailPane` (see :ref:`detail-seam` below),
  which renders the selected Thing's computed state and update thread.

A single reactive attribute, :attr:`LotTextualApp.selected_id`, is the whole
selection model. Selecting a node in *either* tree assigns it, and
:meth:`LotTextualApp.watch_selected_id` re-derives and refreshes all three
columns from an in-memory index of the vault. Ancestors, siblings, and
descendants are all computed from the nested tree returned by
``lot thing list`` — no extra CLI round-trips.

.. _detail-seam:

Hooking up the detail pane
--------------------------

The detail pane (:class:`~lot_textual_ui.detail.DetailPane`) is mounted inside
the ``#detail`` container in :meth:`LotTextualApp.compose`. It stays decoupled
from the trees: rather than being pushed to, it watches the app's reactive in
its own ``on_mount``::

    self.watch(self.app, "selected_id", self._on_selected_id_changed)

Textual's :meth:`~textual.dom.DOMNode.watch` can watch a reactive on any node,
so selection propagates without the shell knowing about the pane. The pane
loads each Thing's state/updates through the app's shared
:class:`~lot_textual_ui.lot_cli.LotCli` instance.
"""

from __future__ import annotations

from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .detail import DetailPane
from .forms import NewThingScreen, NewUpdateScreen
from .keys import ACTION_BINDINGS
from .lot_cli import LotCli, LotError
from .models import Thing, WatchEvent
from .palette import PALETTE_PROVIDERS, LeafCommand

# A short glyph per status so the tree conveys state at a glance without colour.
STATUS_MARKERS = {
    "note": "○",
    "work": "◐",
    "info": "ⓘ",
    "done": "●",
}


def node_label(thing: Thing) -> str:
    """Render a Thing as a tree label: a status marker plus its name."""
    marker = STATUS_MARKERS.get(thing.status, "·")
    return f"{marker} {thing.name}"


class LotTextualApp(App[None]):
    """The LoT Textual application shell.

    Args:
        lot_cli: The vault adapter. Defaults to a real :class:`LotCli`; tests
            inject a fake exposing an async ``thing_list()`` so no vault is
            required.
    """

    TITLE = "LoT"
    SUB_TITLE = "Lists of Things"

    CSS = """
    #columns {
        height: 1fr;
    }

    #left-tree, #centre-tree {
        width: 1fr;
        border-right: solid $panel-lighten-2;
    }

    #detail {
        width: 1fr;
        padding: 1;
    }
    """

    # Every app-level key comes from the one central table (see
    # :mod:`lot_textual_ui.keys`), which is the single seam Phase 5's
    # keybinding overrides will target. Do not add ``Binding``\s here or on
    # child widgets; add them to ``ACTION_BINDINGS`` instead.
    BINDINGS = ACTION_BINDINGS

    # The command palette (``ctrl+p``) draws from these providers on top of
    # Textual's default system commands: the dynamically-discovered ``lot``
    # command tree and the TUI's own internal commands. See
    # :mod:`lot_textual_ui.palette` (and its forms seam).
    COMMANDS = App.COMMANDS | set(PALETTE_PROVIDERS)

    # The entire selection model: the id of the currently selected Thing. The
    # detail-pane work item watches this (see module docstring). ``init=False``
    # keeps the watcher from firing for the initial ``None`` before the vault is
    # loaded; ``on_mount`` assigns the first real selection.
    selected_id: reactive[str | None] = reactive(None, init=False)

    def __init__(self, lot_cli: LotCli | None = None) -> None:
        super().__init__()
        self._lot_cli = lot_cli if lot_cli is not None else LotCli()
        # Indexes over the whole vault, built once on load.
        self._by_id: dict[str, Thing] = {}
        self._parent_of: dict[str, Thing | None] = {}
        self._roots: list[Thing] = []

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            yield Tree("LoT", id="left-tree")
            yield Tree("Descendants", id="centre-tree")
            with Container(id="detail"):
                yield DetailPane(self._lot_cli)
        yield Footer()

    async def on_mount(self) -> None:
        """Load the vault, select an initial Thing, and focus the left tree."""
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        # Initial selection: the first top-level Thing, if any.
        self.selected_id = self._roots[0].id if self._roots else None
        # Start focus in the left column so vim motions have a deterministic
        # home; ``h``/``l`` walk focus from here across the columns.
        self.query_one("#left-tree", Tree).focus()
        # Baseline is loaded; now apply external changes live off `lot watch`.
        self._watch_vault()

    # --- live updates ------------------------------------------------------
    #
    # ``lot watch`` (readme §5.6) streams one minimal, incremental event per
    # settled vault change. We load the baseline above with ``thing_list()`` —
    # the watcher emits no initial-state event — then patch a *single* node of
    # the in-memory index per event so edits from the CLI, Claude sessions, or
    # git appear without a restart. All subprocess/parsing lives in ``LotCli``;
    # the app only folds already-typed events into its in-memory state. Only a
    # ``reload`` event (the rare no-single-Thing fallback) reloads the whole
    # baseline.

    @work(exclusive=False, group="watch")
    async def _watch_vault(self) -> None:
        """Consume the watch stream, applying each event to the UI.

        Runs as a long-lived background worker. Textual cancels it on app exit,
        which unwinds :meth:`LotCli.watch` and terminates the ``lot watch``
        subprocess (no orphan is left). A failed/absent ``lot watch`` is
        swallowed so the browser still works statically.
        """
        try:
            async for event in self._lot_cli.watch():
                await self._apply_event(event)
        except LotError:
            pass

    async def _apply_event(self, event: WatchEvent) -> None:
        """Patch one watch event into the in-memory index and refresh columns.

        The index is mutated incrementally rather than rebuilt: a created /
        modified event upserts one node (id + name + status + parent), a deleted
        event drops that id and its descendants, and the rare id-less ``reload``
        event reloads the full ``thing_list()`` baseline. The selection is
        tracked by id and preserved; if the selected Thing is gone it falls back
        to its old parent, then to the first root, then to nothing. Only what
        changed is repainted: when the selection id is unchanged the reactive
        watcher would not fire, so the trees are rebuilt explicitly
        (names/statuses/structure may have moved), and the detail pane is
        reloaded only when the changed Thing *is* the selection — so an unrelated
        event never disturbs its scroll position.
        """
        previous = self.selected_id
        old_parent = self._parent_of.get(previous) if previous is not None else None
        old_parent_id = old_parent.id if old_parent is not None else None

        if event.kind == "deleted":
            if event.id is not None:
                self._remove_subtree(event.id)
            # A deletion never reloads the detail pane in place: if the selected
            # Thing was the one deleted, the selection changes and the reactive
            # path reloads it instead.
            self._refresh_after(previous, old_parent_id, changed_id=None)
        elif event.kind == "reload" or event.id is None:
            # Fallback: a batch that maps to no single Thing. Reload the whole
            # baseline — the one case a full refresh is acceptable (and rare).
            listing = await self._lot_cli.thing_list()
            self._reindex(listing.things)
            self._refresh_after(previous, old_parent_id, changed_id=None)
        else:
            self._upsert_node(
                event.id, event.name or "", event.status or "", event.parent
            )
            self._refresh_after(previous, old_parent_id, changed_id=event.id)

    def _refresh_after(
        self, previous: str | None, old_parent_id: str | None, changed_id: str | None
    ) -> None:
        """Re-resolve the selection and repaint the minimum after an index patch.

        If the selection id changed (its Thing was removed), assigning it fires
        ``watch_selected_id`` (rebuilds both trees) and the detail pane's own
        watcher (reloads it). Otherwise the reactive stays quiet, so the trees
        are rebuilt in place; the detail pane is reloaded only when ``changed_id``
        is the current selection.
        """
        resolved = self._resolve_selection(previous, old_parent_id)
        if resolved != previous:
            self.selected_id = resolved
            return

        self._rebuild_left_tree(resolved)
        self._rebuild_centre_tree(resolved)
        if changed_id is not None and changed_id == resolved:
            self.query_one(DetailPane).reload()

    def _resolve_selection(
        self, previous: str | None, old_parent_id: str | None
    ) -> str | None:
        """Re-resolve the selection against the freshly rebuilt index."""
        if previous is not None and previous in self._by_id:
            return previous
        if old_parent_id is not None and old_parent_id in self._by_id:
            return old_parent_id
        return self._roots[0].id if self._roots else None

    # --- keyboard/mouse navigation -----------------------------------------
    #
    # These back the actions declared in the central binding table
    # (:mod:`lot_textual_ui.keys`). Each motion is pane-agnostic: it looks up
    # whichever column currently holds focus and does the right thing there —
    # move a Tree's cursor or scroll the detail pane. The mouse needs no code
    # here: Textual's Tree handles click-to-select and every pane
    # (both trees and the DetailPane's VerticalScroll) handles the wheel.

    def _focus_chain(self) -> list[Widget]:
        """The three columns, left to right, as the focus/drill order."""
        return [
            self.query_one("#left-tree", Tree),
            self.query_one("#centre-tree", Tree),
            self.query_one(DetailPane),
        ]

    def _focused_index(self) -> int:
        """Index into :meth:`_focus_chain` of the column that holds focus.

        Walks up from the actually-focused widget so a focused descendant of
        the detail pane still resolves to the pane. Defaults to the left
        column when nothing is focused.
        """
        chain = self._focus_chain()
        node: Widget | None = self.focused
        while node is not None:
            for index, column in enumerate(chain):
                if node is column:
                    return index
            node = node.parent if isinstance(node.parent, Widget) else None
        return 0

    def _nav_target(self) -> Widget:
        """The column vertical motions (``j``/``k``/``g``/``G``) act on."""
        return self._focus_chain()[self._focused_index()]

    def _move_focus(self, delta: int) -> None:
        chain = self._focus_chain()
        index = max(0, min(len(chain) - 1, self._focused_index() + delta))
        chain[index].focus()

    def action_focus_right(self) -> None:
        """Drill in: move focus one column to the right (clamped)."""
        self._move_focus(1)

    def action_focus_left(self) -> None:
        """Drill out: move focus one column to the left (clamped)."""
        self._move_focus(-1)

    def action_cursor_down(self) -> None:
        """Move down one row in the focused pane."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.action_cursor_down()
        else:
            target.scroll_down()

    def action_cursor_up(self) -> None:
        """Move up one row in the focused pane."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.action_cursor_up()
        else:
            target.scroll_up()

    def action_cursor_top(self) -> None:
        """Jump to the first row of the focused pane (vim ``g``)."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.move_cursor_to_line(0)
        else:
            target.scroll_home(animate=False)

    def action_cursor_bottom(self) -> None:
        """Jump to the last row of the focused pane (vim ``G``)."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.move_cursor_to_line(target.last_line)
        else:
            target.scroll_end(animate=False)

    # --- public API for sibling widgets (e.g. the detail pane) -------------

    @property
    def lot_cli(self) -> LotCli:
        """The shared vault adapter.

        Exposed so the command palette providers (see
        :mod:`lot_textual_ui.palette`) can discover the ``lot`` command tree
        through the *one* :class:`LotCli` instance instead of spawning ``lot``
        themselves.
        """
        return self._lot_cli

    def thing_by_id(self, thing_id: str | None) -> Thing | None:
        """Return the Thing with ``thing_id``, or ``None`` if unknown."""
        if thing_id is None:
            return None
        return self._by_id.get(thing_id)

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
          on stdin, …): dispatch on ``command.path`` to the matching form
          screen. ``("thing", "new")`` opens :meth:`open_new_thing_form`;
          ``("update", "work"|"info"|"done")`` open :meth:`open_new_update_form`
          pre-set to that type; other input-needing commands still fall through
          to a placeholder toast until their own form work items land.
        """
        if command.needs_input:
            if command.path == ("thing", "new"):
                self.open_new_thing_form()
                return
            if command.path[:1] == ("update",) and command.path[-1] in (
                "work",
                "info",
                "done",
            ):
                self.open_new_update_form(kind=command.path[-1])
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
        :class:`~lot_textual_ui.forms.NewThingScreen` dismisses with the new
        Thing's ``lot:`` id on success or ``None`` on cancel; the id is handled
        by :meth:`_new_thing_created`.
        """
        self.push_screen(
            NewThingScreen(parent_id=parent_id, title=title),
            self._new_thing_created,
        )

    @work(exclusive=False, group="new-thing-select")
    async def _new_thing_created(self, new_id: str | None) -> None:
        """Reload the vault and jump the selection to a freshly created Thing.

        Called with the form's dismiss value. ``None`` means the form was
        cancelled — nothing to do. Otherwise the vault is reloaded first (the
        live ``lot watch`` stream would bring the node in eventually, but a
        reload avoids the race) and only then is the selection moved, so the
        target id is already in the index. If the node is somehow still unknown
        the assignment is skipped rather than selecting a phantom id.
        """
        if new_id is None:
            return
        await self._reload_vault()
        if new_id in self._by_id:
            self.selected_id = new_id

    def open_new_update_form(
        self, kind: str = "work", thing_id: str | None = None
    ) -> None:
        """Push the new-Update form for a Thing; on submit, refresh its detail.

        The reusable entry point for adding an Update. The palette's ``update
        work``/``info``/``done`` leaves call it with the matching ``kind`` and no
        ``thing_id``, so it defaults to the currently selected Thing — "add an
        update" almost always means "to the Thing I'm looking at". Other flows
        (batch operations, …) may pass an explicit ``thing_id``. With no target
        available (nothing selected and no id given) it notifies and does
        nothing rather than opening a form that cannot submit.

        The :class:`~lot_textual_ui.forms.NewUpdateScreen` dismisses with the new
        update's ``lot:`` id on success or ``None`` on cancel; the result is
        handled by :meth:`_update_created`.
        """
        target = thing_id if thing_id is not None else self.selected_id
        if target is None:
            self.notify(
                "Select a Thing first to add an update to it.",
                title="No Thing selected",
                severity="warning",
            )
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

    async def _reload_vault(self) -> None:
        """Re-read the full ``thing_list`` baseline and repaint, keeping selection.

        Mirrors the ``reload`` path in :meth:`_apply_event`: rebuild the index
        from a fresh listing, then re-resolve the selection and repaint the
        minimum (forcing a detail reload for the current selection so freshly
        written updates appear).
        """
        previous = self.selected_id
        old_parent = self._parent_of.get(previous) if previous is not None else None
        old_parent_id = old_parent.id if old_parent is not None else None
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        self._refresh_after(previous, old_parent_id, changed_id=previous)

    # --- selection model ---------------------------------------------------

    def watch_selected_id(self, old: str | None, new: str | None) -> None:
        """Re-derive and refresh both trees from the new selection.

        The right column (:class:`~lot_textual_ui.detail.DetailPane`) refreshes
        itself by watching ``selected_id`` directly, so it is not touched here.
        """
        self._rebuild_left_tree(new)
        self._rebuild_centre_tree(new)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        """Selecting a node in either tree drives the shared selection."""
        thing_id = event.node.data
        if thing_id is not None:
            self.selected_id = thing_id

    # --- derivation --------------------------------------------------------

    def _reindex(self, things: list[Thing]) -> None:
        """Build the id→Thing and id→parent indexes from the nested tree."""
        self._roots = things
        self._by_id = {}
        self._parent_of = {}

        def walk(items: list[Thing], parent: Thing | None) -> None:
            for thing in items:
                self._by_id[thing.id] = thing
                self._parent_of[thing.id] = parent
                walk(thing.children, thing)

        walk(things, None)

    def _upsert_node(
        self, thing_id: str, name: str, status: str, parent_id: str | None
    ) -> None:
        """Insert or update a single node, keeping every index consistent.

        A never-seen id creates a fresh (childless) :class:`Thing`, linked under
        its parent (or as a root). A known id updates its ``name``/``status`` in
        place — preserving its existing ``children`` so descendants survive — and
        is re-linked only if its parent actually moved. ``_by_id``,
        ``_parent_of`` and the ``children``/``_roots`` sibling lists are all kept
        in agreement so ``_ancestors``/``_siblings``/``_rebuild_*`` stay correct.
        """
        existing = self._by_id.get(thing_id)
        if existing is None:
            node = Thing(id=thing_id, name=name, status=status, children=[])
            self._by_id[thing_id] = node
            self._link(node, parent_id)
            return

        existing.name = name
        existing.status = status
        current_parent = self._parent_of.get(thing_id)
        current_parent_id = current_parent.id if current_parent is not None else None
        if current_parent_id != parent_id:
            self._unlink(existing)
            self._link(existing, parent_id)

    def _remove_subtree(self, thing_id: str) -> None:
        """Drop a Thing and all its descendants from every index."""
        node = self._by_id.get(thing_id)
        if node is None:
            return
        for child in list(node.children):
            self._remove_subtree(child.id)
        self._unlink(node)
        self._by_id.pop(thing_id, None)
        self._parent_of.pop(thing_id, None)

    def _link(self, node: Thing, parent_id: str | None) -> None:
        """Attach ``node`` under ``parent_id`` (or as a root), name-sorted."""
        parent = self._by_id.get(parent_id) if parent_id is not None else None
        self._parent_of[node.id] = parent
        siblings = parent.children if parent is not None else self._roots
        siblings.append(node)
        siblings.sort(key=lambda thing: thing.name)

    def _unlink(self, node: Thing) -> None:
        """Detach ``node`` from its parent's children (or the root list)."""
        parent = self._parent_of.get(node.id)
        siblings = parent.children if parent is not None else self._roots
        siblings[:] = [thing for thing in siblings if thing.id != node.id]

    def _ancestors(self, thing_id: str) -> list[Thing]:
        """Return the ancestor chain from the root down to the parent."""
        chain: list[Thing] = []
        parent = self._parent_of.get(thing_id)
        while parent is not None:
            chain.append(parent)
            parent = self._parent_of.get(parent.id)
        chain.reverse()
        return chain

    def _siblings(self, thing_id: str) -> list[Thing]:
        """Return the Things at the selected Thing's level (including it)."""
        parent = self._parent_of.get(thing_id)
        return parent.children if parent is not None else self._roots

    # --- rendering ---------------------------------------------------------

    def _rebuild_left_tree(self, selected_id: str | None) -> None:
        tree = self.query_one("#left-tree", Tree)
        tree.clear()
        tree.root.expand()
        selected = self.thing_by_id(selected_id)
        if selected is None:
            return

        # Nest the ancestor chain, then hang the sibling level off the deepest
        # ancestor (or the tree root, for a top-level selection).
        node: TreeNode[str] = tree.root
        for ancestor in self._ancestors(selected_id):
            node = node.add(node_label(ancestor), data=ancestor.id, expand=True)

        selected_node: TreeNode[str] | None = None
        for sibling in self._siblings(selected_id):
            leaf = node.add_leaf(node_label(sibling), data=sibling.id)
            if sibling.id == selected_id:
                selected_node = leaf

        # Highlight the selection without re-firing NodeSelected (move_cursor
        # emits NodeHighlighted, which we don't act on).
        if selected_node is not None:
            tree.move_cursor(selected_node)

    def _rebuild_centre_tree(self, selected_id: str | None) -> None:
        tree = self.query_one("#centre-tree", Tree)
        tree.clear()
        selected = self.thing_by_id(selected_id)
        if selected is None:
            tree.root.set_label("Descendants")
            tree.root.data = None
            return

        tree.root.set_label(node_label(selected))
        tree.root.data = selected.id
        tree.root.expand()
        for child in selected.children:
            self._add_subtree(tree.root, child)

    def _add_subtree(self, parent_node: TreeNode[str], thing: Thing) -> None:
        if thing.children:
            node = parent_node.add(node_label(thing), data=thing.id, expand=True)
            for child in thing.children:
                self._add_subtree(node, child)
        else:
            parent_node.add_leaf(node_label(thing), data=thing.id)


def main() -> None:
    """Console-script entry point: run the Textual app."""
    LotTextualApp().run()


if __name__ == "__main__":
    main()
