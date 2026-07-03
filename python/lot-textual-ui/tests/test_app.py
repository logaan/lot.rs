"""Tests for the three-column app shell.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. Async scenarios are driven
via ``asyncio.run`` to match the rest of the suite (no pytest-asyncio needed).
"""

from __future__ import annotations

import asyncio

from textual.widgets import Tree

from lot_textual_ui import __version__
from lot_textual_ui.app import LotTextualApp, node_label
from lot_textual_ui.models import ComputedState, Thing, ThingList, Update


class FakeLotCli:
    """A stand-in for :class:`LotCli` that returns a canned tree.

    The mounted detail pane also calls ``thing_get``/``thing_updates`` on
    selection, so those are stubbed here with trivial canned data (detail-pane
    rendering is exercised in ``test_detail.py``).
    """

    def __init__(self, listing: ThingList) -> None:
        self._listing = listing

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]


def sample_listing() -> ThingList:
    grandchild = Thing(id="g1", name="Grandchild", status="note")
    child = Thing(id="c1", name="Child", status="work", children=[grandchild])
    sibling = Thing(id="c2", name="Sibling", status="done")
    root = Thing(id="r1", name="Root", status="work", children=[child, sibling])
    other = Thing(id="r2", name="Other root", status="note")
    return ThingList(path="/x", things=[root, other])


def make_app() -> LotTextualApp:
    return LotTextualApp(lot_cli=FakeLotCli(sample_listing()))


def node_datas(tree: Tree) -> list[str | None]:
    """Flatten the data payload of every node under a tree's root."""

    result: list[str | None] = []

    def walk(node) -> None:
        for child in node.children:
            result.append(child.data)
            walk(child)

    walk(tree.root)
    return result


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_app_constructs() -> None:
    app = LotTextualApp()
    assert app.TITLE == "LoT"


def test_node_label_includes_status_marker() -> None:
    label = node_label(Thing(id="x", name="Thing", status="done"))
    assert "Thing" in label
    assert label != "Thing"  # a marker is prefixed


def test_three_columns_exist_and_initial_selection() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # All three columns are present.
            app.query_one("#left-tree", Tree)
            app.query_one("#centre-tree", Tree)
            app.query_one("#detail")
            # Initial selection is the first top-level Thing.
            assert app.selected_id == "r1"
            # Centre tree is rooted at the selection and shows its descendants.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data == "r1"
            assert set(node_datas(centre)) == {"c1", "c2", "g1"}
            # Left tree shows the top-level siblings for a root selection.
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "r2"}

    asyncio.run(scenario())


def test_selection_propagates_to_all_columns() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Select a nested Thing directly through the selection model.
            app.selected_id = "c1"
            await pilot.pause()

            # Left column: ancestors (Root) + siblings of the selection.
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "c1", "c2"}

            # Centre column: the selection's descendants.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data == "c1"
            assert set(node_datas(centre)) == {"g1"}

    asyncio.run(scenario())


def test_selecting_a_tree_node_updates_selection() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            centre = app.query_one("#centre-tree", Tree)
            # Find the node for the child and select it as the widget would.
            target = next(node for node in centre.root.children if node.data == "c1")
            centre.select_node(target)
            centre.post_message(Tree.NodeSelected(target))
            await pilot.pause()
            assert app.selected_id == "c1"

    asyncio.run(scenario())
