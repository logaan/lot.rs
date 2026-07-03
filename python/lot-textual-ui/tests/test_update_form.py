"""Tests for the new-Update modal form.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records the
``update_*`` calls it receives and counts ``thing_list`` calls so a successful
submit can be observed triggering a vault reload (which repaints the trees and
re-renders the selected Thing's detail thread).
"""

from __future__ import annotations

import asyncio

from textual.widgets import Label, TextArea

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.forms import (
    _EMPTY_BODY_MESSAGE,
    UPDATE_BODY_TEXTAREA_ID,
    NewUpdateScreen,
)
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


class FakeLotCli:
    """A stand-in :class:`LotCli` recording Update calls and reload counts."""

    def __init__(self, *, fail: bool = False) -> None:
        self._roots: list[Thing] = [Thing(id="r1", name="Root", status="work")]
        self.work_calls: list[tuple[str, str]] = []
        self.info_calls: list[tuple[str, str]] = []
        self.done_calls: list[str] = []
        self.list_calls = 0
        self._fail = fail
        self._counter = 0

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        self.list_calls += 1
        return ThingList(path="/x", things=list(self._roots))

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        for event in ():
            yield event

    async def update_work(self, thing_id: str, body: str) -> str:
        self.work_calls.append((thing_id, body))
        return self._new_id()

    async def update_info(self, thing_id: str, body: str) -> str:
        self.info_calls.append((thing_id, body))
        return self._new_id()

    async def update_done(self, thing_id: str) -> str:
        self.done_calls.append(thing_id)
        return self._new_id()

    def _new_id(self) -> str:
        if self._fail:
            raise LotError(("update", "work"), 1, "boom")
        self._counter += 1
        return f"upd{self._counter}"


def make_app(*, fail: bool = False) -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(fail=fail)
    return LotTextualApp(lot_cli=cli), cli


def test_submit_work_calls_update_work_and_reloads() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            listed_before = cli.list_calls
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The work update targeted the selected Thing with the typed body.
            assert cli.work_calls == [("r1", "wip")]
            assert cli.info_calls == []
            assert cli.done_calls == []
            # The modal closed and the vault was reloaded (detail re-rendered).
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.list_calls > listed_before

    asyncio.run(scenario())


def test_submit_done_calls_update_done_with_no_body() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Opened directly on the bodyless `done` type; empty body is fine.
            app.open_new_update_form(kind="done")
            await pilot.pause()

            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.done_calls == ["r1"]
            assert cli.work_calls == []
            assert cli.info_calls == []
            assert not isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_empty_body_blocks_work_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # Body left blank: submit must not call the CLI.
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.work_calls == []
            assert isinstance(app.screen, NewUpdateScreen)
            error = app.screen.query_one("#new-update-error", Label)
            assert getattr(error, "_Static__content", "") == _EMPTY_BODY_MESSAGE

    asyncio.run(scenario())


def test_cancel_closes_without_calling_cli() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)

            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "drop"
            await pilot.press("escape")
            await pilot.pause()

            assert cli.work_calls == []
            assert cli.info_calls == []
            assert cli.done_calls == []
            assert not isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_cli_error_surfaces_and_keeps_form_open() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "boom"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The CLI was attempted but failed; the form stays open.
            assert cli.work_calls == [("r1", "boom")]
            assert isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_no_selection_does_not_open_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = None
            await pilot.pause()

            app.open_new_update_form(kind="work")
            await pilot.pause()

            # Nothing to target: no form pushed and no CLI call.
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.work_calls == []

    asyncio.run(scenario())


def test_palette_update_work_opens_the_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "work")))
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_palette_update_done_opens_the_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "done")))
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


class LeafUpdate:
    """Minimal stand-in for an ``update`` :class:`LeafCommand`.

    Only the attributes ``run_lot_command`` reads are needed: ``needs_input``
    routes it to the forms branch and ``path`` selects the update form/type.
    """

    needs_input = True

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        self.label = " ".join(path)


if __name__ == "__main__":  # pragma: no cover
    pass
