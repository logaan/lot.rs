"""Tests for the type-specific new-Update flows.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records the
``update_add`` calls it receives and counts ``thing_list`` calls so a
successful submit can be observed triggering a vault reload (which repaints
the trees and re-renders the selected Thing's detail thread).

The update flow is type-specific (no general "pick a type" form):

* a body-taking type (``work``/``info``/custom) opens a form fixed to that
  type — body field only, no type selector;
* a bodyless type (``done``, custom ``takes-body = false``) runs immediately
  on the in-view Thing with no form at all.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Label, RadioSet, TextArea

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
    UpdateTypeInfo,
)


class FakeLotCli:
    """A stand-in :class:`LotCli` recording Update calls and reload counts."""

    def __init__(
        self,
        *,
        fail: bool = False,
        update_types: list[UpdateTypeInfo] | None = None,
    ) -> None:
        self._roots: list[Thing] = [Thing(id="r1", name="Root", status="work")]
        self.add_calls: list[tuple[str, str, str | None]] = []
        self.list_calls = 0
        self._fail = fail
        self._update_types = update_types or []
        self._counter = 0

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig(update_types=list(self._update_types))

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

    async def update_add(self, kind: str, thing_id: str, body: str | None) -> str:
        self.add_calls.append((kind, thing_id, body))
        if self._fail:
            raise LotError(("update", kind), 1, "boom")
        self._counter += 1
        return f"upd{self._counter}"


def make_app(
    *,
    fail: bool = False,
    update_types: list[UpdateTypeInfo] | None = None,
) -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(fail=fail, update_types=update_types)
    return LotTextualApp(lot_cli=cli), cli


def test_submit_work_calls_update_add_and_reloads() -> None:
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
            assert cli.add_calls == [("work", "r1", "wip")]
            # The modal closed and the vault was reloaded (detail re-rendered).
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.list_calls > listed_before

    asyncio.run(scenario())


def test_form_is_type_fixed_with_no_type_selector() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="info")
            await pilot.pause()

            # No type radio set — the form is fixed to the type it was opened
            # for, and says so in its title.
            assert not app.screen.query(RadioSet)
            title = app.screen.query_one("#new-update-title", Label)
            assert "info" in str(getattr(title, "_Static__content", ""))
            # The body editor holds focus so typing starts immediately.
            assert isinstance(app.focused, TextArea)

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

            assert cli.add_calls == []
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

            assert cli.add_calls == []
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
            assert cli.add_calls == [("work", "r1", "boom")]
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
            assert cli.add_calls == []

    asyncio.run(scenario())


def test_palette_update_work_opens_the_work_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "work")))
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)
            assert app.screen._selected_kind() == "work"

    asyncio.run(scenario())


def test_palette_update_done_runs_immediately_without_a_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            listed_before = cli.list_calls
            app.run_lot_command(LeafUpdate(("update", "done")))
            await pilot.pause()
            await pilot.pause()

            # No form: the bodyless update ran straight away on the in-view
            # Thing, and the vault reloaded so its status marker repaints.
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.add_calls == [("done", "r1", None)]
            assert cli.list_calls > listed_before

    asyncio.run(scenario())


def test_bodyless_update_with_no_selection_notifies() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = None
            await pilot.pause()

            app.run_lot_command(LeafUpdate(("update", "done")))
            await pilot.pause()

            assert cli.add_calls == []

    asyncio.run(scenario())


def test_custom_body_taking_type_opens_its_own_form() -> None:
    types = [
        UpdateTypeInfo(name="note", built_in=True),
        UpdateTypeInfo(name="work", built_in=True),
        UpdateTypeInfo(name="info", built_in=True),
        UpdateTypeInfo(name="done", takes_body=False, terminal=True, built_in=True),
        UpdateTypeInfo(name="blocked"),
    ]

    async def scenario() -> None:
        app, cli = make_app(update_types=types)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "blocked")))
            await pilot.pause()

            assert isinstance(app.screen, NewUpdateScreen)
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "why"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.add_calls == [("blocked", "r1", "why")]

    asyncio.run(scenario())


def test_custom_bodyless_type_runs_immediately() -> None:
    types = [
        UpdateTypeInfo(name="done", takes_body=False, terminal=True, built_in=True),
        UpdateTypeInfo(name="wont-do", takes_body=False, terminal=True),
    ]

    async def scenario() -> None:
        app, cli = make_app(update_types=types)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "wont-do")))
            await pilot.pause()
            await pilot.pause()

            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.add_calls == [("wont-do", "r1", None)]

    asyncio.run(scenario())


def test_update_path_leaf_is_not_treated_as_an_update_type() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # `lot update path` needs input but is not an update *type*; it
            # must fall through to the placeholder, not open a form or run.
            app.run_lot_command(LeafUpdate(("update", "path")))
            await pilot.pause()

            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.add_calls == []

    asyncio.run(scenario())


class LeafUpdate:
    """Minimal stand-in for an ``update`` :class:`LeafCommand`.

    Only the attributes ``run_lot_command`` reads are needed: ``needs_input``
    routes it to the forms branch and ``path`` selects the update type.
    """

    needs_input = True

    def __init__(self, path: tuple[str, ...]) -> None:
        self.path = path
        self.label = " ".join(path)


if __name__ == "__main__":  # pragma: no cover
    pass
