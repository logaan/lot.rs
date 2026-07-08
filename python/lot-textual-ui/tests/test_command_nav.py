"""Tests for the command navigator (``space`` / ``ctrl+letter``).

The pure state machine (:class:`CommandNav`) is exercised directly against a
small synthetic command tree. The screen and the app's entry points (the
``space`` leader, the ``ctrl+letter`` shortcuts, the forms seam) are driven
with Textual's ``App.run_test()`` pilot against a fake :class:`LotCli`, so no
real vault or subprocess is needed.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Input

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.command_nav import (
    CHOOSER_GUARD,
    CLOSE,
    CommandNav,
    CommandNavScreen,
)
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.palette import LeafCommand

# --- a synthetic command tree ------------------------------------------------
#
# Roughly the real `lot` shape, but deliberately not a copy of it: a synthetic
# `undo` leaf sits alongside `update` so a `u` collision exercises the chooser.


def leaf(name: str, about: str = "") -> dict:
    return {"name": name, "about": about}


def tree() -> dict:
    return {
        "name": "lot",
        "subcommands": [
            {"name": "vault", "subcommands": [leaf("new")]},
            {
                "name": "thing",
                "about": "Manage Things",
                "subcommands": [leaf("new"), leaf("get"), leaf("list")],
            },
            {
                "name": "update",
                "subcommands": [leaf("work"), leaf("info"), leaf("done")],
            },
            leaf("undo"),
            leaf("interface"),
        ],
    }


# --- the pure state machine --------------------------------------------------


def test_unique_letter_navigates_down() -> None:
    nav = CommandNav(tree())
    assert nav.on_letter("v") is None  # a group: navigate, don't run
    assert nav.command_path() == ("vault",)
    assert nav.breadcrumb() == "lot vault"
    assert nav.chooser is None


def test_unique_letter_on_leaf_invokes_immediately() -> None:
    nav = CommandNav(tree())
    assert nav.on_letter("t") is None
    outcome = nav.on_letter("n")
    assert isinstance(outcome, LeafCommand)
    assert outcome.path == ("thing", "new")


def test_letter_matching_is_case_insensitive() -> None:
    nav = CommandNav(tree())
    assert nav.on_letter("V") is None
    assert nav.command_path() == ("vault",)


def test_unmatched_letter_is_ignored() -> None:
    nav = CommandNav(tree())
    assert nav.on_letter("z") is None
    assert nav.path == []
    assert nav.chooser is None


def test_hidden_blocking_commands_are_unreachable() -> None:
    # `watch`/`web` block forever and `interface` recursively launches this UI,
    # so the navigator prunes them: their letters match nothing and they never
    # show at the current level.
    full = tree()
    full["subcommands"] += [leaf("watch"), leaf("web")]
    nav = CommandNav(full)
    names = {str(child.get("name")) for child in nav.children()}
    assert names.isdisjoint({"watch", "web", "interface"})
    assert nav.on_letter("w") is None  # no watch/web to run
    assert nav.path == []
    assert nav.chooser is None
    assert nav.on_letter("i") is None  # no interface to run
    assert nav.path == []
    # Ordinary commands are untouched.
    assert nav.on_letter("v") is None
    assert nav.command_path() == ("vault",)


def test_ambiguous_letter_opens_chooser_then_selects() -> None:
    nav = CommandNav(tree())
    # `u` matches both `update` and `undo`.
    assert nav.on_letter("u", now=0.0) is None
    chooser = nav.chooser
    assert chooser is not None
    assert len(chooser.candidates) == 2
    assert chooser.selected == 0

    # Move to `undo` and confirm past the guard; `undo` is a leaf so the
    # confirming Enter both picks it and runs it.
    nav.move_chooser(1)
    outcome = nav.on_enter(now=CHOOSER_GUARD)
    assert isinstance(outcome, LeafCommand)
    assert outcome.path == ("undo",)
    assert nav.chooser is None


def test_chooser_enter_ignored_within_guard() -> None:
    nav = CommandNav(tree())
    nav.on_letter("u", now=0.0)
    assert nav.on_enter(now=CHOOSER_GUARD / 2) is None
    assert nav.chooser is not None, "the premature Enter changed nothing"
    # Past the guard, the default first candidate (`update`, a group) confirms.
    assert nav.on_enter(now=CHOOSER_GUARD) is None
    assert nav.command_path() == ("update",)


def test_chooser_letters_move_highlight_and_backspace_dismisses() -> None:
    nav = CommandNav(tree())
    nav.on_letter("u", now=0.0)
    nav.on_letter("j")
    assert nav.chooser is not None and nav.chooser.selected == 1
    nav.on_letter("k")
    assert nav.chooser.selected == 0
    assert nav.on_backspace() is None
    assert nav.chooser is None, "backspace dismisses the chooser"
    assert nav.path == [], "without navigating anywhere"


def test_backspace_undoes_and_escape_clears_then_closes() -> None:
    nav = CommandNav(tree())
    nav.on_letter("t")
    assert nav.on_backspace() is None
    assert nav.path == []
    assert nav.on_backspace() == CLOSE

    nav.on_letter("t")
    assert nav.on_escape() is None
    assert nav.path == [], "escape clears all the way to the top"
    assert nav.on_escape() == CLOSE


def test_enter_without_chooser_does_nothing() -> None:
    nav = CommandNav(tree())
    nav.on_letter("t")
    assert nav.on_enter() is None
    assert nav.command_path() == ("thing",)


def test_reset_returns_to_the_top_level() -> None:
    nav = CommandNav(tree())
    nav.on_letter("t")
    nav.reset()
    assert nav.path == []
    assert nav.breadcrumb() == "lot"


# --- the screen and the app entry points -------------------------------------


class FakeLotCli:
    """A stand-in for :class:`LotCli`: canned vault data, a canned help tree,
    and a record of every command run."""

    def __init__(self, help_tree: dict) -> None:
        self._tree = help_tree
        self.ran: list[tuple[str, ...]] = []

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return ThingList(path="/x", things=[Thing(id="r1", name="Root", status="note")])

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        for event in ():
            yield event

    async def help_yaml(self) -> dict:
        return self._tree

    async def run_command(self, *args: str) -> str:
        self.ran.append(args)
        return ""


def make_app() -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(tree())
    return LotTextualApp(lot_cli=cli), cli


def test_space_opens_navigator_and_letters_run_a_leaf() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Space wins over the focused tree's own space-to-toggle binding.
            await pilot.press("space")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandNavScreen)
            await pilot.press("t")  # thing (a group: navigate)
            assert screen._nav.breadcrumb() == "lot thing"
            await pilot.press("l")  # list (a leaf: runs and closes)
            await pilot.pause()
            assert not isinstance(app.screen, CommandNavScreen)
            await app.workers.wait_for_complete()
            assert ("thing", "list") in cli.ran

    asyncio.run(scenario())


def test_ctrl_letter_opens_navigator_inside_top_level_command() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandNavScreen)
            assert screen._nav.breadcrumb() == "lot thing"
            await pilot.press("n")  # thing new: a leaf, runs and closes
            await pilot.pause()
            assert not isinstance(app.screen, CommandNavScreen)
            await app.workers.wait_for_complete()
            assert ("thing", "new") in cli.ran

    asyncio.run(scenario())


def test_ctrl_letter_collision_opens_chooser() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+u")  # update / undo collide on `u`
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandNavScreen)
            chooser = screen._nav.chooser
            assert chooser is not None and len(chooser.candidates) == 2
            # Pick `undo` (rewinding the guard so the pilot's Enter counts).
            chooser.opened_at -= CHOOSER_GUARD
            await pilot.press("j", "enter")
            await pilot.pause()
            assert not isinstance(app.screen, CommandNavScreen)
            await app.workers.wait_for_complete()
            assert ("undo",) in cli.ran

    asyncio.run(scenario())


def test_ctrl_letter_matching_nothing_is_ignored() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            base = app.screen
            await pilot.press("ctrl+x")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.screen is base
            assert cli.ran == []

    asyncio.run(scenario())


def test_escape_clears_then_closes_the_navigator() -> None:
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("ctrl+t")
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandNavScreen)
            await pilot.press("escape")  # clear back to the top level...
            assert screen._nav.breadcrumb() == "lot"
            assert isinstance(app.screen, CommandNavScreen)
            await pilot.press("escape")  # ...then close
            await pilot.pause()
            assert not isinstance(app.screen, CommandNavScreen)

    asyncio.run(scenario())


def test_space_in_a_form_input_stays_a_space() -> None:
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("n")  # open the new-Thing form (a modal)
            await pilot.pause()
            name = app.screen.query_one("#new-thing-name", Input)
            assert name.has_focus
            await pilot.press("a", "space", "b")
            assert name.value == "a b"
            assert not isinstance(app.screen, CommandNavScreen)

    asyncio.run(scenario())
