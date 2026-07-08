"""Tests for the in-view-Thing mutation commands: ``thing move`` / ``thing archive``.

Both run through their own bespoke modals (the batch-move
:class:`~lot_textual_ui.batch.ThingPickerScreen` and the batch-archive
:class:`~lot_textual_ui.batch.ConfirmScreen`), so these tests boot the app
headless with Textual's ``App.run_test()`` pilot against a *fake* :class:`LotCli`
that records ``thing_move`` / ``thing_archive`` calls and counts reloads.

:class:`~lot_textual_ui.palette.LeafCommand` / :class:`~lot_textual_ui.palette.ArgSpec`
are built **inline** (the captured ``help.yaml`` fixture lacks ``thing move`` /
``thing archive``) so each test states exactly the arg metadata it exercises.
"""

from __future__ import annotations

import asyncio

from textual.widgets import OptionList

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.batch import ConfirmScreen, ThingPickerScreen
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.palette import ArgSpec, LeafCommand


class FakeLotCli:
    """A stand-in :class:`LotCli` recording moves/archives and counting reloads.

    ``thing_archive`` also drops the Thing from the canned listing, so a
    post-archive reload sees it gone (as the real CLI would) — the case that must
    re-resolve the now-dangling selection without crashing.
    """

    def __init__(self) -> None:
        self._roots = [
            Thing(id="r1", name="Root", status="work"),
            Thing(id="r2", name="Other root", status="note"),
        ]
        self.move_calls: list[tuple[str, str | None, bool]] = []
        self.archive_calls: list[str] = []
        self.thing_list_calls = 0

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

    async def thing_move(
        self, thing_id: str, parent: str | None = None, root: bool = False
    ) -> str:
        self.move_calls.append((thing_id, parent, root))
        return thing_id

    async def thing_archive(self, thing_id: str) -> str:
        self.archive_calls.append(thing_id)
        self._roots = [t for t in self._roots if t.id != thing_id]
        return thing_id

    async def watch(self):
        for event in ():
            yield event


def make_app() -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli()
    return LotTextualApp(lot_cli=cli), cli


def move_command() -> LeafCommand:
    return LeafCommand(
        path=("thing", "move"),
        about="Move a Thing (and its subtree) to a new parent or the root",
        args=(
            ArgSpec(name="thing", help="Thing id", required=False, takes_value=True),
            ArgSpec(
                name="parent",
                help="Destination parent",
                required=False,
                takes_value=True,
                long="parent",
            ),
            ArgSpec(
                name="root",
                help="Move to the vault root",
                required=False,
                takes_value=False,
                long="root",
                possible_values=("true", "false"),
            ),
        ),
    )


def archive_command() -> LeafCommand:
    return LeafCommand(
        path=("thing", "archive"),
        about="Archive a Thing and all its descendants",
        args=(
            ArgSpec(name="thing", help="Thing id", required=False, takes_value=True),
        ),
    )


async def _settle(pilot) -> None:
    # The mutation workers await the CLI call and then a reload; pump generously.
    for _ in range(8):
        await pilot.pause()


# --- thing move ---------------------------------------------------------------


def test_thing_move_opens_the_destination_picker() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(move_command())
            await pilot.pause()

            assert isinstance(app.screen, ThingPickerScreen)
            # Top level + the tree minus the Thing itself (r1 is excluded): r2.
            option_list = app.screen.query_one(OptionList)
            assert option_list.option_count == 2

    asyncio.run(scenario())


def test_thing_move_to_top_level_uses_root() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            reloads_before = cli.thing_list_calls

            app.run_lot_command(move_command())
            await pilot.pause()
            # The first option is always "Top level (vault root)" -> --root.
            app.screen.query_one(OptionList).highlighted = 0
            await pilot.pause()
            await pilot.press("enter")
            await _settle(pilot)

            assert cli.move_calls == [("r1", None, True)]
            assert cli.thing_list_calls > reloads_before  # a mutation reloads

    asyncio.run(scenario())


def test_thing_move_to_a_thing_uses_parent() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(move_command())
            await pilot.pause()
            # Second option is the only other Thing, r2 -> --parent r2.
            app.screen.query_one(OptionList).highlighted = 1
            await pilot.pause()
            await pilot.press("enter")
            await _settle(pilot)

            assert cli.move_calls == [("r1", "r2", False)]

    asyncio.run(scenario())


def test_cancelling_the_move_picker_moves_nothing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(move_command())
            await pilot.pause()
            assert isinstance(app.screen, ThingPickerScreen)
            await pilot.press("escape")
            await _settle(pilot)

            assert cli.move_calls == []
            assert not isinstance(app.screen, ThingPickerScreen)

    asyncio.run(scenario())


def test_thing_move_without_selection_notifies_and_opens_no_picker() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = None
            await pilot.pause()
            before = len(app._notifications)

            app.run_lot_command(move_command())
            await pilot.pause()

            assert cli.move_calls == []
            assert not isinstance(app.screen, ThingPickerScreen)
            assert len(app._notifications) == before + 1

    asyncio.run(scenario())


# --- thing archive ------------------------------------------------------------


def test_thing_archive_opens_a_confirmation() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(archive_command())
            await pilot.pause()

            assert isinstance(app.screen, ConfirmScreen)
            assert cli.archive_calls == []  # nothing archived until confirmed

    asyncio.run(scenario())


def test_confirming_archive_calls_cli_and_reloads_without_crashing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # The in-view Thing is the one being archived, so the post-archive
            # reload must re-resolve a now-gone selection rather than crash.
            app.selected_id = "r1"
            await pilot.pause()
            reloads_before = cli.thing_list_calls

            app.run_lot_command(archive_command())
            await pilot.pause()
            app._archive_thing_confirmed("r1", "Root", True)
            await _settle(pilot)

            assert cli.archive_calls == ["r1"]
            assert cli.thing_list_calls > reloads_before
            # The app survived the reload of a vanished selection.
            assert app.is_running

    asyncio.run(scenario())


def test_cancelling_archive_confirmation_archives_nothing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            app.run_lot_command(archive_command())
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot)

            assert cli.archive_calls == []
            assert not isinstance(app.screen, ConfirmScreen)

    asyncio.run(scenario())


def test_thing_archive_without_selection_notifies_and_opens_no_dialog() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = None
            await pilot.pause()
            before = len(app._notifications)

            app.run_lot_command(archive_command())
            await pilot.pause()

            assert cli.archive_calls == []
            assert not isinstance(app.screen, ConfirmScreen)
            assert len(app._notifications) == before + 1

    asyncio.run(scenario())
