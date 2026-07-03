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
from lot_textual_ui.lot_cli import LotError
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
        try:
            return self._states[thing_id]
        except KeyError:
            # Mirror the real CLI: an unknown id exits non-zero.
            raise LotError(
                ("thing", "get", thing_id), 1, f"unknown id: {thing_id}"
            ) from None

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


def body_visible(item: UpdateItem) -> bool:
    """Whether an update item's markdown body is currently shown."""
    return bool(item.query_one(Markdown).display)


def test_update_items_start_expanded() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            items = list(app.query_one(DetailPane).query(UpdateItem))
            assert items
            # Default state matches the old always-expanded behaviour.
            for item in items:
                assert item.collapsed is False
                assert body_visible(item)
                # The chevron reflects the expanded state.
                assert "▼" in str(item.query_one(".update-header").render())

    asyncio.run(scenario())


def test_toggle_collapses_and_expands_body() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            item = app.query_one(DetailPane).query(UpdateItem).first()
            assert body_visible(item)

            item.toggle()
            await pilot.pause()
            assert item.collapsed is True
            assert not body_visible(item)
            assert "▶" in str(item.query_one(".update-header").render())

            item.toggle()
            await pilot.pause()
            assert item.collapsed is False
            assert body_visible(item)
            assert "▼" in str(item.query_one(".update-header").render())

    asyncio.run(scenario())


def test_clicking_header_toggles_collapse() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            item = app.query_one(DetailPane).query(UpdateItem).first()
            assert body_visible(item)

            # Clicking the header line folds the item.
            await pilot.click(item.query_one(".update-header"))
            await pilot.pause()
            assert item.collapsed is True
            assert not body_visible(item)

    asyncio.run(scenario())


def test_clicking_body_does_not_toggle_collapse() -> None:
    """Regression: a click (or drag to select) in the body must not fold it.

    The body Markdown is where mouse text-selection happens; if a body click
    toggled collapse it would hide the very text the user was selecting. Only
    the header toggles.
    """

    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            item = app.query_one(DetailPane).query(UpdateItem).first()
            assert body_visible(item)

            # Click the body markdown, not the header: no fold.
            await pilot.click(item.query_one(Markdown))
            await pilot.pause()
            assert item.collapsed is False
            assert body_visible(item)
            # The click still focused the item, so it stays the current update.
            assert app.focused is item

    asyncio.run(scenario())


def test_keyboard_toggle_acts_on_focused_update() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            pane = app.query_one(DetailPane)
            first, second = list(pane.query(UpdateItem))

            second.focus()
            await pilot.pause()
            assert app.focused is second

            # 'z' toggles the focused update, not any other.
            await pilot.press("z")
            await pilot.pause()
            assert second.collapsed is True
            assert first.collapsed is False

            await pilot.press("z")
            await pilot.pause()
            assert second.collapsed is False

    asyncio.run(scenario())


def test_current_update_id_survives_collapse() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            pane = app.query_one(DetailPane)
            first, second = list(pane.query(UpdateItem))

            # Focus tracking still resolves the focused item after our changes.
            second.focus()
            await pilot.pause()
            assert pane.current_update_id == second.update_id

            # Collapsing does not disturb update_id or focus tracking.
            await pilot.press("z")
            await pilot.pause()
            assert second.collapsed is True
            assert second.update_id == "a2"
            assert pane.current_update_id == second.update_id

    asyncio.run(scenario())


def test_collapse_all_and_expand_all() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)

            pane = app.query_one(DetailPane)
            items = list(pane.query(UpdateItem))

            app.action_collapse_all_updates()
            await pilot.pause()
            assert all(item.collapsed for item in items)
            assert all(not body_visible(item) for item in items)

            app.action_expand_all_updates()
            await pilot.pause()
            assert all(not item.collapsed for item in items)
            assert all(body_visible(item) for item in items)

    asyncio.run(scenario())


def click_link(app: LotTextualApp, href: str) -> None:
    """Simulate activating a rendered link by dispatching ``LinkClicked``.

    Calls the pane's handler directly with a ``Markdown.LinkClicked`` message —
    the same message Textual posts when a body link is clicked — so the tests
    exercise the navigation path without a real vault or mouse.
    """
    pane = app.query_one(DetailPane)
    md = app.query_one("#detail-state", Markdown)
    pane.on_markdown_link_clicked(Markdown.LinkClicked(md, href))


def test_lot_link_click_navigates_to_known_thing() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.selected_id == "a"

            # A lot: link to a Thing already in the index navigates directly,
            # with no CLI round-trip.
            fake = app.lot_cli
            before = list(fake.get_calls)
            click_link(app, "lot:b")
            await settle(app, pilot)
            assert app.selected_id == "b"
            # Fast path: `thing get` was not called to resolve the target.
            assert fake.get_calls == before + ["b"]  # only the detail reload

    asyncio.run(scenario())


def test_lot_link_click_resolves_update_id_via_cli() -> None:
    async def scenario() -> None:
        fake = sample()
        # An update id unknown to the in-memory index; `lot thing get` maps it to
        # its owning Thing "b" via the returned computed state's task-id.
        fake._states["upd-b"] = ComputedState(
            status="work", task_id="b", update_id="upd-b", body=""
        )
        app = LotTextualApp(lot_cli=fake)
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.selected_id == "a"

            click_link(app, "lot:upd-b")
            await settle(app, pilot)
            assert app.selected_id == "b"
            # The id was resolved through the CLI (not found in the index).
            assert "upd-b" in fake.get_calls

    asyncio.run(scenario())


def test_non_lot_link_click_does_not_navigate() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.selected_id == "a"

            # A plain web link is left to default handling: no navigation, no
            # crash, no CLI resolution.
            click_link(app, "https://example.com")
            await settle(app, pilot)
            assert app.selected_id == "a"

    asyncio.run(scenario())


def test_unknown_lot_link_click_notifies_without_crashing() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await settle(app, pilot)
            assert app.selected_id == "a"

            notifications: list[dict] = []
            app.notify = (  # type: ignore[method-assign]
                lambda message, **kwargs: notifications.append(
                    {"message": message, **kwargs}
                )
            )

            # An id that matches no Thing and no update surfaces an error toast
            # and leaves the selection untouched.
            click_link(app, "lot:doesnotexist")
            await settle(app, pilot)
            assert app.selected_id == "a"
            assert notifications
            assert notifications[-1].get("severity") == "error"

    asyncio.run(scenario())
