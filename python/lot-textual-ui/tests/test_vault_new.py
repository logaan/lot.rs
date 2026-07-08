"""Tests for the ``vault new`` command flow (create a new vault on disk).

``vault new`` reuses the generic :class:`~lot_textual_ui.forms.CommandFormScreen`
(one required ``path`` field) and, on submit, runs ``lot vault new <path>`` in a
worker and toasts — deliberately *without* switching or reloading the running
UI. The app is booted headless against a *fake* :class:`LotCli` that records
``run_command`` argv, counts reloads, and flags any vault switch, so a test can
assert the create ran and the current vault was left untouched.

The ``vault new`` :class:`~lot_textual_ui.palette.LeafCommand` /
:class:`~lot_textual_ui.palette.ArgSpec` are built **inline** (the captured
``help.yaml`` fixture is not relied on) so the required ``path`` arg is explicit.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Label

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.forms import (
    _EMPTY_FIELD_MESSAGE,
    CommandFormScreen,
    CommandResultScreen,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.palette import ArgSpec, LeafCommand


class FakeLotCli:
    """A stand-in :class:`LotCli` recording runs, reloads, and vault switches."""

    def __init__(self) -> None:
        self._roots = [Thing(id="r1", name="Root", status="work")]
        self.ran: list[tuple[str, ...]] = []
        self.thing_list_calls = 0
        self.set_vault_paths: list[str] = []

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        self.thing_list_calls += 1
        return ThingList(path="/x", things=list(self._roots))

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def run_command(self, *args: str) -> str:
        self.ran.append(args)
        return ""

    def set_vault_path(self, path: str) -> None:
        self.set_vault_paths.append(path)

    async def watch(self):
        for event in ():
            yield event


def make_app() -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli()
    return LotTextualApp(lot_cli=cli), cli


def vault_new_command() -> LeafCommand:
    return LeafCommand(
        path=("vault", "new"),
        about="Create a new LoT vault on disk",
        args=(
            ArgSpec(
                name="path",
                help="Where to create the vault",
                required=True,
                takes_value=True,
            ),
        ),
    )


def test_vault_new_opens_the_generic_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(vault_new_command())
            await pilot.pause()
            assert isinstance(app.screen, CommandFormScreen)
            # A single field: the required path (no prefill).
            field = app.screen.query_one("#command-form-field-0", Input)
            assert field.value == ""

    asyncio.run(scenario())


def test_submit_creates_the_vault_without_switching_or_reloading() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            reloads_before = cli.thing_list_calls

            app.run_lot_command(vault_new_command())
            await pilot.pause()
            app.screen.query_one("#command-form-field-0", Input).value = "/tmp/newvault"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The vault was created via the generic run_command seam...
            assert ("vault", "new", "/tmp/newvault") in cli.ran
            # ...the current vault was NOT switched or reloaded...
            assert cli.set_vault_paths == []
            assert cli.thing_list_calls == reloads_before
            # ...and there is no result modal — a create just toasts.
            assert not isinstance(app.screen, CommandResultScreen)
            assert not isinstance(app.screen, CommandFormScreen)

    asyncio.run(scenario())


def test_empty_path_blocks_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            app.run_lot_command(vault_new_command())
            await pilot.pause()
            # Submit with the required path left blank.
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.ran == []
            assert isinstance(app.screen, CommandFormScreen)
            error = app.screen.query_one("#command-form-error", Label)
            expected = _EMPTY_FIELD_MESSAGE.format(field="Where to create the vault")
            assert getattr(error, "_Static__content", "") == expected

    asyncio.run(scenario())
