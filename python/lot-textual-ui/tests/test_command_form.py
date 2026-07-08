"""Tests for the generic read-only command form + result modal.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault or subprocess is required. The fake
records every ``run_command`` argv and counts ``thing_list`` calls, so a test
can assert both what the assembled command was *and* that a read-only command
never triggers a vault reload.

:class:`~lot_textual_ui.palette.LeafCommand` / :class:`~lot_textual_ui.palette.ArgSpec`
are built **inline** (rather than from the captured ``help.yaml`` fixture, which
lacks ``update path``) so each test states exactly the arg metadata it exercises.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input, Label, Select

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
    """A stand-in :class:`LotCli` recording runs and counting reloads."""

    def __init__(self) -> None:
        self._roots = [Thing(id="r1", name="Root", status="work")]
        self.ran: list[tuple[str, ...]] = []
        self.thing_list_calls = 0
        self.output = "OUTPUT"

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
        return self.output

    async def watch(self):
        for event in ():
            yield event


def make_app() -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli()
    return LotTextualApp(lot_cli=cli), cli


# Inline leaf commands mirroring `lot help --format=yaml` for the read-only set.


def thing_path_command() -> LeafCommand:
    return LeafCommand(
        path=("thing", "path"),
        about="Print the filesystem path of a Thing's folder",
        args=(
            ArgSpec(name="thing", help="Thing id", required=False, takes_value=True),
        ),
    )


def thing_get_command() -> LeafCommand:
    return LeafCommand(
        path=("thing", "get"),
        about="Print a Thing's computed current state",
        args=(
            ArgSpec(name="thing", help="Thing id", required=False, takes_value=True),
            ArgSpec(
                name="format",
                help="Output format",
                required=False,
                takes_value=True,
                long="format",
                default="yaml",
                possible_values=("yaml", "markdown"),
            ),
        ),
    )


def update_path_command() -> LeafCommand:
    return LeafCommand(
        path=("update", "path"),
        about="Print the filesystem path of an Update file",
        args=(
            ArgSpec(name="update", help="Update id", required=True, takes_value=True),
        ),
    )


def test_read_only_command_opens_the_generic_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(thing_path_command())
            await pilot.pause()
            assert isinstance(app.screen, CommandFormScreen)

    asyncio.run(scenario())


def test_submit_runs_assembled_argv_and_shows_result() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            reloads_before = cli.thing_list_calls

            app.run_lot_command(thing_path_command())
            await pilot.pause()

            app.screen.query_one("#command-form-field-0", Input).value = "lot:abc"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The assembled argv was run through the generic seam...
            assert ("thing", "path", "lot:abc") in cli.ran
            # ...its stdout is shown in the scrollable result modal...
            assert isinstance(app.screen, CommandResultScreen)
            # ...and a read-only command never reloads the vault.
            assert cli.thing_list_calls == reloads_before

    asyncio.run(scenario())


def test_thing_positional_is_prefilled_from_the_in_view_thing() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(thing_path_command())
            await pilot.pause()

            # The `thing` field is seeded with the Thing the user is looking at.
            assert app.current_thing_id == "r1"
            field = app.screen.query_one("#command-form-field-0", Input)
            assert field.value == "r1"

    asyncio.run(scenario())


def test_defaulted_option_is_not_rendered_as_a_field() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            app.run_lot_command(thing_get_command())
            await pilot.pause()

            # `--format` is defaulted, so only the `thing` id renders a field.
            inputs = app.screen.query(Input)
            assert len(inputs) == 1
            assert not app.screen.query(Select)

            inputs.first().value = "lot:xyz"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The command runs on the default format — no `--format` in the argv.
            assert ("thing", "get", "lot:xyz") in cli.ran

    asyncio.run(scenario())


def test_required_field_left_blank_blocks_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            app.run_lot_command(update_path_command())
            await pilot.pause()

            # Submit with the required `update` id left blank.
            await pilot.press("ctrl+s")
            await pilot.pause()

            # Nothing ran; the form stays open with an in-form error.
            assert cli.ran == []
            assert isinstance(app.screen, CommandFormScreen)
            error = app.screen.query_one("#command-form-error", Label)
            expected = _EMPTY_FIELD_MESSAGE.format(field="Update id")
            assert getattr(error, "_Static__content", "") == expected

    asyncio.run(scenario())


def test_cancel_closes_without_running() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            app.run_lot_command(update_path_command())
            await pilot.pause()
            assert isinstance(app.screen, CommandFormScreen)

            app.screen.query_one("#command-form-field-0", Input).value = "lot:u"
            await pilot.press("escape")
            await pilot.pause()

            assert cli.ran == []
            assert not isinstance(app.screen, CommandFormScreen)

    asyncio.run(scenario())


def test_empty_output_still_shows_the_result_modal() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        cli.output = ""
        async with app.run_test() as pilot:
            await pilot.pause()

            app.run_lot_command(thing_path_command())
            await pilot.pause()
            app.screen.query_one("#command-form-field-0", Input).value = "lot:abc"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # An empty (but successful) run still surfaces a result modal.
            assert isinstance(app.screen, CommandResultScreen)

    asyncio.run(scenario())
