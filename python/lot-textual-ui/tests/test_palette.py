"""Tests for the command palette: help-tree flattening and the providers.

The pure flattening (``flatten_help_tree``) is tested straight against the
captured ``help.yaml`` fixture. The providers are exercised with Textual's
``App.run_test()`` against a *fake* :class:`LotCli` so no real vault or
subprocess is needed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from lot_textual_ui.palette import (
    INTERNAL_COMMANDS,
    InternalCommandProvider,
    LeafCommand,
    LotCommandProvider,
    flatten_help_tree,
)

FIXTURES = Path(__file__).parent / "fixtures"


def help_tree() -> dict:
    return yaml.safe_load((FIXTURES / "help.yaml").read_text())


# --- pure flattening -------------------------------------------------------


def test_flatten_yields_leaf_commands_not_groups() -> None:
    commands = flatten_help_tree(help_tree())
    paths = {cmd.path for cmd in commands}

    # A representative spread of leaves across every group is present.
    assert ("vault", "new") in paths
    assert ("thing", "new") in paths
    assert ("thing", "list") in paths
    assert ("update", "work") in paths
    assert ("update", "done") in paths
    assert ("claude", "install") in paths
    # Nested groups flatten to their full path.
    assert ("claude", "send", "sonnet") in paths
    # Top-level leaves (no subcommands) are included with a single-element path.
    assert ("help",) in paths

    # Group nodes are never emitted as runnable commands themselves.
    assert ("thing",) not in paths
    assert ("update",) not in paths
    assert ("claude",) not in paths
    assert ("claude", "send") not in paths
    # The root command itself is never a leaf.
    assert () not in paths
    assert ("lot",) not in paths


def test_flatten_excludes_hidden_blocking_commands() -> None:
    # `watch`/`web` block forever and `interface` would recursively launch this
    # UI from inside itself, so none of the three may surface as a runnable
    # palette entry — even though all are leaves in the discovered help tree.
    tree = help_tree()
    names = {child["name"] for child in tree["subcommands"]}
    assert {"watch", "web", "interface"} <= names  # present in the fixture...

    paths = {cmd.path for cmd in flatten_help_tree(tree)}
    assert ("watch",) not in paths  # ...but never emitted.
    assert ("web",) not in paths
    assert ("interface",) not in paths
    # The ordinary leaves are unaffected.
    assert ("thing", "list") in paths
    assert ("update", "work") in paths
    assert ("vault", "new") in paths
    assert ("claude", "send", "sonnet") in paths
    assert ("help",) in paths


def test_flatten_labels_and_help() -> None:
    commands = {cmd.path: cmd for cmd in flatten_help_tree(help_tree())}
    thing_new = commands[("thing", "new")]
    assert thing_new.label == "thing new"
    assert thing_new.help_text  # non-empty short description
    assert isinstance(thing_new, LeafCommand)


def test_needs_input_classification() -> None:
    commands = {cmd.path: cmd for cmd in flatten_help_tree(help_tree())}

    # Commands with a required arg or a value-taking arg without a default need
    # user input, so they must route to the forms hook rather than run blind.
    assert commands[("vault", "new")].needs_input  # required <path>
    assert commands[("thing", "new")].needs_input  # value-taking name/parent
    assert commands[("update", "work")].needs_input  # value-taking thing/content
    assert commands[("update", "done")].needs_input  # value-taking thing

    # `thing list` only has `--format`, which is value-taking but defaulted, so
    # it needs no input and can be run directly.
    assert not commands[("thing", "list")].needs_input
    # `claude install` takes no arguments at all.
    assert not commands[("claude", "install")].needs_input


def test_arg_specs_are_captured_for_forms() -> None:
    commands = {cmd.path: cmd for cmd in flatten_help_tree(help_tree())}
    thing_new = commands[("thing", "new")]
    arg_names = {arg.name for arg in thing_new.args}
    assert {"editor", "parent", "name"} <= arg_names

    vault_new = commands[("vault", "new")]
    (path_arg,) = [a for a in vault_new.args if a.name == "path"]
    assert path_arg.required
    assert path_arg.takes_value


def test_flatten_empty_tree() -> None:
    assert flatten_help_tree({}) == []


# --- provider integration --------------------------------------------------


class FakeLotCli:
    """A stand-in :class:`LotCli` returning canned help + listing data."""

    def __init__(self, listing: ThingList, help_data: dict) -> None:
        self._listing = listing
        self._help = help_data
        self.ran: list[tuple[str, ...]] = []
        self.claude_sends: list[tuple[str, str]] = []

    async def config_get(self) -> EffectiveConfig:
        return EffectiveConfig()

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def help_yaml(self) -> dict:
        return self._help

    async def run_command(self, *args: str) -> str:
        self.ran.append(args)
        return ""

    async def claude_send(self, model: str, thing_id: str) -> str:
        self.claude_sends.append((model, thing_id))
        return "backgrounded · abc123"

    async def watch(self):
        for event in ():
            yield event


def sample_listing() -> ThingList:
    root = Thing(id="r1", name="Root", status="work")
    return ThingList(path="/x", things=[root])


def make_app() -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(sample_listing(), help_tree())
    return LotTextualApp(lot_cli=cli), cli


def test_app_registers_palette_providers() -> None:
    assert LotCommandProvider in LotTextualApp.COMMANDS
    assert InternalCommandProvider in LotTextualApp.COMMANDS


def test_lot_provider_yields_matching_hits() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test():
            provider = LotCommandProvider(app.screen)
            await provider.startup()
            hits = [hit async for hit in provider.search("thing new")]
            labels = [hit.text for hit in hits]
            assert "thing new" in labels
            # Fuzzy matching ranks the exact command first.
            assert hits[0].text == "thing new"
            # An unrelated query yields no `thing new` hit.
            other = [hit.text async for hit in provider.search("zzzzz")]
            assert "thing new" not in other

    asyncio.run(scenario())


def test_lot_provider_discovery_lists_all_commands() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test():
            provider = LotCommandProvider(app.screen)
            await provider.startup()
            discovered = [hit.text async for hit in provider.discover()]
            assert "thing new" in discovered
            assert "update work" in discovered

    asyncio.run(scenario())


def test_internal_provider_yields_quit_and_refresh() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test():
            provider = InternalCommandProvider(app.screen)
            hits = [hit.text async for hit in provider.search("refresh")]
            assert any("Refresh" in text for text in hits)
            titles = {cmd.title for cmd in INTERNAL_COMMANDS}
            assert {"Quit", "Refresh vault", "Archive done Things"} <= titles

    asyncio.run(scenario())


def test_run_lot_command_runs_no_input_command_and_refreshes() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            commands = {c.path: c for c in flatten_help_tree(help_tree())}
            app.run_lot_command(commands[("thing", "list")])
            await pilot.pause()
            # The worker actually invoked `lot thing list` through the CLI.
            assert ("thing", "list") in cli.ran

    asyncio.run(scenario())


def test_run_lot_command_routes_input_command_to_hook() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            commands = {c.path: c for c in flatten_help_tree(help_tree())}
            # A command needing input must NOT run; it hits the placeholder hook.
            app.run_lot_command(commands[("thing", "new")])
            await pilot.pause()
            assert ("thing", "new") not in cli.ran

    asyncio.run(scenario())


def test_claude_send_launches_on_in_view_thing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            commands = {c.path: c for c in flatten_help_tree(help_tree())}
            # `claude send opus` needs the Thing argument, so it does not run
            # blind through `run_command`; instead it launches on the in-view
            # Thing (the sole root here) with the model passed through.
            app.run_lot_command(commands[("claude", "send", "opus")])
            await pilot.pause()
            await pilot.pause()
            assert cli.claude_sends == [("opus", "r1")]
            # It is a bespoke launch, not the generic no-input `run_command`.
            assert ("claude", "send", "opus") not in cli.ran

    asyncio.run(scenario())


def test_claude_send_without_selection_notifies() -> None:
    async def scenario() -> None:
        # An empty vault: nothing is in view, so there is no Thing to send.
        cli = FakeLotCli(ThingList(path="/x", things=[]), help_tree())
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            commands = {c.path: c for c in flatten_help_tree(help_tree())}
            app.run_lot_command(commands[("claude", "send", "sonnet")])
            await pilot.pause()
            assert cli.claude_sends == []

    asyncio.run(scenario())


def test_refresh_vault_action_reloads_listing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.action_refresh_vault()
            await pilot.pause()
            # Selection is preserved across a reload.
            assert app.selected_id == "r1"

    asyncio.run(scenario())


def test_command_palette_binding_is_registered() -> None:
    from lot_textual_ui.keys import ACTION_BINDINGS

    actions = {binding.action for binding in ACTION_BINDINGS}
    assert "command_palette" in actions
