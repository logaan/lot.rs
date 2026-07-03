"""Tests for the right-column detail pane.

The pane is exercised through the real app shell booted headless with
``App.run_test()`` against a *fake* :class:`LotCli` (canned computed state +
updates), so no vault or subprocess is involved. Selection is driven via the
app's ``selected_id`` reactive, and the worker that loads/renders detail is
awaited with ``app.workers.wait_for_complete()``.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Markdown, Static

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.detail import DetailPane, UpdateItem
from lot_textual_ui.models import ComputedState, Thing, ThingList, Update


class FakeLotCli:
    """Canned :class:`LotCli` keyed by Thing id for detail-pane rendering."""

    def __init__(
        self,
        listing: ThingList,
        states: dict[str, ComputedState],
        updates: dict[str, list[Update]],
    ) -> None:
        self._listing = listing
        self._states = states
        self._updates = updates
        self.get_calls: list[str] = []
        self.updates_calls: list[str] = []

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        self.get_calls.append(thing_id)
        return self._states[thing_id]

    async def thing_updates(self, thing_id: str) -> list[Update]:
        self.updates_calls.append(thing_id)
        return self._updates[thing_id]

    async def watch(self):
        # No live events in the detail-pane tests; the watch worker ends at once.
        for event in ():
            yield event


def sample() -> FakeLotCli:
    a = Thing(id="a", name="Alpha", status="note")
    b = Thing(id="b", name="Beta", status="work")
    listing = ThingList(path="/x", things=[a, b])
    states = {
        "a": ComputedState(
            status="note", task_id="a", update_id="a1", body="# Alpha\n\nHello."
        ),
        "b": ComputedState(
            status="work", task_id="b", update_id="b2", body="# Beta\n\nWorld."
        ),
    }
    updates = {
        "a": [
            Update(
                update_id="a1", type="note", at="2026-01-01T00:00:00Z", body="First."
            ),
            Update(
                update_id="a2", type="work", at="2026-01-02T00:00:00Z", body="Second."
            ),
        ],
        "b": [
            Update(
                update_id="b1", type="note", at="2026-02-01T00:00:00Z", body="Only."
            ),
        ],
    }
    return FakeLotCli(listing, states, updates)


def markdown_sources(pane: DetailPane) -> list[str]:
    """Every update item's markdown body, in mounted (oldest-first) order."""
    return [item.query_one(Markdown).source for item in pane.query(UpdateItem)]


async def settle(app: LotTextualApp, pilot) -> None:
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


def test_detail_renders_state_and_update_items() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)  # initial selection is "a"
            assert app.selected_id == "a"

            pane = app.query_one(DetailPane)
            state = app.query_one("#detail-state", Markdown)
            assert state.source == "# Alpha\n\nHello."

            # Two updates, oldest first, each its own markdown body.
            items = pane.query(UpdateItem)
            assert len(items) == 2
            assert markdown_sources(pane) == ["First.", "Second."]

            # Headers carry type, timestamp, and update id.
            first_header = str(items.first().query_one(".update-header").render())
            assert "note" in first_header
            assert "a1" in first_header

    asyncio.run(scenario())


def test_detail_updates_on_selection_change() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            app.selected_id = "b"
            await settle(app, pilot)

            state = app.query_one("#detail-state", Markdown)
            assert state.source == "# Beta\n\nWorld."
            pane = app.query_one(DetailPane)
            assert markdown_sources(pane) == ["Only."]

    asyncio.run(scenario())


def test_detail_handles_empty_body_and_no_updates() -> None:
    async def scenario() -> None:
        fake = sample()
        fake._states["a"] = ComputedState(
            status="note", task_id="a", update_id="a1", body=""
        )
        fake._updates["a"] = []
        app = LotTextualApp(lot_cli=fake)
        async with app.run_test() as pilot:
            await settle(app, pilot)

            state = app.query_one("#detail-state", Markdown)
            assert "no computed state" in state.source.lower()

            pane = app.query_one(DetailPane)
            assert list(pane.query(UpdateItem)) == []
            # A muted "no updates" notice stands in for the empty thread.
            box = app.query_one("#detail-updates")
            notices = [str(s.render()).lower() for s in box.query(Static)]
            assert any("no updates" in text for text in notices)

    asyncio.run(scenario())
