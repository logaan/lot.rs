"""The LoT Textual application: a three-column, selection-driven browser.

Layout (left to right):

* **Left** — a :class:`~textual.widgets.Tree` of the whole vault's root and
  branch Things (every Thing that has children), nested by parentage; leaf
  Things (no children) are omitted. The selected Thing — always a root or a
  branch — is highlighted here. The tree's always-visible ``LoT`` root row is
  itself selectable: it stands for the vault as a whole (see
  :data:`VAULT_ROOT`).
* **Centre** — a :class:`~textual.widgets.Tree` of the selected Thing's
  descendants (this is where leaf Things are reached). With the vault root
  selected it shows the *full* vault tree: every root Thing with all of its
  descendants.
* **Right** — a container with id ``detail`` holding the
  :class:`~lot_textual_ui.detail.DetailPane` (see :ref:`detail-seam` below),
  which renders the selected Thing's computed state and update thread.

Two reactive attributes model the selection, one per navigable column:

* :attr:`LotTextualApp.selected_id` is the **left** column's selection — the
  root or branch Thing the left tree highlights, and which roots the centre
  tree. The item under the *left* cursor assigns it: moving the cursor (or
  clicking) selects, no separate confirm keypress needed. Cursoring onto the
  ``LoT`` root row assigns the :data:`VAULT_ROOT` sentinel, rooting the centre
  column at the whole vault.
* :attr:`LotTextualApp.active_id` is the **centre** column's active item — the
  Thing shown in the right column. It resets to :attr:`selected_id` whenever the
  left selection changes, but the item under the *centre* cursor moves only
  ``active_id`` (the left column is left untouched). So each column keeps its own
  active item, and drilling into a descendant in the centre never resets the
  left column.

Selection follows the cursor in both trees (see
:meth:`LotTextualApp.on_tree_node_highlighted`); Enter/click still work via
:meth:`LotTextualApp.on_tree_node_selected`.

:meth:`LotTextualApp.watch_selected_id` re-derives the left and centre trees;
:meth:`LotTextualApp.watch_active_id` highlights the active centre node (the
detail pane watches ``active_id`` itself). The root/branch skeleton and the
descendants are all computed from the nested tree returned by ``lot thing
list`` — no extra CLI round-trips.

.. _detail-seam:

Hooking up the detail pane
--------------------------

The detail pane (:class:`~lot_textual_ui.detail.DetailPane`) is mounted inside
the ``#detail`` container in :meth:`LotTextualApp.compose`. It stays decoupled
from the trees: rather than being pushed to, it watches the centre column's
active item (:attr:`LotTextualApp.active_id`) in its own ``on_mount``::

    self.watch(self.app, "active_id", self._on_active_id_changed)

Textual's :meth:`~textual.dom.DOMNode.watch` can watch a reactive on any node,
so the active item propagates without the shell knowing about the pane. The
pane loads each Thing's state/updates through the app's shared
:class:`~lot_textual_ui.lot_cli.LotCli` instance.
"""

from __future__ import annotations

from rich.text import Text
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .batch_actions import BatchActionsMixin
from .clipboard import ClipboardMixin
from .commands import CommandsMixin
from .config_theme import ConfigThemeMixin
from .detail import DetailPane, UpdateItem
from .index import VAULT_ROOT, VaultIndex
from .keys import ACTION_BINDINGS
from .lot_cli import LotCli
from .models import (
    EffectiveConfig,
    Thing,
    UpdateType,
)
from .navigation import NavigationMixin
from .palette import PALETTE_PROVIDERS
from .vault_switching import VaultSwitchingMixin
from .watch import WatchMixin
from .wrapping_tree import WrappingTree

