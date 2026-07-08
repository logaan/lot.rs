"""Tests for consuming the ``lot watch`` stream into live UI state.

Three layers, none of which need a real vault or a real ``lot watch``:

* the pure document *framer* and stream *parser* (against a canned fixture);
* the :class:`WatchEvent` model; and
* the app patching events in — incremental index mutation and selection
  preservation — driven through Textual's test harness with a fake
  :class:`LotCli` whose ``watch`` async generator yields canned events.

Each event is minimal (see ``lot help watch``): a created/modified event patches
one node
(id + name + status + parent, plus state/updates), a deleted event drops an id
and its descendants, and a bare ``reload`` event reloads the whole baseline.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.lot_cli import (
    iter_watch_documents,
    parse_watch_event,
    parse_watch_stream,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
    WatchEvent,
)

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text()


# --- document framing ------------------------------------------------------


def test_framer_splits_stream_on_column_zero_markers() -> None:
    stream = fixture("watch_stream.yaml")
    docs = list(iter_watch_documents(stream.splitlines(keepends=True)))
    # Two events in the fixture; the indented `---` inside a body must not add a
    # spurious third document.
    assert len(docs) == 2
    assert "kind: created" in docs[0]
    assert "kind: deleted" in docs[1]
    # The body's horizontal rule stayed with the first (created) event.
    assert "horizontal rule inside the body" in docs[0]


def test_framer_yields_nothing_for_blank_or_marker_only_input() -> None:
    assert list(iter_watch_documents([])) == []
    assert list(iter_watch_documents(["---\n", "---\n", "\n"])) == []


def test_framer_emits_trailing_document_without_closing_marker() -> None:
    lines = ["---\n", "kind: modified\n", "id: lot:x\n", "name: X\n"]
    docs = list(iter_watch_documents(lines))
    assert len(docs) == 1
    assert "kind: modified" in docs[0]


# --- stream + event parsing ------------------------------------------------


def test_parse_watch_stream_yields_typed_events() -> None:
    events = list(parse_watch_stream(fixture("watch_stream.yaml")))
    assert len(events) == 2

    created, deleted = events
    assert isinstance(created, WatchEvent)

    # A created event carries the patch fields (id/name/status, no parent for a
    # top-level Thing) plus typed state and updates — but no whole-tree snapshot.
    assert created.kind == "created"
    assert created.id == "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
    assert created.name == "This is the name"
    assert created.status == "note"
    assert created.parent is None
    assert isinstance(created.state, ComputedState)
    assert created.state.status == "note"
    assert "note-at" in created.state.timestamps
    assert created.updates is not None
    assert [u.type for u in created.updates] == ["note"]
    assert isinstance(created.updates[0], Update)

    # A deleted event carries only kind + id: no name/status/parent/state/updates.
    assert deleted.kind == "deleted"
    assert deleted.id == "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
    assert deleted.name is None
    assert deleted.status is None
    assert deleted.parent is None
    assert deleted.state is None
    assert deleted.updates is None


def test_parse_single_watch_event_document() -> None:
    doc = list(iter_watch_documents(fixture("watch_stream.yaml").splitlines(True)))[0]
    event = parse_watch_event(doc)
    assert event.kind == "created"
    assert event.state is not None and "This is the name" in (event.state.body or "")


def test_watch_event_from_dict_deleted_carries_only_id() -> None:
    event = WatchEvent.from_dict({"kind": "deleted", "id": "lot:x"})
    assert event.kind == "deleted"
    assert event.id == "lot:x"
    assert event.name is None
    assert event.status is None
    assert event.parent is None
    assert event.state is None
    assert event.updates is None


def test_watch_event_from_dict_reload_is_bare() -> None:
    event = WatchEvent.from_dict({"kind": "reload"})
    assert event.kind == "reload"
    assert event.id is None
    assert event.state is None
    assert event.updates is None


def test_watch_event_from_dict_child_carries_parent() -> None:
    event = WatchEvent.from_dict(
        {
            "kind": "created",
            "id": "c1",
            "name": "Child",
            "status": "note",
            "parent": "r1",
        }
    )
    assert event.parent == "r1"


# --- application to the app -------------------------------------------------


class FakeWatchCli:
    """Fake :class:`LotCli` whose ``watch`` yields a canned list of events."""

    def __init__(
        self,
        listing: ThingList,
        events: list[WatchEvent] | None = None,
        states: dict[str, ComputedState] | None = None,
        updates: dict[str, list[Update]] | None = None,
    ) -> None:
        self._listing = listing
        self._events = events or []
        self._states = states or {}
        self._updates = updates or {}
        self.get_calls: list[str] = []
        self.updates_calls: list[str] = []
        self.list_calls = 0

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        self.list_calls += 1
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        self.get_calls.append(thing_id)
        return self._states.get(
            thing_id,
            ComputedState(status="note", task_id=thing_id, update_id="u", body="body"),
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        self.updates_calls.append(thing_id)
        return self._updates.get(thing_id, [])

    async def watch(self):
        for event in self._events:
            yield event


def baseline() -> ThingList:
    child = Thing(id="c1", name="Child", status="note")
    root = Thing(id="r1", name="Root", status="work", children=[child])
    other = Thing(id="r2", name="Other", status="note")
    return ThingList(path="/x", things=[root, other])


def upsert(
    thing_id: str,
    name: str,
    status: str = "note",
    parent: str | None = None,
    kind: str = "modified",
) -> WatchEvent:
    """A minimal created/modified event patching one node."""
    return WatchEvent(
        kind=kind,
        id=thing_id,
        name=name,
        status=status,
        parent=parent,
        state=ComputedState(
            status=status, task_id=thing_id, update_id="u", body="body"
        ),
        updates=[],
    )


def delete(thing_id: str) -> WatchEvent:
    return WatchEvent(kind="deleted", id=thing_id)


def test_watch_worker_applies_events_and_updates_index() -> None:
    async def scenario() -> None:
        # The stream renames r1 (still present) and, separately, drops r2.
        cli = FakeWatchCli(
            baseline(),
            events=[upsert("r1", "Renamed", "done"), delete("r2")],
        )
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The index reflects the patches, and r1's descendant survived.
            assert app.thing_by_id("r1").name == "Renamed"
            assert app.thing_by_id("c1") is not None
            assert app.thing_by_id("r2") is None
            # The initial selection (the vault root) survived the patches.
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_upsert_can_create_a_new_child_under_its_parent() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeWatchCli(baseline()))
        async with app.run_test() as pilot:
            await pilot.pause()

            # A brand-new child of r1 appears under it without a full reload.
            await app._apply_event(
                upsert("c2", "Second", "note", parent="r1", kind="created")
            )
            await pilot.pause()

            new = app.thing_by_id("c2")
            assert new is not None
            r1 = app.thing_by_id("r1")
            assert [c.id for c in r1.children] == ["c1", "c2"]

    asyncio.run(scenario())


def test_modified_event_reparents_a_moved_thing() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeWatchCli(baseline()))
        async with app.run_test() as pilot:
            await pilot.pause()

            # A move surfaces as one `modified` event carrying the new parent
            # (never a created/deleted pair): c1 moves from under r1 to under
            # r2 and must still show up — under its new parent.
            await app._apply_event(upsert("c1", "Child", "note", parent="r2"))
            await pilot.pause()

            assert app.thing_by_id("c1") is not None
            assert app.thing_by_id("r1").children == []
            assert [c.id for c in app.thing_by_id("r2").children] == ["c1"]

    asyncio.run(scenario())


def test_apply_event_preserves_still_present_nested_selection() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "c1"
            await pilot.pause()

            # A modification renaming the selected nested Thing keeps it selected.
            await app._apply_event(upsert("c1", "Child renamed", "work", parent="r1"))
            await pilot.pause()

            assert app.selected_id == "c1"
            assert app.thing_by_id("c1").name == "Child renamed"

    asyncio.run(scenario())


def test_apply_event_falls_back_when_selected_thing_deleted() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            # r1 (and its child c1) is gone; selection falls back to a root.
            await app._apply_event(delete("r1"))
            await pilot.pause()

            assert app.thing_by_id("c1") is None
            assert app.selected_id == "r2"

    asyncio.run(scenario())


def test_apply_event_falls_back_to_the_vault_root_when_vault_emptied() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeWatchCli(baseline()))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            await app._apply_event(delete("r1"))
            await app._apply_event(delete("r2"))
            await pilot.pause()
            # No Things are left to select; the vault root always exists.
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_reload_event_reloads_the_baseline() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            calls_before = cli.list_calls

            # A bare reload event re-runs thing_list() to resync from scratch.
            await app._apply_event(WatchEvent(kind="reload"))
            await pilot.pause()

            assert cli.list_calls == calls_before + 1
            # The selection is preserved across the resync.
            assert app.selected_id == "r1"

    asyncio.run(scenario())


def test_apply_event_reloads_detail_when_selected_thing_changes() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await app.workers.wait_for_complete()
            await pilot.pause()
            before = cli.updates_calls.count("r1")

            # An event whose id IS the current selection reloads the detail pane
            # even though the selection id is unchanged.
            await app._apply_event(upsert("r1", "Root v2", "work"))
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.updates_calls.count("r1") > before

    asyncio.run(scenario())


def test_unrelated_event_does_not_reload_detail() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await app.workers.wait_for_complete()
            await pilot.pause()
            before = cli.updates_calls.count("r1")

            # Modifying an unrelated Thing must not disturb the detail pane.
            await app._apply_event(upsert("r2", "Other v2", "note"))
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.updates_calls.count("r1") == before

    asyncio.run(scenario())


# --- active-item preservation across rebuilds --------------------------------
#
# Regression tests for the "active item switches after a command runs" bug.
# Both tree columns are rebuilt after every reload/watch event; restoring the
# centre cursor *immediately* after a rebuild reads the fresh nodes' unassigned
# line numbers (-1), clamps the cursor to the top row, and — because selection
# follows the cursor — reassigns ``active_id`` to the centre root. The fixes
# defer the cursor restore past the refresh and re-find the node by id.


def test_reload_vault_preserves_active_descendant() -> None:
    """A reload (as after send-to-claude or any palette command) must not move
    the active item off the centre descendant onto the centre root."""

    async def scenario() -> None:
        from textual.widgets import Tree

        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.active_id = "c1"
            await pilot.pause()

            await app._reload_vault()
            await pilot.pause()
            await pilot.pause()

            assert app.active_id == "c1"
            centre = app.query_one("#centre-tree", Tree)
            assert centre.cursor_node is not None
            assert centre.cursor_node.data == "c1"

    asyncio.run(scenario())


def test_unrelated_event_keeps_centre_cursor_on_active_item() -> None:
    """A watch event for an unrelated Thing (an external disk write) must not
    yank the centre cursor to the top row."""

    async def scenario() -> None:
        from textual.widgets import Tree

        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Whole-vault view (the launch selection); drill the centre column
            # into a descendant.
            assert app.selected_id == VAULT_ROOT
            app.active_id = "c1"
            await pilot.pause()

            await app._apply_event(upsert("r2", "Other v2", "note"))
            await pilot.pause()
            await pilot.pause()

            assert app.active_id == "c1"
            centre = app.query_one("#centre-tree", Tree)
            assert centre.cursor_node is not None
            assert centre.cursor_node.data == "c1"

    asyncio.run(scenario())
