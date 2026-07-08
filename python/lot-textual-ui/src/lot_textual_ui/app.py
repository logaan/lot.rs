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

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .batch import TOP_LEVEL, ConfirmScreen, ThingPickerScreen
from .clipboard import ClipboardMixin
from .command_nav import RESERVED_CTRL_LETTERS, CommandNav, CommandNavScreen
from .config_theme import ConfigThemeMixin
from .detail import DetailPane, UpdateItem
from .forms import BatchUpdateScreen, NewThingScreen, NewUpdateScreen
from .index import VAULT_ROOT, VaultIndex
from .keys import ACTION_BINDINGS
from .lot_cli import LotCli, LotError
from .models import (
    EffectiveConfig,
    Thing,
    UpdateType,
    default_update_types,
)
from .navigation import NavigationMixin
from .palette import PALETTE_PROVIDERS, LeafCommand
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


# The colours for the stock types: the fallback before config loads (and for
# callers that pass no map).
DEFAULT_STATUS_COLORS = status_colors(default_update_types())

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
    stock set's colours.
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

    # --- multi-select marks --------------------------------------------------
    #
    # Multi-select is a set of marked Thing ids (`_marked`) the batch actions
    # below operate on. Marking is per-Thing, not per-row: a Thing shown in
    # both tree columns is marked in both at once. Marked rows carry the
    # MARK_INDICATOR glyph (see `node_label`); labels are re-rendered in place
    # so toggling never rebuilds a tree (and so never disturbs its cursor).

    @property
    def marked_ids(self) -> frozenset[str]:
        """The ids of the currently marked Things (a read-only snapshot)."""
        return frozenset(self._marked)

    def _node_label(self, thing: Thing) -> Text:
        """A tree label for ``thing``, mark-aware (see :func:`node_label`)."""
        return node_label(
            thing, marked=thing.id in self._marked, colors=self._status_colors
        )

    def _cursor_thing_id(self) -> str | None:
        """The Thing the mark toggle targets: under the focused tree's cursor.

        With focus on either tree column this is the highlighted node's Thing;
        with focus elsewhere (the detail pane) it falls back to the in-view
        Thing (:attr:`current_thing_id`) so the key still does something
        sensible. ``None`` when there is nothing to target.
        """
        target = self._nav_target()
        if isinstance(target, Tree):
            node = target.cursor_node
            if node is not None and isinstance(node.data, str):
                return node.data
            return None
        return self.current_thing_id

    def action_toggle_mark(self) -> None:
        """Toggle the multi-select mark on the highlighted Thing."""
        thing_id = self._cursor_thing_id()
        if thing_id is None or thing_id not in self._index.by_id:
            self.notify(
                "Move the cursor onto a Thing first.",
                title="Nothing to mark",
                severity="warning",
            )
            return
        if thing_id in self._marked:
            self._marked.discard(thing_id)
        else:
            self._marked.add(thing_id)
        self._refresh_mark_indicators({thing_id})

    def action_clear_marks(self) -> None:
        """Drop every multi-select mark."""
        if not self._marked:
            return
        cleared = set(self._marked)
        self._marked.clear()
        self._refresh_mark_indicators(cleared)

    def _refresh_mark_indicators(self, ids: set[str] | None = None) -> None:
        """Re-render the labels of (the given) Things in both tree columns.

        ``ids=None`` refreshes every Thing-carrying node. Labels are set in
        place — no tree is rebuilt, so cursors and expansion are untouched.
        """
        for tree_id in ("#left-tree", "#centre-tree"):
            tree = self.query_one(tree_id, Tree)
            self._relabel(tree.root, ids)

    def _relabel(self, node: TreeNode[str], ids: set[str] | None) -> None:
        thing = self._index.by_id.get(node.data) if isinstance(node.data, str) else None
        if thing is not None and (ids is None or thing.id in ids):
            node.set_label(self._node_label(thing))
        for child in node.children:
            self._relabel(child, ids)

    def _prune_marks(self) -> None:
        """Drop marks whose Things are no longer in the index.

        Called from every index rebuild (:meth:`_reindex`) and single-node
        removal (:meth:`_remove_subtree`), so after an archive batch — or an
        external deletion arriving via ``lot watch``, or a vault switch — the
        mark set never references a vanished Thing.
        """
        self._marked &= set(self._index.by_id)

    # --- batch operations over the marked set --------------------------------
    #
    # Each batch action collects its remaining input through a modal (a
    # destination picker, a confirmation, the batch-update form), then runs
    # the per-item `lot` calls **sequentially** in one worker (`_run_batch`):
    # progress is shown in the header subtitle, a failed item never aborts the
    # rest, successes are unmarked as they land, and at the end the vault is
    # reloaded and a summary (with every failure's Thing and error) is shown.
    # Failed items stay marked so they can be retried after fixing the cause.

    def _marked_in_order(self) -> list[str]:
        """The marked ids in tree order (the index is built by a tree walk)."""
        return [thing_id for thing_id in self._index.by_id if thing_id in self._marked]

    def _require_marked(self, verb: str) -> list[str] | None:
        """The marked set for a batch action, or ``None`` (+ a hint) if empty."""
        ids = self._marked_in_order()
        if not ids:
            self.notify(
                f"Mark some Things first (press 'x' on them), then {verb}.",
                title="Nothing marked",
                severity="warning",
            )
            return None
        return ids

    def action_batch_move(self) -> None:
        """Move every marked Thing under a picked destination (or the root).

        Opens :class:`~lot_textual_ui.batch.ThingPickerScreen` over the whole
        vault tree plus a "Top level" entry. The marked Things themselves are
        excluded (a Thing cannot be its own destination); a destination inside
        one marked subtree is still offered, because it may be valid for the
        *other* marked Things — the CLI rejects the cyclic ones and those show
        up in the per-item failure report.
        """
        ids = self._require_marked("run Move marked Things")
        if ids is None:
            return
        self.push_screen(
            ThingPickerScreen(self._index.roots, exclude=set(ids)),
            self._move_target_chosen,
        )

    def _move_target_chosen(self, target: str | None) -> None:
        """Run the batch move to the picker's destination (``None`` = cancel)."""
        if target is None:
            return

        if target == TOP_LEVEL:

            async def move(thing_id: str) -> str:
                return await self._lot_cli.thing_move(thing_id, root=True)

        else:

            async def move(thing_id: str) -> str:
                return await self._lot_cli.thing_move(thing_id, parent=target)

        self._run_batch("Move", move, self._marked_in_order())

    def action_batch_archive(self) -> None:
        """Archive every marked Thing, after a count-confirming dialog.

        Archiving removes each Thing *and all its descendants* from the vault
        (history stays in git), so the confirmation states the count plainly.
        The CLI refuses to archive when ``vault.auto-commit`` is ``false``;
        that error text is surfaced per item like any other failure.
        """
        ids = self._require_marked("run Archive marked Things")
        if ids is None:
            return
        count = len(ids)
        plural = "s" if count != 1 else ""
        self.push_screen(
            ConfirmScreen(
                f"Archive {count} marked Thing{plural}? Each is removed from "
                "the vault together with all of its descendant Things "
                "(history is preserved in git).",
                title="Archive marked Things",
                confirm_label="Archive",
            ),
            self._archive_confirmed,
        )

    def _archive_confirmed(self, confirmed: bool | None) -> None:
        """Run the batch archive once the dialog confirms it."""
        if not confirmed:
            return
        self._run_batch("Archive", self._lot_cli.thing_archive, self._marked_in_order())

    def action_vault_archive(self) -> None:
        """Archive every done Thing in the vault, after a confirming dialog.

        Unlike the batch actions this needs no marks: it runs one
        ``lot vault archive`` (readme §5.4.2), which itself finds every Thing
        in a terminal status (an update type with ``terminal = true``, like
        the stock ``done``), commits them, and commits all their deletions in
        a single commit. The CLI refuses when ``vault.auto-commit`` is
        ``false``; that error text is surfaced in the failure toast.
        """
        self.push_screen(
            ConfirmScreen(
                "Archive every done Thing in the vault? Each Thing in a "
                "terminal status is removed "
                "together with all of its descendant Things "
                "(history is preserved in git).",
                title="Archive done Things",
                confirm_label="Archive",
            ),
            self._vault_archive_confirmed,
        )

    def _vault_archive_confirmed(self, confirmed: bool | None) -> None:
        """Run the vault-wide archive once the dialog confirms it."""
        if not confirmed:
            return
        self._run_vault_archive()

    @work(exclusive=True, group="batch")
    async def _run_vault_archive(self) -> None:
        """Run ``lot vault archive``, then reload the vault and report.

        Shares the ``batch`` worker group (and its exclusivity) with
        :meth:`_run_batch`: a vault-wide archive is a mutation of the same
        kind, so it must never run concurrently with a batch. It is a single
        CLI call rather than a per-item loop — the CLI owns finding the done
        Things and making the one deletion commit — so failure reporting is a
        single toast carrying the CLI's error text.
        """
        self.sub_title = "Archive done Things…"
        try:
            archived = await self._lot_cli.vault_archive()
        except LotError as error:
            self._update_vault_subtitle()
            self.notify(
                str(error),
                title="Archive done Things",
                severity="error",
                timeout=12,
            )
            return

        await self._reload_vault()
        self._refresh_mark_indicators()
        self._update_vault_subtitle()

        if archived:
            plural = "s" if len(archived) != 1 else ""
            self.notify(
                f"Archived {len(archived)} done Thing{plural}.",
                title="Archive done Things",
            )
        else:
            self.notify(
                "No done Things to archive.",
                title="Archive done Things",
            )

    def action_batch_update(self) -> None:
        """Append one new Update to every marked Thing.

        Opens :class:`~lot_textual_ui.forms.BatchUpdateScreen` — the batch
        variant of the new-Update form — once; the collected type + body are
        then applied to each marked Thing in turn (e.g. mark a handful of
        finished tasks and record one ``done`` across all of them).
        """
        ids = self._require_marked("run Update marked Things")
        if ids is None:
            return
        self.push_screen(
            BatchUpdateScreen(len(ids), update_types=self.creatable_update_types()),
            self._batch_update_submitted,
        )

    def _batch_update_submitted(self, result: tuple[str, str | None] | None) -> None:
        """Apply the collected Update to every marked Thing (``None`` = cancel).

        The form dismisses with the validated ``(kind, body)`` pair — ``body``
        is ``None`` for a ``takes-body = false`` type — which maps straight
        onto :meth:`LotCli.add_update` for every kind, built-in or custom.
        """
        if result is None:
            return
        kind, body = result

        async def add_update(thing_id: str) -> str:
            return await self._lot_cli.add_update(kind, thing_id, body)

        self._run_batch("Update", add_update, self._marked_in_order())

    @work(exclusive=True, group="batch")
    async def _run_batch(
        self,
        label: str,
        operation: Callable[[str], Awaitable[str]],
        ids: list[str],
    ) -> None:
        """Run one batch operation sequentially with per-item error reporting.

        Items run strictly one after another (the vault is git-backed; parallel
        mutations would race its lock and commits). A failure is recorded —
        Thing name plus the CLI's error text — and the batch *continues*; the
        failed Thing keeps its mark so the batch can be re-run after fixing the
        cause, while each success is unmarked immediately. Progress is shown in
        the header subtitle. Afterwards the vault is reloaded wholesale (one
        coherent repaint rather than N incremental ``lot watch`` patches; the
        reload also re-resolves a selection whose Thing was archived away and
        prunes marks for vanished Things), the subtitle is restored, and a
        summary — every failure spelled out — is toasted.
        """
        total = len(ids)
        # Capture names up front: a moved/archived Thing may be gone from the
        # index by the time the failure report is rendered.
        names = {
            thing_id: (
                thing.name if (thing := self._index.by_id.get(thing_id)) else thing_id
            )
            for thing_id in ids
        }
        failures: list[tuple[str, str]] = []
        for index, thing_id in enumerate(ids, start=1):
            self.sub_title = f"{label}: {index}/{total}…"
            try:
                await operation(thing_id)
            except LotError as error:
                failures.append((names[thing_id], str(error)))
            else:
                self._marked.discard(thing_id)

        await self._reload_vault()
        self._refresh_mark_indicators()
        self._update_vault_subtitle()

        succeeded = total - len(failures)
        if failures:
            detail = "\n".join(f"• {name}: {message}" for name, message in failures)
            self.notify(
                f"{succeeded} of {total} succeeded; {len(failures)} failed "
                f"(still marked):\n{detail}",
                title=f"{label} marked Things",
                severity="error",
                timeout=12,
            )
        else:
            plural = "s" if total != 1 else ""
            self.notify(
                f"{label}: {total} Thing{plural} processed.",
                title=f"{label} marked Things",
            )

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

    # --- command navigator (Space / Ctrl+letter) ----------------------------
    #
    # The hierarchical command selector (see :mod:`lot_textual_ui.command_nav`):
    # ``space`` opens it at the top level of the discovered ``lot`` command
    # tree, and ``ctrl+<first letter of a top-level command>`` opens it already
    # inside that command (``ctrl+t`` → ``lot thing``, then ``n`` runs
    # ``lot thing new``). A picked leaf runs through :meth:`run_lot_command`,
    # exactly like a fuzzy-palette pick.

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

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate base-screen-only actions while any modal screen is on top.

        ``command_nav``'s binding is ``priority=True`` so it beats the focused
        :class:`~textual.widgets.Tree`'s own space-to-toggle — but priority
        bindings also fire while a modal (a form with text inputs, the
        navigator itself) is on top, where a typed space must stay a space. So
        the action is disabled whenever any screen is pushed. The multi-select
        and batch actions are gated the same way (see
        :data:`_BASE_SCREEN_ACTIONS`); every other action passes through
        untouched.
        """
        if len(self.screen_stack) > 1 and (
            action == "command_nav" or action in self._BASE_SCREEN_ACTIONS
        ):
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
          the Thing, is the one the user is looking at); other input-needing
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
    async def _new_thing_created(self, new_id: str | None) -> None:
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
        """
        if new_id is None:
            return
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
        """
        self._highlight_centre(new)

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
