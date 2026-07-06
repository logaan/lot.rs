"""Tests for :class:`~lot_textual_ui.wrapping_tree.WrappingTree`.

The two tree columns use :class:`WrappingTree` so a Thing name wider than its
column wraps onto extra rows instead of being truncated (and hidden behind a
horizontal scroll). These drive the widget through the real app — booted
headless with a *fake* :class:`LotCli`, as in ``test_app.py`` — at a deliberately
narrow width so names are forced to wrap.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Tree

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.wrapping_tree import WrappingTree

LONG_CHILD = "First child with a really long name that must wrap onto several rows"
LONG_SIBLING = "Second child also long enough to wrap across the narrow column here"


class FakeLotCli:
    """Minimal fake vault adapter returning a canned two-child tree."""

    def __init__(self) -> None:
        child = Thing(id="c1", name=LONG_CHILD, status="work")
        sibling = Thing(id="c2", name=LONG_SIBLING, status="done")
        root = Thing(id="r1", name="Root", status="work", children=[child, sibling])
        self._listing = ThingList(path="/x", things=[root])

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(status="note", task_id=thing_id, update_id="u1", body="b")

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="b")]

    async def watch(self):
        for event in ():
            yield event


def _rows(tree: Tree) -> list[str]:
    """The rendered text of every visual row of ``tree`` (blanks trimmed off)."""
    rows = [tree.render_line(y).text for y in range(tree.virtual_size.height)]
    return [row.rstrip() for row in rows]


def test_columns_are_wrapping_trees() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            assert isinstance(app.query_one("#left-tree", Tree), WrappingTree)
            assert isinstance(app.query_one("#centre-tree", Tree), WrappingTree)

    asyncio.run(scenario())


def test_long_name_wraps_over_multiple_rows() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        # Narrow terminal → each ~1/3 column is too slim for the long names.
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", Tree)
            # Root + two children = 3 tree lines, but wrapping needs more rows.
            assert len(centre._tree_lines) == 3
            assert centre.virtual_size.height > 3
            # Every word of a wrapped name survives, spread across the rows.
            joined = " ".join(_rows(centre))
            for word in LONG_CHILD.split():
                assert word in joined

    asyncio.run(scenario())


def test_wrapping_avoids_horizontal_overflow() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(48, 24)) as pilot:
            await pilot.pause()
            for tree_id in ("#left-tree", "#centre-tree"):
                tree = app.query_one(tree_id, Tree)
                # Wrapping to the content width means the virtual (scrollable)
                # width never exceeds it, so there is no horizontal scroll.
                assert tree.virtual_size.width <= tree.size.width

    asyncio.run(scenario())


def test_label_region_spans_all_wrapped_rows() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(48, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", WrappingTree)
            # The label regions of the three lines must tile the visual rows
            # exactly: contiguous, non-overlapping, covering the full height.
            expected_y = 0
            total = 0
            for line_no in range(len(centre._tree_lines)):
                region = centre._get_label_region(line_no)
                assert region is not None
                assert region.y == expected_y
                assert region.height >= 1
                expected_y += region.height
                total += region.height
            assert total == centre.virtual_size.height

    asyncio.run(scenario())


def test_continuation_rows_are_indented_under_the_label() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", WrappingTree)
            rows = _rows(centre)
            # The first child (not the last) carries the ├── connector on its
            # first row; its wrapped continuations replace the connector with the
            # vertical guide that runs down to the next sibling — never a second
            # connector — so the tree structure reads down the wrapped rows.
            first_child = 1
            first_row = centre._line_first_row[first_child]
            count = centre._line_row_count[first_child]
            assert count > 1  # the long name did wrap
            assert "├" in rows[first_row]
            for offset in range(1, count):
                continuation = rows[first_row + offset]
                assert continuation.startswith("│")  # sibling follows below
                assert "├" not in continuation and "└" not in continuation

            # The last child's continuations have no sibling below, so their
            # guide column is blank rather than a vertical.
            last_child = 2
            last_first = centre._line_first_row[last_child]
            for offset in range(1, centre._line_row_count[last_child]):
                assert rows[last_first + offset].startswith("  ")

    asyncio.run(scenario())


def test_status_is_a_fixed_column_and_the_name_wraps_under_itself() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(90, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", WrappingTree)
            rows = _rows(centre)
            first_child = 1  # status "work", the long-named LONG_CHILD
            first_row = centre._line_first_row[first_child]
            count = centre._line_row_count[first_child]
            assert count > 1  # the long name did wrap
            # The status word sits in its own leading column on the first row
            # only — never repeated down the wrapped continuations.
            assert "work" in rows[first_row]
            for offset in range(1, count):
                assert "work" not in rows[first_row + offset]

            # The name starts at the same column on every row: the fixed columns
            # (guides + mark + status + gutter) on the first row are matched by
            # blank/guide indentation of the exact same width on the rest, so the
            # name reads as one column, not wrapped under the status.
            name_col = centre._name_column(centre._tree_lines[first_child])
            assert rows[first_row][name_col] == "F"  # "First child ..." begins
            for offset in range(1, count):
                continuation = rows[first_row + offset]
                # Nothing but guide/space precedes the name column...
                assert set(continuation[:name_col]) <= {"│", " "}
                # ...and the wrapped word begins exactly at the name column.
                assert continuation[name_col] != " "

    asyncio.run(scenario())


def test_navigation_stays_one_node_per_line() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        async with app.run_test(size=(48, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", WrappingTree)
            centre.focus()
            # Cursor/navigation index nodes, not visual rows: the two children
            # are lines 1 and 2 regardless of how many rows their names occupy.
            assert centre.get_node_at_line(1).data == "c1"
            assert centre.get_node_at_line(2).data == "c2"
            centre.cursor_line = 2
            await pilot.pause()
            assert centre.cursor_node is not None
            assert centre.cursor_node.data == "c2"

    asyncio.run(scenario())


def test_short_name_stays_on_one_row() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeLotCli())
        # A terminal wide enough that even the longest name fits its column.
        async with app.run_test(size=(300, 24)) as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", WrappingTree)
            # Every name fits on its own row: rows == tree lines (no wrapping).
            assert centre.virtual_size.height == len(centre._tree_lines)

    asyncio.run(scenario())
