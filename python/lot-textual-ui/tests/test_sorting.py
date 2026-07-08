"""Unit tests for the pure tree sort order.

:mod:`lot_textual_ui.sorting` has no Textual dependency, so these exercise it as
plain data — no app shell or fake CLI. The app-level wiring (the ``s`` keypress
repainting both columns) is covered in ``test_app.py``.
"""

from __future__ import annotations

from lot_textual_ui.models import Thing
from lot_textual_ui.sorting import (
    SORT_CYCLE,
    SortMode,
    next_sort_mode,
    sort_things,
)

# The stock status order the STATUS mode groups by.
STATUS_ORDER = ["note", "work", "info", "done"]


def test_cycle_is_status_then_recent_then_name_and_wraps() -> None:
    assert SORT_CYCLE[0] is SortMode.STATUS
    assert next_sort_mode(SortMode.STATUS) is SortMode.RECENT
    assert next_sort_mode(SortMode.RECENT) is SortMode.NAME
    # Wraps back to the start.
    assert next_sort_mode(SortMode.NAME) is SortMode.STATUS


def test_name_sort_is_case_insensitive_alphabetical() -> None:
    things = [
        Thing(id="1", name="banana", status="note"),
        Thing(id="2", name="Apple", status="note"),
        Thing(id="3", name="cherry", status="note"),
    ]
    order = [t.id for t in sort_things(things, SortMode.NAME, STATUS_ORDER)]
    assert order == ["2", "1", "3"]


def test_status_sort_groups_by_configured_order_keeping_input_order_within() -> None:
    # The app feeds siblings name-sorted, so STATUS relies on the stable sort to
    # keep a group in its incoming order rather than re-sorting by name here.
    things = [
        Thing(id="d", name="zeta", status="done"),
        Thing(id="n1", name="alpha", status="note"),
        Thing(id="w", name="gamma", status="work"),
        Thing(id="n2", name="beta", status="note"),
    ]
    order = [t.id for t in sort_things(things, SortMode.STATUS, STATUS_ORDER)]
    # note group (n1, n2 in input order) < work < done.
    assert order == ["n1", "n2", "w", "d"]


def test_status_sort_puts_unknown_status_last() -> None:
    things = [
        Thing(id="mystery", name="aaa", status="archived"),
        Thing(id="known", name="zzz", status="note"),
    ]
    order = [t.id for t in sort_things(things, SortMode.STATUS, STATUS_ORDER)]
    assert order == ["known", "mystery"]


def test_recent_sort_is_newest_first() -> None:
    things = [
        Thing(id="old", name="a", status="note", updated="2026-01-01T00:00:00+00:00"),
        Thing(id="new", name="b", status="note", updated="2026-06-01T00:00:00+00:00"),
        Thing(id="mid", name="c", status="note", updated="2026-03-01T00:00:00+00:00"),
    ]
    order = [t.id for t in sort_things(things, SortMode.RECENT, STATUS_ORDER)]
    assert order == ["new", "mid", "old"]


def test_recent_sort_folds_in_descendant_activity() -> None:
    # The parent's own update is old, but a grandchild is the newest thing in
    # the vault — so the parent's subtree floats above a sibling touched later
    # than the parent but earlier than the grandchild.
    grandchild = Thing(
        id="g", name="g", status="work", updated="2026-09-01T00:00:00+00:00"
    )
    child = Thing(
        id="c",
        name="c",
        status="work",
        updated="2026-02-01T00:00:00+00:00",
        children=[grandchild],
    )
    parent = Thing(
        id="parent",
        name="parent",
        status="note",
        updated="2026-01-01T00:00:00+00:00",
        children=[child],
    )
    sibling = Thing(
        id="sibling",
        name="sibling",
        status="note",
        updated="2026-05-01T00:00:00+00:00",
    )
    order = [
        t.id for t in sort_things([sibling, parent], SortMode.RECENT, STATUS_ORDER)
    ]
    assert order == ["parent", "sibling"]


def test_recent_sort_ranks_missing_timestamps_last_by_name() -> None:
    things = [
        Thing(id="dated", name="z", status="note", updated="2026-01-01T00:00:00+00:00"),
        Thing(id="undated_b", name="beta", status="note"),
        Thing(id="undated_a", name="alpha", status="note"),
    ]
    order = [t.id for t in sort_things(things, SortMode.RECENT, STATUS_ORDER)]
    # The dated Thing leads; the two undated ones follow in name order.
    assert order == ["dated", "undated_a", "undated_b"]


def test_sort_does_not_mutate_the_input_list() -> None:
    things = [
        Thing(id="b", name="b", status="note"),
        Thing(id="a", name="a", status="note"),
    ]
    original = list(things)
    sort_things(things, SortMode.NAME, STATUS_ORDER)
    assert things == original
