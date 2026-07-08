"""The in-memory sort order for the Thing trees: pure, view-only logic.

The two tree columns render the vault index's sibling lists, which the index
keeps in a single canonical (name) order. Display order is layered on top of
that as a *view transform* — this module — so the sort is purely presentational
and lives only in memory (it is never persisted, and the index is never
reordered). :class:`~lot_textual_ui.app.LotTextualApp` holds the current
:class:`SortMode`, cycles it on a keypress, and calls :func:`sort_things` on
each level of siblings as it builds the trees.

There is no Textual dependency here — only :class:`~lot_textual_ui.models.Thing`
— so the whole ordering is unit-testable as plain data.
"""

from __future__ import annotations

from enum import Enum

from .models import Thing


class SortMode(Enum):
    """How the tree columns order each list of siblings.

    * :attr:`STATUS` — group by status, in the vault's configured
      ``update-types`` order (so, with the stock set, ``note`` before ``work``
      before ``info`` before ``done``); the default. Within a status the
      incoming order is kept, which the app supplies name-sorted (the index
      holds every sibling list in name order), so a status group reads
      alphabetically.
    * :attr:`RECENT` — most recently active first, where a Thing's activity is
      the newest update anywhere in its subtree (itself or any descendant), so a
      parent floats up whenever any child moves.
    * :attr:`NAME` — plain alphabetical (case-insensitive).
    """

    STATUS = "status"
    RECENT = "recent"
    NAME = "name"


# The order the sort key cycles through, starting from the default. Kept as an
# explicit tuple (rather than relying on Enum definition order) so the cycle is
# one obvious thing to reorder.
SORT_CYCLE: tuple[SortMode, ...] = (SortMode.STATUS, SortMode.RECENT, SortMode.NAME)

# Human-readable labels for the toast shown when the sort is cycled.
SORT_LABELS: dict[SortMode, str] = {
    SortMode.STATUS: "status",
    SortMode.RECENT: "recent activity",
    SortMode.NAME: "name",
}


def next_sort_mode(mode: SortMode) -> SortMode:
    """The next mode in :data:`SORT_CYCLE`, wrapping around at the end."""
    index = SORT_CYCLE.index(mode)
    return SORT_CYCLE[(index + 1) % len(SORT_CYCLE)]


def subtree_recency(thing: Thing) -> str:
    """The newest update timestamp anywhere in ``thing``'s subtree.

    Folds the Thing's own :attr:`~lot_textual_ui.models.Thing.updated` together
    with every descendant's, returning the maximum. A missing timestamp is
    treated as the empty string, which sorts before any real ISO-8601 stamp — so
    a never-updated subtree ranks last under :attr:`SortMode.RECENT`. Timestamps
    are compared as strings: ``lot`` writes them as UTC ISO-8601 (``…+00:00``),
    for which lexicographic order is chronological order.
    """
    newest = thing.updated or ""
    for child in thing.children:
        newest = max(newest, subtree_recency(child))
    return newest


def sort_things(
    things: list[Thing], mode: SortMode, status_order: list[str]
) -> list[Thing]:
    """Return ``things`` ordered for display under ``mode`` (never in place).

    ``status_order`` is the vault's configured status sequence (the
    ``update-types`` names) that :attr:`SortMode.STATUS` groups by; a status not
    in it sorts after every known status. The sort is always stable, so
    :attr:`SortMode.STATUS` keeps the incoming order within a status group (the
    app supplies it name-sorted); :attr:`SortMode.NAME` and
    :attr:`SortMode.RECENT` order explicitly and break ties by name.
    """
    items = list(things)
    if mode is SortMode.NAME:
        items.sort(key=lambda thing: thing.name.casefold())
    elif mode is SortMode.STATUS:
        # Rank by status only and lean on the stable sort to keep the incoming
        # (name) order within a group, rather than adding a name tie-break here.
        rank = {name: index for index, name in enumerate(status_order)}
        unknown = len(rank)
        items.sort(key=lambda thing: rank.get(thing.status, unknown))
    elif mode is SortMode.RECENT:
        # Sort stably by name first, then by subtree recency descending: equal
        # recency (including everything with no timestamp) keeps name order.
        items.sort(key=lambda thing: thing.name.casefold())
        items.sort(key=subtree_recency, reverse=True)
    return items