# Update types are vault-configured, so status colours can't be a fixed
# name -> colour table. Instead each configured type is assigned a colour by
# its position in the vault's `update-types` list, cycling through this
# sequence — except *terminal* types, which are always dimmed (a retired
# Thing should recede). With the stock set this yields the classic mapping:
# note=blue, work=yellow, info=green, done=grey50.
STATUS_COLOR_CYCLE = [
    "blue",
    "yellow",
    "green",
    "cyan",
    "red",
    "bright_blue",
    "bright_yellow",
    "bright_green",
    "bright_cyan",
    "bright_red",
]
# The colour for terminal (Thing-retiring) statuses.
TERMINAL_STATUS_COLOR = "grey50"
# Fallback colour for a status the configured types don't know (e.g. a Thing
# left in a since-removed status).
UNKNOWN_STATUS_COLOR = "magenta"


def status_colors(types: list[UpdateType]) -> dict[str, str]:
    """A status -> colour map for the configured update types.

    Non-terminal types take colours from :data:`STATUS_COLOR_CYCLE` in their
    configured order; terminal types are always
    :data:`TERMINAL_STATUS_COLOR`. Statuses outside the map render in
    :data:`UNKNOWN_STATUS_COLOR` (the lookup's fallback, not an entry here).
    """
    colors: dict[str, str] = {}
    bright = 0
    for t in types:
        if t.terminal:
            colors[t.name] = TERMINAL_STATUS_COLOR
        else:
            colors[t.name] = STATUS_COLOR_CYCLE[bright % len(STATUS_COLOR_CYCLE)]
            bright += 1
    return colors


# Before config loads no types are known (types are entirely
# vault-configured, with no fallback set), so every status renders in
# UNKNOWN_STATUS_COLOR until the first `lot settings get` lands and
# `_apply_config` recolours.
DEFAULT_STATUS_COLORS: dict[str, str] = {}

# No default theme of our own: when config sets none we leave Textual's built-in
# default in place so the user's chosen Textual colourscheme is respected. Users
# can still switch theme at runtime via the palette's "Switch theme" command.


# The glyph shown in front of a multi-select-marked row. A named constant so
# the marked-row indicator is one obvious thing to restyle (and for tests).
MARK_INDICATOR = "●"

# VAULT_ROOT — the left tree's whole-vault sentinel — is defined in
# :mod:`lot_textual_ui.index` (selection resolution falls back to it) and
# re-exported here as part of the app's public surface.

# The copy-confirmation toast in web mode. The app can only *send* the text to
# the browser (via OSC 52 through textual-serve); whether the browser actually
# writes its clipboard depends on the page being secure (localhost/HTTPS) — the
# app cannot observe the outcome, so the wording promises only the handoff.
WEB_COPY_NOTICE = (
    "Sent {text} to the browser clipboard — the browser may block the write "
    "unless the page is on localhost or HTTPS."
)


def node_label(
    thing: Thing, marked: bool = False, colors: dict[str, str] | None = None
) -> Text:
    """Render a Thing as a tree label: a colour-coded status name plus its name.

    The status is spelled out (e.g. ``work``) rather than shown as a glyph, and
    padded to a fixed width so the Thing names line up in the tree. A leading
    two-cell column carries the multi-select :data:`MARK_INDICATOR` when the
    Thing is ``marked`` (and stays blank otherwise, so marked and unmarked rows
    keep their columns aligned). ``colors`` is the status -> colour map for the
    vault's configured types (see :func:`status_colors`), defaulting to the
    empty pre-config map (every status in :data:`UNKNOWN_STATUS_COLOR`).
    """
    if colors is None:
        colors = DEFAULT_STATUS_COLORS
    status = thing.status or "?"
    color = colors.get(thing.status, UNKNOWN_STATUS_COLOR)
    label = Text()
    label.append(f"{MARK_INDICATOR} " if marked else "  ", style="bold cyan")
    label.append(f"{status:<4}", style=color)
    label.append(f"  {thing.name}")
    return label


