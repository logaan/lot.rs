"""The in-memory vault index: pure data-structure logic over ``Thing`` trees.

:class:`VaultIndex` owns the id -> Thing / id -> parent maps and the root list
the app derives its three columns from, plus the incremental patches the
``lot watch`` stream applies (upsert one node, drop a subtree) and the
selection re-resolution that follows them. It has **no Textual dependency** —
it never queries a widget or touches a reactive — so it is unit-testable as a
plain object; the app holds one instance and delegates (see
:class:`~lot_textual_ui.app.LotTextualApp`).

UI concerns stay out on purpose: the multi-select mark set lives on the app,
so :meth:`VaultIndex.remove_subtree` *returns* the removed ids for the app to
prune its marks with, rather than reaching into them.
"""

from __future__ import annotations

from .models import Thing

# The sentinel the left tree's always-visible "LoT" root row carries as its
# node data. Selecting it selects the vault as a whole: the centre column shows
# the full vault tree (every root Thing with all of its descendants) and the
# detail pane empties until a centre item is chosen. Deliberately not a `lot:`
# id, so it can never collide with a real Thing (cf. `batch.TOP_LEVEL`). It
# lives here because selection resolution (:meth:`VaultIndex.resolve_selection`)
# falls back to it; the app re-exports it.
VAULT_ROOT = "__vault-root__"


class VaultIndex:
    """Indexes over the whole vault, kept consistent under incremental patches.

    ``by_id`` maps every Thing id to its (mutable) :class:`Thing`; ``parent_of``
    maps an id to its parent Thing (``None`` for a root); ``roots`` is the
    top-level list, name-sorted like every sibling list. The three are always
    kept in agreement — every mutation goes through :meth:`reindex`,
    :meth:`upsert_node` or :meth:`remove_subtree`.
    """

    def __init__(self) -> None:
        self.by_id: dict[str, Thing] = {}
        self.parent_of: dict[str, Thing | None] = {}
        self.roots: list[Thing] = []

    def reindex(self, things: list[Thing]) -> None:
        """Rebuild the id->Thing and id->parent indexes from the nested tree."""
        self.roots = things
        self.by_id = {}
        self.parent_of = {}

        def walk(items: list[Thing], parent: Thing | None) -> None:
            for thing in items:
                self.by_id[thing.id] = thing
                self.parent_of[thing.id] = parent
                walk(thing.children, thing)

        walk(things, None)

    def upsert_node(
        self,
        thing_id: str,
        name: str,
        status: str,
        parent_id: str | None,
        updated: str | None = None,
    ) -> None:
        """Insert or update a single node, keeping every index consistent.

        A never-seen id creates a fresh (childless) :class:`Thing`, linked under
        its parent (or as a root). A known id updates its
        ``name``/``status``/``updated`` in place — preserving its existing
        ``children`` so descendants survive — and is re-linked only if its parent
        actually moved. ``updated`` is the Thing's most-recent-update timestamp
        (from the watch event's computed state), keeping the recency sort current
        as changes stream in. ``by_id``, ``parent_of`` and the
        ``children``/``roots`` sibling lists are all kept in agreement.
        """
        existing = self.by_id.get(thing_id)
        if existing is None:
            node = Thing(
                id=thing_id, name=name, status=status, children=[], updated=updated
            )
            self.by_id[thing_id] = node
            self.link(node, parent_id)
            return

        existing.name = name
        existing.status = status
        existing.updated = updated
        current_parent = self.parent_of.get(thing_id)
        current_parent_id = current_parent.id if current_parent is not None else None
        if current_parent_id != parent_id:
            self.unlink(existing)
            self.link(existing, parent_id)

    def remove_subtree(self, thing_id: str) -> set[str]:
        """Drop a Thing and all its descendants from every index.

        Returns the set of removed ids so the caller can retire any state of
        its own keyed by them (the app prunes its multi-select marks — mark
        state deliberately does not live here).
        """
        removed: set[str] = set()
        node = self.by_id.get(thing_id)
        if node is None:
            return removed
        for child in list(node.children):
            removed |= self.remove_subtree(child.id)
        self.unlink(node)
        self.by_id.pop(thing_id, None)
        self.parent_of.pop(thing_id, None)
        removed.add(thing_id)
        return removed

    def link(self, node: Thing, parent_id: str | None) -> None:
        """Attach ``node`` under ``parent_id`` (or as a root), name-sorted."""
        parent = self.by_id.get(parent_id) if parent_id is not None else None
        self.parent_of[node.id] = parent
        siblings = parent.children if parent is not None else self.roots
        siblings.append(node)
        siblings.sort(key=lambda thing: thing.name)

    def unlink(self, node: Thing) -> None:
        """Detach ``node`` from its parent's children (or the root list)."""
        parent = self.parent_of.get(node.id)
        siblings = parent.children if parent is not None else self.roots
        siblings[:] = [thing for thing in siblings if thing.id != node.id]

    def left_visible_id(self, thing_id: str) -> str:
        """The nearest Thing shown in the left tree for ``thing_id``.

        The left tree holds only roots and branches, so a leaf never appears
        there. This returns ``thing_id`` itself when it is a root or a branch,
        else its parent's id — the parent is a branch (it has this Thing as a
        child), so it is always left-visible. Used to pick the left selection
        that *contains* a Thing (e.g. jumping to a freshly created leaf child,
        which the centre column then shows). Unknown ids are returned unchanged.
        """
        thing = self.by_id.get(thing_id)
        if thing is None:
            return thing_id
        parent = self.parent_of.get(thing_id)
        if parent is None or thing.children:
            return thing_id
        return parent.id

    def resolve_selection(
        self, previous: str | None, old_parent_id: str | None
    ) -> str | None:
        """Re-resolve a selection against the freshly patched index.

        A still-present selection survives; a vanished Thing falls back to its
        old parent, then the first root, then the vault root (which, unlike a
        Thing, always exists — so after mount the selection is never ``None``).
        """
        if previous == VAULT_ROOT:
            # The vault root is not in the index but always exists; a selection
            # on it survives any vault change.
            return VAULT_ROOT
        if previous is not None and previous in self.by_id:
            return previous
        if old_parent_id is not None and old_parent_id in self.by_id:
            return old_parent_id
        return self.roots[0].id if self.roots else VAULT_ROOT
