"""Tests for mouse text-selection and the copy-selection action.

Real mouse-drag selection is driven by the terminal and hard to exercise
headless, so these tests cover the *seams*: that the detail pane's content
widgets allow selection (Textual's native ``ALLOW_SELECT``), that
``action_copy_selection`` copies the screen's selected text via
``copy_to_clipboard`` (with the screen selection mocked) and toasts, that it
declines gracefully when nothing is selected, and that the action is reachable
from the key table and the palette. Booted headless with ``App.run_test()``
against a *fake* :class:`LotCli` so no vault or subprocess is involved.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Markdown, Static

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
    """Canned :class:`LotCli` with one Thing that has computed state + updates."""

    def __init__(self) -> None:
        a = Thing(id="lot:aaa", name="Alpha", status="work")
        self._listing = ThingList(path="/vault", things=[a])
        self._states = {
            "lot:aaa": ComputedState(
                status="work",
                task_id="lot:aaa",
                update_id="lot:a1",
                body="# Alpha\n\nSelect me.",
            ),
        }
        self._updates = {
            "lot:aaa": [
                Update(update_id="lot:a1", type="note", at="t1", body="body text"),
            ],
        }

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return self._states[thing_id]

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return self._updates[thing_id]

    async def watch(self):
        for event in ():
            yield event


def make_app() -> tuple[LotTextualApp, list[str]]:
    """An app wired to the fake, with ``copy_to_clipboard`` recording to a list."""
    app = LotTextualApp(lot_cli=FakeLotCli())
    copied: list[str] = []
    app.copy_to_clipboard = copied.append  # type: ignore[method-assign]
    return app, copied


def test_detail_content_widgets_allow_selection() -> None:
    """The update-body widgets are selectable, not disabled.

    Textual's mouse text-selection only spans widgets whose ``allow_select`` is
    true; if the detail content opted out (or were disabled) a drag would
    select nothing. This guards that the panes keep the framework default.
    """

    async def scenario() -> None:
        app, _copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()

            items = list(app.query(UpdateItem))
            assert items
            for item in items:
                body = item.query_one(Markdown)
                assert body.allow_select is True
                assert not body.disabled

    asyncio.run(scenario())


def test_copy_selection_copies_screen_selection_and_toasts() -> None:
    async def scenario() -> None:
        app, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            notifications: list[dict] = []
            app.notify = (  # type: ignore[method-assign]
                lambda message, **kwargs: notifications.append(
                    {"message": message, **kwargs}
                )
            )
            # Stand in for a live mouse selection spanning the detail pane.
            app.screen.get_selected_text = lambda: "selected words"  # type: ignore[method-assign]

            app.action_copy_selection()
            assert copied == ["selected words"]
            # A confirmation toast fires (title "Selection").
            assert notifications
            assert notifications[-1].get("title") == "Selection"

    asyncio.run(scenario())


def test_copy_selection_key_binding_copies() -> None:
    async def scenario() -> None:
        app, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.screen.get_selected_text = lambda: "via key"  # type: ignore[method-assign]
            await pilot.press("c")
            assert copied == ["via key"]

    asyncio.run(scenario())


def test_copy_selection_noop_when_nothing_selected() -> None:
    async def scenario() -> None:
        app, copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            notifications: list[dict] = []
            app.notify = (  # type: ignore[method-assign]
                lambda message, **kwargs: notifications.append(
                    {"message": message, **kwargs}
                )
            )
            app.screen.get_selected_text = lambda: None  # type: ignore[method-assign]

            app.action_copy_selection()
            # Nothing copied; a warning toast explains why.
            assert copied == []
            assert notifications
            assert notifications[-1].get("severity") == "warning"

    asyncio.run(scenario())


def test_copy_selection_registered_in_palette() -> None:
    from lot_textual_ui.palette import INTERNAL_COMMANDS

    titles = {command.title for command in INTERNAL_COMMANDS}
    assert "Copy selection" in titles


def test_no_updates_notice_is_selectable() -> None:
    """Even the empty-thread and empty-state notices stay selectable Statics."""

    async def scenario() -> None:
        app, _copied = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            pane = app.query_one(DetailPane)
            for static in pane.query(Static):
                assert static.allow_select is True

    asyncio.run(scenario())


def test_copy_selection_key_is_bound() -> None:
    from lot_textual_ui.keys import ACTION_BINDINGS

    actions = {binding.action for binding in ACTION_BINDINGS}
    assert "copy_selection" in actions


if __name__ == "__main__":  # pragma: no cover
    test_copy_selection_registered_in_palette()
