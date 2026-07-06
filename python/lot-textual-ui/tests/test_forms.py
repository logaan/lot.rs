"""Tests for the new-Thing modal form.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records
``thing_new`` calls and grows its listing so a successful create can be observed
jumping the selection to the new Thing.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Label, TextArea

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.forms import (
    _EMPTY_NAME_MESSAGE,
    BODY_TEXTAREA_ID,
    NewThingScreen,
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
    """A stand-in :class:`LotCli` that records creates and grows its listing."""

    def __init__(self, *, fail: bool = False) -> None:
        self._roots: list[Thing] = [Thing(id="r1", name="Root", status="work")]
        self.new_calls: list[tuple[str, str, str | None]] = []
        self._fail = fail
        self._counter = 0

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
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

    async def thing_new(self, name: str, body: str, parent: str | None = None) -> str:
        self.new_calls.append((name, body, parent))
        if self._fail:
            raise LotError(("thing", "new"), 1, "boom")
        self._counter += 1
        new_id = f"new{self._counter}"
        self._roots.append(Thing(id=new_id, name=name, status="note"))
        return new_id


def make_app(*, fail: bool = False) -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(fail=fail)
    return LotTextualApp(lot_cli=cli), cli


def test_submit_creates_thing_and_jumps_selection() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "My Thing"
            app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "Some body"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.new_calls == [("My Thing", "Some body", None)]
            # Selection jumped to the freshly created Thing.
            assert app.selected_id == "new1"
            # The modal closed after a successful create.
            assert not isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_submit_passes_parent_when_form_seeded_with_one() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form(parent_id="r1", title="New child Thing")
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Child"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.new_calls == [("Child", "", "r1")]

    asyncio.run(scenario())


def test_empty_name_blocks_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # Name left blank; body set. Submit must not call the CLI.
            app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "orphan body"
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.new_calls == []
            # The form stays open with an error message shown.
            assert isinstance(app.screen, NewThingScreen)
            error = app.screen.query_one("#new-thing-error", Label)
            # The friendly validation message is shown in-form.
            assert getattr(error, "_Static__content", "") == _EMPTY_NAME_MESSAGE

    asyncio.run(scenario())


def test_whitespace_name_is_rejected() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "   "
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.new_calls == []
            assert isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_cancel_closes_without_calling_cli() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()
            assert isinstance(app.screen, NewThingScreen)

            app.screen.query_one("#new-thing-name", Input).value = "Discarded"
            await pilot.press("escape")
            await pilot.pause()

            assert cli.new_calls == []
            assert not isinstance(app.screen, NewThingScreen)
            # Selection unchanged (still the launch-time vault root).
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_cli_error_surfaces_and_keeps_form_open() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Boom"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The CLI was attempted but failed; the form stays open so input
            # is not lost, and the selection did not move.
            assert cli.new_calls == [("Boom", "", None)]
            assert isinstance(app.screen, NewThingScreen)
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_palette_thing_new_opens_the_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            command = LeafThingNew()
            app.run_lot_command(command)
            await pilot.pause()
            assert isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_new_child_action_opens_form_seeded_with_selection() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.action_new_child_thing()
            await pilot.pause()

            # The form opened, pre-seeded with the current selection as parent.
            assert isinstance(app.screen, NewThingScreen)
            assert app.screen._parent_id == app.selected_id == "r1"

    asyncio.run(scenario())


def test_new_child_action_no_ops_without_selection() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # No selection: there is no parent to hang a child under.
            app.selected_id = None
            await pilot.pause()
            before = len(app._notifications)

            app.action_new_child_thing()
            await pilot.pause()

            # No form is pushed; the user is notified instead.
            assert not isinstance(app.screen, NewThingScreen)
            assert len(app._notifications) == before + 1

    asyncio.run(scenario())


def test_new_child_is_registered_in_palette_and_keys() -> None:
    from lot_textual_ui.keys import ACTION_BINDINGS
    from lot_textual_ui.palette import INTERNAL_COMMANDS

    # Discoverable in the command palette.
    titles = {cmd.title for cmd in INTERNAL_COMMANDS}
    assert "New child Thing" in titles

    # Bound to a key that drives the app action.
    actions = {binding.action for binding in ACTION_BINDINGS}
    assert "new_child_thing" in actions
    assert "new_thing" in actions
    child_binding = next(b for b in ACTION_BINDINGS if b.action == "new_child_thing")
    assert child_binding.key == "a"
    assert child_binding.description  # shows in the footer/help


class LeafThingNew:
    """Minimal stand-in for the ``thing new`` :class:`LeafCommand`.

    Only the attributes ``run_lot_command`` reads are needed: ``needs_input``
    routes it to the forms branch and ``path`` selects the new-Thing form.
    """

    needs_input = True
    path = ("thing", "new")
    label = "thing new"


if __name__ == "__main__":  # pragma: no cover
    pass