def label_name_offset(thing: Thing) -> int:
    """Cells before the name in :func:`node_label`'s label.

    The name is preceded by the two-cell mark column, the (min-four-wide) status
    column, and a two-space gutter; :class:`~lot_textual_ui.wrapping_tree.WrappingTree`
    uses this so a wrapped name lines up under itself in its own column rather
    than under the status. Kept in sync with :func:`node_label` by construction.
    """
    status = thing.status or "?"
    return 2 + len(f"{status:<4}") + 2


class LotTextualApp(
    ConfigThemeMixin,
    VaultSwitchingMixin,
    WatchMixin,
    NavigationMixin,
    ClipboardMixin,
    BatchActionsMixin,
    CommandsMixin,
    App[None],
):
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

    /* All three columns share one theme-derived background. Without this the
       three diverge: the two Trees default to $surface while the #detail
       container falls through to the darker screen $background, and Textual's
       Tree:focus adds a `background-tint` that lightens whichever tree has
       focus. The overrides below keep every column at $surface regardless of
       focus. */
    #left-tree, #centre-tree, #detail {
        width: 1fr;
        background: $surface;
    }

    #left-tree, #centre-tree {
        border-right: solid $panel-lighten-2;
    }

    #left-tree:focus, #centre-tree:focus {
        background-tint: $surface 0%;
    }

    #detail {
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

    # The left column's selection: the id of the Thing whose siblings the left
    # tree shows and which roots the centre tree. ``init=False`` keeps the
    # watcher from firing for the initial ``None`` before the vault is loaded;
    # ``on_mount`` assigns the first real selection.
    selected_id: reactive[str | None] = reactive(None, init=False)

    # The centre column's active item: the id of the Thing shown in the right
    # (detail) column. Reset to ``selected_id`` on every left-selection change
    # (see :meth:`watch_selected_id`), then moved independently by selecting a
    # node in the centre tree. The detail-pane work item watches this (see module
    # docstring). ``init=False`` mirrors ``selected_id`` so the watcher stays
    # quiet until the first real selection cascades into it.
    active_id: reactive[str | None] = reactive(None, init=False)

    def __init__(self, lot_cli: LotCli | None = None) -> None:
        super().__init__()
        self._lot_cli = lot_cli if lot_cli is not None else LotCli()
        # The merged effective config from `lot settings get`, loaded on mount.
        # Defaults to an empty config so it is always a valid model even before
        # (or if) the CLI load fails. See :meth:`_apply_config`.
        self._config = EffectiveConfig()
        # Status colours for the configured update types, recomputed whenever
        # config (re)loads so the tree colours whatever types the vault
        # defines. Starts from the stock set's colours.
        self._status_colors: dict[str, str] = dict(DEFAULT_STATUS_COLORS)
        # Indexes over the whole vault (id -> Thing, id -> parent, the root
        # list), built on load and patched incrementally by watch events. Pure
        # data-structure logic, extracted so it is unit-testable on its own.
        self._index = VaultIndex()
        # The vault currently targeted, tracked so a *failed* switch can revert
        # the shared adapter to it. Seeded from ``config.vault_path`` on mount and
        # updated on every successful switch (see :meth:`action_switch_vault`).
        self._active_vault_path: str = ""
        # The `lot help --format=yaml` tree, discovered lazily the first time
        # the command navigator opens and cached (see :meth:`_open_command_nav`).
        self._help_tree: dict | None = None
        # Set while a left-tree *cursor* move drives the selection, so
        # :meth:`watch_selected_id` skips the left-tree rebuild that would yank
        # the cursor back to the top. See :meth:`_select_node`.
        self._suppress_left_rebuild = False
        # The multi-select mark set: ids of the Things batch operations act on.
        # Kept consistent with the index — ids that leave ``_by_id`` (archive,
        # deletion, a vault switch) are pruned so a mark can never point at a
        # Thing that no longer exists. See the "multi-select marks" section.
        self._marked: set[str] = set()
        # Guards the theme-persistence watcher (see :meth:`on_mount`) against
        # *programmatic* theme changes: applying the configured theme on launch,
        # or a theme carried by a vault switched into, sets it via
        # :meth:`_apply_theme`, which raises this flag so the watcher does not
        # write that value straight back to config. Only a deliberate runtime
        # pick (the palette's "Switch theme") persists. See :meth:`_persist_theme`.
        self._suppress_theme_persist = False

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            yield WrappingTree("LoT", id="left-tree")
            yield WrappingTree("Descendants", id="centre-tree")
            with Container(id="detail"):
                yield DetailPane(self._lot_cli)
        yield Footer()

    async def on_mount(self) -> None:
        """Load config + the vault, select the vault root, focus the left tree."""
        # Clicking (or selecting) a branch must only *select* it, never fold
        # it. Textual's Tree otherwise toggles a branch on every select (its
        # ``auto_expand`` default), so turn that off on both trees. The trees
        # draw no fold arrows (see WrappingTree) and every node is added
        # expanded, so both columns read as fixed, fully-expanded outlines.
        for tree_id in ("#left-tree", "#centre-tree"):
            self.query_one(tree_id, Tree).auto_expand = False
        # The left tree's "LoT" root row stands for the vault as a whole:
        # carrying the VAULT_ROOT sentinel makes it selectable like any Thing
        # row (see _select_node), rooting the centre column at the full vault.
        # Tree.clear() (used by the rebuilds) keeps the root node, so this
        # assignment survives every repaint.
        self.query_one("#left-tree", Tree).root.data = VAULT_ROOT
        # Config first, so the configured theme is applied before the first paint.
        await self._apply_config()
        # Only now — after the *configured* theme has been applied — start
        # watching ``theme`` for changes to persist. Attaching it here (rather
        # than overriding Textual's own ``watch_theme``) means the mount-time
        # application above never triggers a write-back; subsequent runtime picks
        # do (guarded by ``_suppress_theme_persist`` for vault-switch reapplies).
        self.watch(self, "theme", self._on_theme_changed, init=False)
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        # Initial selection: the vault root, so the app opens on the whole
        # vault — the left cursor starts on the "LoT" row (Textual initialises
        # the tree cursor to the top line), and the centre column shows the
        # full tree. Any other initial selection would fight the
        # NodeHighlighted the cursor initialisation emits for the root row.
        self.selected_id = VAULT_ROOT
        # Start focus in the left column so vim motions have a deterministic
        # home; ``h``/``l`` walk focus from here across the columns.
        self.query_one("#left-tree", Tree).focus()
        # Baseline is loaded; now apply external changes live off `lot watch`.
        self._watch_vault()

    # --- config & theme ----------------------------------------------------
    # Lives in :class:`~lot_textual_ui.config_theme.ConfigThemeMixin`.

    # --- vault switching ---------------------------------------------------
    # Lives in :class:`~lot_textual_ui.vault_switching.VaultSwitchingMixin`.

    # --- live updates ------------------------------------------------------
    # Lives in :class:`~lot_textual_ui.watch.WatchMixin`.

    # --- keyboard/mouse navigation -----------------------------------------
    # Lives in :class:`~lot_textual_ui.navigation.NavigationMixin`.

    # --- copy to clipboard -------------------------------------------------
    # Lives in :class:`~lot_textual_ui.clipboard.ClipboardMixin`.

    # --- expand / collapse updates -----------------------------------------
    #
    # Each UpdateItem in the detail thread can be collapsed to just its header.
    # The toggle key acts on whichever update is focused (UpdateItems join the
    # Tab order); the two palette commands collapse/expand the whole thread.

    def action_toggle_update(self) -> None:
        """Collapse/expand the focused update (or the current one as fallback).

        Bound to a dedicated key (see :mod:`lot_textual_ui.keys`). When an
        :class:`~lot_textual_ui.detail.UpdateItem` holds focus it is toggled;
        otherwise the pane's current (last-focused, else latest) update is, so
        the key still does something useful when focus sits on the pane itself.
        """
        focused = self.focused
        if isinstance(focused, UpdateItem):
            focused.toggle()
            return
        pane = self.query_one(DetailPane)
        current = pane.current_update_id
        if current is None:
            return
        for item in pane.query(UpdateItem):
            if item.update_id == current:
                item.toggle()
                return

    def action_collapse_all_updates(self) -> None:
        """Collapse every update in the thread to its header (palette command)."""
        self.query_one(DetailPane).set_all_collapsed(True)

    def action_expand_all_updates(self) -> None:
        """Expand every update in the thread to show its body (palette command)."""
        self.query_one(DetailPane).set_all_collapsed(False)

    # --- key-bindings help panel ---------------------------------------------
    #
    # Textual's own `HelpPanel` (a summary of the focused widget's bindings) is
    # already reachable via the `ctrl+p` palette's built-in "Keys" system
    # command (see `App.get_system_commands`), which picks
    # `action_show_help_panel`/`action_hide_help_panel` depending on whether one
    # is already mounted. This gives the same chord a direct binding (see
    # `keys.py`) by mirroring that same query-and-pick logic as a single
    # toggle action, rather than only ever showing the panel.

    def action_toggle_help_panel(self) -> None:
        """Show the keys/bindings help panel, or hide it if already shown.

        Mirrors the palette's "Keys" system command's own logic
        (`App.get_system_commands`): if a `HelpPanel` is already mounted on the
        current screen, remove it; otherwise mount one.
        """
        if self.screen.query("HelpPanel"):
            self.action_hide_help_panel()
        else:
            self.action_show_help_panel()

    # --- multi-select marks & batch operations ------------------------------
    # Live in :class:`~lot_textual_ui.batch_actions.BatchActionsMixin`.

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
        return self._index.by_id.get(thing_id)

    # --- commands (navigator / palette dispatch / forms entry points) -------
    # Live in :class:`~lot_textual_ui.commands.CommandsMixin`.

    # --- vault reload --------------------------------------------------------

    async def _reload_vault(self) -> None:
        """Re-read the full ``thing_list`` baseline and repaint, keeping selection.

        Mirrors the ``reload`` path in :meth:`_apply_event`: rebuild the index
        from a fresh listing, then re-resolve the selection and repaint the
        minimum (forcing a detail reload for the current selection so freshly
        written updates appear).
        """
        previous = self.selected_id
        old_parent = (
            self._index.parent_of.get(previous) if previous is not None else None
        )
        old_parent_id = old_parent.id if old_parent is not None else None
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        self._refresh_after(previous, old_parent_id, changed_id=previous)

    # --- selection model ---------------------------------------------------

    @property
    def current_thing_id(self) -> str | None:
        """The Thing currently in view — the centre column's active item.

        This is what the right/detail column shows, so it is also what the
        Thing-scoped actions target (copy Thing URI/path, add update, add child):
        they act on the Thing the user is actually looking at, not the left
        column's root. Falls back to :attr:`selected_id` before any active item
        is set — except the vault root (:data:`VAULT_ROOT`), which is not a
        Thing, so with it selected and no active item there is nothing in view.
        """
        if self.active_id is not None:
            return self.active_id
        return self.selected_id if self.selected_id != VAULT_ROOT else None

    def _require_current_thing(self, message: str, title: str) -> str | None:
        """The in-view Thing's id for a Thing-scoped action, or ``None`` + a hint.

        The one guard behind every action that needs a current Thing (copy
        URI/path, add child/update, send to Claude — cf. :meth:`_require_marked`
        for the batch actions): returns :attr:`current_thing_id` when a Thing is
        in view, else toasts ``message`` (as a warning titled ``title``) and
        returns ``None`` for the caller to bail on.
        """
        thing_id = self.current_thing_id
        if thing_id is None:
            self.notify(message, title=title, severity="warning")
        return thing_id

    def watch_selected_id(self, old: str | None, new: str | None) -> None:
        """Re-derive the left and centre trees, and reset the centre's active item.

        A new left selection re-roots the centre column at ``new`` and makes it
        the centre's active item too, so the right column starts on the newly
        selected Thing. Assigning :attr:`active_id` fires
        :meth:`watch_active_id` (which highlights the centre node) and the detail
        pane's own watcher (which reloads it). Selecting the vault root
        (:data:`VAULT_ROOT`) is the exception: it is not a Thing, so the active
        item clears (emptying the detail pane) until a centre item is chosen.

        The left tree is rebuilt unless the change came from the left tree's own
        cursor (:attr:`_suppress_left_rebuild`): the cursor already sits on the
        selected node, so rebuilding would only reset it to the top. The centre
        column is always re-rooted regardless.
        """
        if not self._suppress_left_rebuild:
            self._rebuild_left_tree(new)
        self._rebuild_centre_tree(new)
        self.active_id = None if new == VAULT_ROOT else new

    def watch_active_id(self, old: str | None, new: str | None) -> None:
        """Highlight the active item in the centre tree on change.

        The right column (:class:`~lot_textual_ui.detail.DetailPane`) refreshes
        itself by watching ``active_id`` directly, so it is not touched here.

        ``active_id`` (via :attr:`current_thing_id`) is what
        ``CommandsMixin.check_action`` gates the copy-Thing footer hints
        (``copy_thing_uri``/``copy_thing_path``) on, so :meth:`refresh_bindings`
        repaints the footer here too — otherwise selecting a Thing without also
        moving focus (the copy hints' gate does not depend on focus) would leave
        a stale footer until some unrelated focus change happened to trigger
        Textual's own ``refresh_bindings`` call.
        """
        self._highlight_centre(new)
        self.refresh_bindings()

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        """Follow the cursor: the item *under* it becomes the column's selection.

        Any cursor move — ``j``/``k``, ``g``/``G``, arrow keys, a click — emits
        ``NodeHighlighted``, so acting on it here selects the highlighted item
        without a separate confirm keypress (the behaviour Enter used to be
        required for). Routing matches :meth:`on_tree_node_selected`: a left-tree
        highlight moves the left selection (re-rooting the centre column), a
        centre-tree highlight moves only the centre's active item.

        This does not loop. Our own programmatic ``move_cursor`` calls — a tree
        rebuild's re-cursor and :meth:`_highlight_centre` — re-emit this for the
        node we just selected, so the resulting assignment writes the *same* id
        back to the reactive, which no-ops rather than firing another rebuild.
        """
        self._select_node(event.node)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        """Route an explicit selection (Enter or click) to its column.

        Highlighting already follows the cursor (see
        :meth:`on_tree_node_highlighted`), so this now mostly reaffirms the
        current selection; it still matters when Enter/click lands without moving
        the cursor (re-selecting the already-highlighted node).
        """
        self._select_node(event.node)

    def _select_node(self, node: TreeNode[str]) -> None:
        """Assign the selection reactive that ``node``'s column owns.

        A node in the **left** tree moves the left selection (and re-roots the
        centre column); a node in the **centre** tree moves only the centre's
        active item, leaving the left column exactly where it is. The left
        tree's ``LoT`` root carries :data:`VAULT_ROOT`, so cursoring onto it
        selects the whole vault like any other row. Nodes with no data at all
        (the centre tree's root when the vault root is selected) are ignored.

        The cursor already sits on ``node``, so a left selection suppresses
        :meth:`watch_selected_id`'s left-tree rebuild (which would reset the
        cursor to the top); only the centre column is re-rooted off the new
        selection.
        """
        thing_id = node.data
        if thing_id is None:
            return
        left_tree = self.query_one("#left-tree", Tree)
        if node.tree is left_tree:
            if thing_id == self.selected_id:
                return
            self._suppress_left_rebuild = True
            try:
                self.selected_id = thing_id
            finally:
                self._suppress_left_rebuild = False
        else:
            self.active_id = thing_id

    # --- derivation --------------------------------------------------------
    #
    # The index itself (id -> Thing / id -> parent / roots, upserts, subtree
    # removal, selection resolution) is pure data-structure logic and lives in
    # :class:`~lot_textual_ui.index.VaultIndex`; the app only bundles a rebuild
    # with its own mark pruning here.

    def _reindex(self, things: list[Thing]) -> None:
        """Rebuild the vault index from a fresh listing, then prune marks.

        Marks are UI state keyed by Thing id, so every wholesale rebuild —
        mount, vault switch, a ``reload`` watch event, :meth:`_reload_vault` —
        re-validates them against the new index (see :meth:`_prune_marks`).
        """
        self._index.reindex(things)
        self._prune_marks()

    # --- rendering ---------------------------------------------------------

    def _rebuild_left_tree(self, selected_id: str | None) -> None:
        """Rebuild the left tree: the whole vault's root and branch Things.

        Every root Thing and every branch (a Thing with children) is shown,
        nested by parentage; leaf Things (no children) are omitted — the centre
        column reaches them by rooting at their branch. The selected Thing is
        highlighted when present (it always is: a left selection is a root, a
        branch, or the vault root — whose :data:`VAULT_ROOT` sentinel the tree's
        own root node carries), which does not re-fire ``NodeSelected``
        (``move_cursor`` emits only ``NodeHighlighted``, which we treat as a
        no-op for the same id).
        """
        tree = self.query_one("#left-tree", Tree)
        tree.clear()
        # clear() keeps the cursor *line*, which now points at an arbitrary row
        # of the new tree (or past its end); Textual's next idle re-layout would
        # reconcile that stale line and emit a NodeHighlighted for whatever node
        # happens to sit there, stealing the selection. Park the cursor
        # event-lessly until the deferred restore below lands it on the real
        # selected node.
        tree.unselect()
        tree.root.expand()
        for root in self._index.roots:
            self._add_left_subtree(tree.root, root)
        if selected_id is not None:
            # Freshly added nodes have no line numbers until the tree next
            # lays itself out, so an immediate move_cursor would clamp to
            # the top row (the LoT root) — and, since that row is selectable,
            # its NodeHighlighted would steal the selection. Deferring past
            # the refresh moves the cursor onto the real node; the
            # NodeHighlighted that emits carries the already-selected id,
            # which _select_node treats as a no-op. The node is re-found by id
            # at fire time in case another rebuild replaced it meanwhile.
            self.call_after_refresh(self._restore_left_cursor)

    def _restore_left_cursor(self) -> None:
        """Move the left cursor onto the selected node (deferred past refresh).

        Reads :attr:`selected_id` at fire time — not the id captured when the
        rebuild scheduled it — so back-to-back rebuilds all converge on the
        current selection rather than replaying stale ones.
        """
        selected_id = self.selected_id
        if selected_id is None:
            return
        tree = self.query_one("#left-tree", Tree)
        node = self._find_node(tree.root, selected_id)
        if node is not None:
            tree.move_cursor(node)

    def _set_name_offset(self, node: TreeNode[str], thing: Thing) -> None:
        """Tell the tree how wide ``thing``'s fixed label columns are.

        So the :class:`~lot_textual_ui.wrapping_tree.WrappingTree` keeps the
        mark/status columns on the node's first row and wraps only the name
        under itself (see :func:`label_name_offset`). A no-op on a plain tree.
        """
        tree = node.tree
        if isinstance(tree, WrappingTree):
            tree.set_name_offset(node, label_name_offset(thing))

    def _add_left_subtree(self, parent_node: TreeNode[str], thing: Thing) -> None:
        """Add ``thing`` and its branch descendants to the left tree.

        Every root reaches here (so a childless root still shows); a non-root
        Thing is only reached when it is itself a branch. A Thing whose only
        children are leaves is added as a leaf node — it shows, but its leaf
        children do not — so the tree is the vault's root/branch skeleton.
        """
        branches = [child for child in thing.children if child.children]
        if branches:
            node = parent_node.add(self._node_label(thing), data=thing.id, expand=True)
            for branch in branches:
                self._add_left_subtree(node, branch)
        else:
            node = parent_node.add_leaf(self._node_label(thing), data=thing.id)
        self._set_name_offset(node, thing)

    def _rebuild_centre_tree(self, selected_id: str | None) -> None:
        tree = self.query_one("#centre-tree", Tree)
        tree.clear()
        # As in _rebuild_left_tree: park the stale cursor event-lessly so the
        # rebuild cannot emit a NodeHighlighted for an arbitrary row — here that
        # would reassign active_id, yanking the detail pane off the Thing the
        # user was looking at. _highlight_centre re-lands it on the active node.
        tree.unselect()
        if selected_id == VAULT_ROOT:
            # The vault root is selected: show the whole vault — every root
            # Thing with all of its descendants. The root row mirrors the left
            # tree's "LoT" label but carries no Thing id, so cursoring onto it
            # never moves the active item.
            tree.root.set_label("LoT")
            tree.root.data = None
            if isinstance(tree, WrappingTree):
                tree.set_name_offset(tree.root, 0)
            tree.root.expand()
            for root in self._index.roots:
                self._add_subtree(tree.root, root)
            return
        selected = self.thing_by_id(selected_id)
        if selected is None:
            tree.root.set_label("Descendants")
            tree.root.data = None
            # The reused root object may still carry a previous selection's name
            # offset; the plain "Descendants" label has no fixed columns.
            if isinstance(tree, WrappingTree):
                tree.set_name_offset(tree.root, 0)
            return

        tree.root.set_label(self._node_label(selected))
        tree.root.data = selected.id
        self._set_name_offset(tree.root, selected)
        tree.root.expand()
        for child in selected.children:
            self._add_subtree(tree.root, child)

    def _highlight_centre(self, active_id: str | None) -> None:
        """Move the centre tree's cursor to the active node, if present.

        The move is deferred past the next refresh (like the left tree's
        rebuild restore): the centre is usually freshly rebuilt here, and an
        immediate ``move_cursor`` would read the new nodes' still-unassigned
        line numbers (-1), clamp the cursor to the top row, and emit a
        ``NodeHighlighted`` for the centre *root* — reassigning ``active_id``
        to it and switching the detail pane off the Thing the user was on.
        After the refresh the lines are real, the cursor lands on the active
        node, and the ``NodeHighlighted`` it emits carries the already-active
        id, which ``_select_node`` treats as a no-op. A ``None`` id leaves the
        cursor as-is.
        """
        if active_id is None:
            return
        self.call_after_refresh(self._restore_centre_cursor)

    def _restore_centre_cursor(self) -> None:
        """Move the centre cursor onto the active node (deferred past refresh).

        Reads :attr:`active_id` at fire time so queued restores all converge on
        the current active item. An id not in the centre tree (the active item
        lives outside the rooted subtree) leaves the cursor as-is.
        """
        active_id = self.active_id
        if active_id is None:
            return
        tree = self.query_one("#centre-tree", Tree)
        node = self._find_node(tree.root, active_id)
        if node is not None:
            tree.move_cursor(node)

    def _find_node(self, node: TreeNode[str], data: str) -> TreeNode[str] | None:
        """Depth-first search for the node carrying ``data`` under ``node``."""
        if node.data == data:
            return node
        for child in node.children:
            found = self._find_node(child, data)
            if found is not None:
                return found
        return None

    def _add_subtree(self, parent_node: TreeNode[str], thing: Thing) -> None:
        if thing.children:
            node = parent_node.add(self._node_label(thing), data=thing.id, expand=True)
            for child in thing.children:
                self._add_subtree(node, child)
        else:
            node = parent_node.add_leaf(self._node_label(thing), data=thing.id)
        self._set_name_offset(node, thing)


def main() -> None:
    """Console-script entry point: run the Textual app."""
    LotTextualApp().run()


if __name__ == "__main__":
    main()
