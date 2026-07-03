"""Tests for the copy-to-clipboard actions (Thing/Update URI and path).

Booted headless with ``App.run_test()`` against a *fake* :class:`LotCli` so no
vault or subprocess is involved. ``copy_to_clipboard`` is replaced on the app
instance with a recorder, so each test asserts the exact text handed to the
clipboard: a URI comes straight from the in-memory model, while a path comes
from the fake's ``thing_path`` / ``update_path`` (mirroring ``lot thing path`` /
``lot update path``). Update-scoped copies are checked against the focused
update and the latest-update fallback.
"""

from __future__ import annotations

import asyncio

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.detail import DetailPane, UpdateItem
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


class FakeLotCli:
    """Canned :class:`LotCli` covering the reads plus the two path lookups."""

    def __init__(self) -> None:
        a = Thing(id="lot:aaa", name="Alpha", status="work")
        b = Thing(id="lot:bbb", name="Beta", status="note")
        self._listing = ThingList(path="/vault", things=[a, b])
        self._states = {
            "lot:aaa": ComputedState(
                status="work", task_id="lot:aaa", update_id="lot:a2", body="# Alpha"
            ),
            "lot:bbb": ComputedState(
                status="note", task_id="lot:bbb", update_id="lot:b1", body="# Beta"
            ),
        }
        self._updates = {
            "lot:aaa": [
                Update(update_id="lot:a1", type="note", at="t1", body="first"),
                Update(update_id="lot:a2", type="work", at="t2", body="second"),
            ],
            "lot:bbb": [
                Update(update_id="lot:b1", type="note", at="t1", body="only"),
            ],
        }
        self.thing_path_calls: list[str] = []
        self.update_path_calls: list[str] = []

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return self._states[thing_id]

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return self._updates[thing_id]

    async def thing_path(self, thing_id: str) -> str:
        self.thing_path_calls.append(thing_id)
        return f"/vault/{thing_id}/dir"

    async def update_path(self, update_id: str) -> str:
        self.update_path_calls.append(update_id)
        return f"/vault/updates/{update_id}.md"

    async def watch(self):
        for event in ():
            yield event


def make_app() -> tuple[LotTextualApp, FakeLotCli, list[str]]:
    """An app wired to the fake, with ``copy_to_clipboard`` recording to a list."""
    fake = FakeLotCli()
    app = LotTextualApp(lot_cli=fake)
    copied: list[str] = []
    # Replace the OSC-52 clipboard call with a recorder so tests observe the
    # exact text without touching a real terminal clipboard.
    app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
    return app, fake, copied


def test_copy_thing_uri_copies_selected_id() -> None:
    async def scenario() -> None:
        app, _fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.selected_id == "lot:aaa"
            await pilot.press("y")
            assert copied == ["lot:aaa"]

    asyncio.run(scenario())


def test_copy_thing_path_uses_lot_cli_path() -> None:
    async def scenario() -> None:
        app, fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("Y")
            await app.workers.wait_for_complete()
            assert fake.thing_path_calls == ["lot:aaa"]
            assert copied == ["/vault/lot:aaa/dir"]

    asyncio.run(scenario())


def test_copy_update_uri_uses_focused_update() -> None:
    async def scenario() -> None:
        app, _fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            items = list(app.query(UpdateItem))
            assert [item.update_id for item in items] == ["lot:a1", "lot:a2"]
            # Focus the *first* (older) update explicitly.
            items[0].focus()
            await pilot.pause()
            assert app.query_one(DetailPane).current_update_id == "lot:a1"
            app.action_copy_update_uri()
            assert copied == ["lot:a1"]

    asyncio.run(scenario())


def test_copy_update_uri_falls_back_to_latest() -> None:
    async def scenario() -> None:
        app, _fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            # No UpdateItem focused: fall back to the Thing's latest update.
            assert app.query_one(DetailPane).current_update_id == "lot:a2"
            app.action_copy_update_uri()
            assert copied == ["lot:a2"]

    asyncio.run(scenario())


def test_copy_update_path_uses_lot_cli_path_for_current_update() -> None:
    async def scenario() -> None:
        app, fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            app.action_copy_update_path()
            await app.workers.wait_for_complete()
            assert fake.update_path_calls == ["lot:a2"]
            assert copied == ["/vault/updates/lot:a2.md"]

    asyncio.run(scenario())


def test_copy_update_uri_notifies_when_no_updates() -> None:
    async def scenario() -> None:
        app, _fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Beta has updates too; simulate an empty thread by clearing them.
            app.query_one(DetailPane)._update_ids = []
            app.action_copy_update_uri()
            assert copied == []

    asyncio.run(scenario())


def test_copy_thing_uri_notifies_when_nothing_selected() -> None:
    async def scenario() -> None:
        app, _fake, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = None
            await pilot.pause()
            app.action_copy_thing_uri()
            assert copied == []

    asyncio.run(scenario())


def test_copy_actions_are_registered_in_palette() -> None:
    from lot_textual_ui.palette import INTERNAL_COMMANDS

    titles = {command.title for command in INTERNAL_COMMANDS}
    assert {
        "Copy Thing URI",
        "Copy Thing path",
        "Copy Update URI",
        "Copy Update path",
    } <= titles
