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

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal
from textual.reactive import reactive
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .detail import DetailPane
from .lot_cli import LotCli
from .models import Thing

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

    BINDINGS = [
        ("q", "quit", "Quit"),
    ]

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
        """Load the vault and select an initial Thing."""
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        # Initial selection: the first top-level Thing, if any.
        self.selected_id = self._roots[0].id if self._roots else None

    # --- public API for sibling widgets (e.g. the detail pane) -------------

    def thing_by_id(self, thing_id: str | None) -> Thing | None:
        """Return the Thing with ``thing_id``, or ``None`` if unknown."""
        if thing_id is None:
            return None
        return self._by_id.get(thing_id)

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
