"""Tests for consuming the ``lot watch`` stream into live UI state.

Three layers, none of which need a real vault or a real ``lot watch``:

* the pure document *framer* and stream *parser* (against a canned fixture);
* the :class:`WatchEvent` model; and
* the app folding events in — index refresh and selection preservation — driven
  through Textual's test harness with a fake :class:`LotCli` whose ``watch``
  async generator yields canned events.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.lot_cli import (
    iter_watch_documents,
    parse_watch_event,
    parse_watch_stream,
)
from lot_textual_ui.models import (
    ComputedState,
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
    lines = ["---\n", "kind: modified\n", "things:\n", "  things: []\n"]
    docs = list(iter_watch_documents(lines))
    assert len(docs) == 1
    assert "kind: modified" in docs[0]


# --- stream + event parsing ------------------------------------------------


def test_parse_watch_stream_yields_typed_events() -> None:
    events = list(parse_watch_stream(fixture("watch_stream.yaml")))
    assert len(events) == 2

    created, deleted = events
    assert isinstance(created, WatchEvent)

    # A created event carries id, typed state, typed updates, and a typed tree.
    assert created.kind == "created"
    assert created.id == "lot:6Ic9Cg6kx0Xk2hQhVz3aBd"
    assert isinstance(created.state, ComputedState)
    assert created.state.status == "note"
    assert "note-at" in created.state.timestamps
    assert created.updates is not None
    assert [u.type for u in created.updates] == ["note"]
    assert isinstance(created.updates[0], Update)
    assert isinstance(created.things, ThingList)
    assert created.things.path == "/Users/you/vault"
    assert [t.id for t in created.things.things] == ["lot:6Ic9Cg6kx0Xk2hQhVz3aBd"]

    # A deleted event omits id/state/updates but always carries the tree.
    assert deleted.kind == "deleted"
    assert deleted.id is None
    assert deleted.state is None
    assert deleted.updates is None
    assert deleted.things.things == []


def test_parse_single_watch_event_document() -> None:
    doc = list(iter_watch_documents(fixture("watch_stream.yaml").splitlines(True)))[0]
    event = parse_watch_event(doc)
    assert event.kind == "created"
    assert event.state is not None and "This is the name" in (event.state.body or "")


def test_watch_event_from_dict_deleted_omits_state_and_updates() -> None:
    event = WatchEvent.from_dict(
        {"kind": "deleted", "things": {"path": "/x", "things": []}}
    )
    assert event.kind == "deleted"
    assert event.id is None
    assert event.state is None
    assert event.updates is None
    assert isinstance(event.things, ThingList)


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

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        self.get_calls.append(thing_id)
        return self._states.get(
            thing_id,
            ComputedState(status="note", task_id=thing_id, update_id="u", body="body"),
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return self._updates.get(thing_id, [])

    async def watch(self):
        for event in self._events:
            yield event


def baseline() -> ThingList:
    child = Thing(id="c1", name="Child", status="note")
    root = Thing(id="r1", name="Root", status="work", children=[child])
    other = Thing(id="r2", name="Other", status="note")
    return ThingList(path="/x", things=[root, other])


def event_with(things: list[Thing], *, kind: str = "modified", id: str | None = None):
    return WatchEvent(kind=kind, id=id, things=ThingList(path="/x", things=things))


def test_watch_worker_applies_event_and_updates_index() -> None:
    async def scenario() -> None:
        # The stream renames r1 (still present) and drops r2.
        renamed = Thing(id="r1", name="Renamed", status="done")
        cli = FakeWatchCli(baseline(), events=[event_with([renamed], id="r1")])
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The index reflects the streamed snapshot, not the baseline.
            assert app.thing_by_id("r1").name == "Renamed"
            assert app.thing_by_id("r2") is None
            # Selection (r1) survived the swap.
            assert app.selected_id == "r1"

    asyncio.run(scenario())


def test_apply_event_preserves_still_present_nested_selection() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "c1"
            await pilot.pause()

            # A modified snapshot that keeps c1 but restructures around it.
            child = Thing(id="c1", name="Child renamed", status="work")
            root = Thing(id="r1", name="Root", status="work", children=[child])
            app._apply_event(event_with([root], id="c1"))
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
            assert app.selected_id == "r1"

            # r1 is gone; only r2 remains. Root selection falls back to a root.
            r2 = Thing(id="r2", name="Other", status="note")
            app._apply_event(event_with([r2], kind="deleted"))
            await pilot.pause()

            assert app.selected_id == "r2"

    asyncio.run(scenario())


def test_apply_event_clears_selection_when_vault_emptied() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=FakeWatchCli(baseline()))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._apply_event(event_with([], kind="deleted"))
            await pilot.pause()
            assert app.selected_id is None

    asyncio.run(scenario())


def test_apply_event_reloads_detail_when_selected_thing_changes() -> None:
    async def scenario() -> None:
        cli = FakeWatchCli(baseline())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.selected_id == "r1"
            before = cli.get_calls.count("r1")

            # An event whose id IS the current selection reloads the detail pane
            # even though the selection id is unchanged.
            renamed = Thing(id="r1", name="Root v2", status="work")
            other = Thing(id="r2", name="Other", status="note")
            app._apply_event(event_with([renamed, other], id="r1"))
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.get_calls.count("r1") > before

    asyncio.run(scenario())
