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
from lot_textual_ui.detail import DetailPane, UpdateItem, linkify_lot_ids
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


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

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

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


async def open_thing(app: LotTextualApp, pilot, thing_id: str) -> None:
    """Select a Thing and settle — the app opens on the whole-vault view,
    where no Thing is in view, so detail tests pick one explicitly."""
    await settle(app, pilot)
    app.selected_id = thing_id
    await settle(app, pilot)


def test_detail_renders_update_items() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")
            assert app.selected_id == "a"

            pane = app.query_one(DetailPane)

            # Two updates, oldest first, each its own markdown body. The pane
            # renders only the thread — no separate computed-state body, which
            # would just duplicate it (readme §3.1.4).
            assert len(pane.query("#detail-state")) == 0
            items = pane.query(UpdateItem)
            assert len(items) == 2
            assert markdown_sources(pane) == ["First.", "Second."]

            # Headers carry type, timestamp, and update id.
            first_header = str(items.first().query_one(".update-header").render())
            assert "note" in first_header
            assert "a1" in first_header

    asyncio.run(scenario())


def test_detail_renders_custom_typed_updates() -> None:
    # A custom update type (readme §5.2.5) renders like any other: its name in
    # the header and its (possibly absent) body below — nothing special-cases
    # the built-in type names.
    async def scenario() -> None:
        thing = Thing(id="a", name="Alpha", status="wont-do")
        listing = ThingList(path="/x", things=[thing])
        states = {
            "a": ComputedState(
                status="wont-do", task_id="a", update_id="a2", body="First."
            )
        }
        updates = {
            "a": [
                Update(update_id="a1", type="note", at="t1", body="First."),
                # A bodyless custom terminal update, as `wont-do` would write.
                Update(update_id="a2", type="wont-do", at="t2", body=None),
            ]
        }
        app = LotTextualApp(lot_cli=FakeLotCli(listing, states, updates))
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")

            pane = app.query_one(DetailPane)
            items = pane.query(UpdateItem)
            assert len(items) == 2
            last_header = str(items.last().query_one(".update-header").render())
            assert "wont-do" in last_header
            assert "a2" in last_header
            # The absent body renders as an empty markdown block, not a crash.
            assert markdown_sources(pane) == ["First.", ""]

    asyncio.run(scenario())


def test_detail_updates_on_selection_change() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")

            app.selected_id = "b"
            await settle(app, pilot)

            pane = app.query_one(DetailPane)
            assert markdown_sources(pane) == ["Only."]

    asyncio.run(scenario())


def test_detail_handles_no_updates() -> None:
    async def scenario() -> None:
        fake = sample()
        fake._updates["a"] = []
        app = LotTextualApp(lot_cli=fake)
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
            await open_thing(app, pilot, "a")

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
    exercise the navigation path without a real vault or mouse. The message is
    sourced from an update body's Markdown, the only place links now render.
    """
    pane = app.query_one(DetailPane)
    md = pane.query(UpdateItem).first().query_one(Markdown)
    pane.on_markdown_link_clicked(Markdown.LinkClicked(md, href))


def test_lot_link_click_navigates_to_known_thing() -> None:
    async def scenario() -> None:
        app = LotTextualApp(lot_cli=sample())
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")
            assert app.selected_id == "a"

            # A lot: link to a Thing already in the index navigates directly,
            # with no CLI round-trip.
            fake = app.lot_cli
            before = list(fake.get_calls)
            click_link(app, "lot:b")
            await settle(app, pilot)
            assert app.selected_id == "b"
            # Fast path: `thing get` was not called at all — not to resolve the
            # target (it's already indexed), nor to reload detail (the pane now
            # loads only the update thread).
            assert fake.get_calls == before

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
            await open_thing(app, pilot, "a")
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
            await open_thing(app, pilot, "a")
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
            await open_thing(app, pilot, "a")
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


# A canonical id: `lot:` + exactly 22 base62 digits (crates/lot-core/src/id.rs).
ID = "lot:0123456789ABCDEFGHIJab"
LINKED = f"[{ID}]({ID})"


def test_linkify_wraps_bare_ids_in_prose() -> None:
    assert linkify_lot_ids(f"See {ID} for context.") == f"See {LINKED} for context."
    # Multiple ids on one line, and ids inside plain parentheses, all linkify.
    assert linkify_lot_ids(f"({ID}) and {ID}") == f"({LINKED}) and {LINKED}"
    # An id at the very start/end of the text works (no surrounding chars).
    assert linkify_lot_ids(ID) == LINKED


def test_linkify_ignores_non_canonical_ids() -> None:
    # Wrong length (21 or 23 base62 digits) is not a canonical id.
    assert linkify_lot_ids(ID[:-1]) == ID[:-1]
    assert linkify_lot_ids(ID + "c") == ID + "c"
    # `lot:` glued to a preceding word (e.g. "pilot:") is not an id either.
    assert linkify_lot_ids(f"pi{ID}") == f"pi{ID}"
    # Non-base62 characters in the digits don't match.
    bad = "lot:0123456789ABCDEFGHIJ_b"
    assert linkify_lot_ids(bad) == bad


def test_linkify_leaves_existing_link_syntax_alone() -> None:
    # An authored markdown link: neither the text nor the target is re-wrapped.
    authored = f"[Holiday]({ID})"
    assert linkify_lot_ids(authored) == authored
    assert linkify_lot_ids(f"[{ID}](https://x.example)") == f"[{ID}](https://x.example)"
    # An autolink already renders as a link.
    assert linkify_lot_ids(f"<{ID}>") == f"<{ID}>"


def test_linkify_leaves_code_alone() -> None:
    # Inline code spans keep their literal content.
    assert linkify_lot_ids(f"run `lot thing get {ID}` now").count("[") == 0
    # Fenced code blocks too, while prose after the fence still linkifies.
    text = f"```\n{ID}\n```\n\n{ID}"
    assert linkify_lot_ids(text) == f"```\n{ID}\n```\n\n{LINKED}"


def test_detail_linkifies_bare_ids_in_bodies() -> None:
    # End to end through the pane: a body that merely *mentions* an id renders
    # with that id wrapped as a markdown link, so clicking it navigates via the
    # existing lot:-link handling.
    async def scenario() -> None:
        fake = sample()
        fake._updates["a"] = [
            Update(
                update_id="a1",
                type="note",
                at="2026-01-01T00:00:00Z",
                body=f"Blocked on {ID}.",
            ),
        ]
        app = LotTextualApp(lot_cli=fake)
        async with app.run_test() as pilot:
            await open_thing(app, pilot, "a")

            assert markdown_sources(app.query_one(DetailPane)) == [
                f"Blocked on {LINKED}."
            ]

    asyncio.run(scenario())
