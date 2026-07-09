"""Crash-hardening tests for runtime vault switching.

The switch reloads the new vault's tree (``thing_list``) and *then* re-reads its
``[tui]`` config (``_apply_config`` -> ``config_get``). The tree load is already
guarded, but a malformed new-vault config raises a parse error (``TypeError`` /
``yaml.YAMLError``) — or a missing binary raises ``FileNotFoundError`` — none of
which are ``LotError``. Those must not crash the switch worker and leave the app
half-switched; the vault (whose tree loaded fine) stays switched and the bad
config is surfaced as a warning.

Driven through Textual's ``run_test()`` harness against a fake :class:`LotCli`,
via ``asyncio.run`` like the rest of the suite.
"""

from __future__ import annotations

import asyncio

import yaml
from textual.widgets import Tree

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
    VaultEntry,
)


class ConfigErrorSwitchCli:
    """A fake :class:`LotCli` whose new vault loads but whose config is broken.

    ``thing_list`` answers for whichever vault ``set_vault_path`` last targeted
    (so the switch's tree reload succeeds), while ``config_get`` raises for any
    path in ``bad_config_paths`` — standing in for a new vault whose ``lot
    settings get`` output is unparseable or mis-shaped.
    """

    def __init__(self, error: Exception, bad_config_paths: set[str]) -> None:
        self._error = error
        self._bad_config_paths = bad_config_paths
        self.vault_path = ""  # the currently targeted vault ("" is the initial)
        self.set_calls: list[str] = []
        self.watch_starts = 0
        self._vaults = [
            VaultEntry(path="/vault-a", name="A"),
            VaultEntry(path="/vault-b", name="B"),
        ]
        self._listings = {
            "": ThingList(
                path="/vault-a", things=[Thing(id="a1", name="A root", status="note")]
            ),
            "/vault-b": ThingList(
                path="/vault-b", things=[Thing(id="b1", name="B root", status="note")]
            ),
        }

    def set_vault_path(self, path: str) -> None:
        self.vault_path = path
        self.set_calls.append(path)

    async def config_get(self) -> EffectiveConfig:
        if self.vault_path in self._bad_config_paths:
            raise self._error
        return EffectiveConfig(
            vaults=self._vaults, vault_path=self.vault_path or "/vault-a"
        )

    async def thing_list(self) -> ThingList:
        return self._listings.get(self.vault_path) or self._listings[""]

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        self.watch_starts += 1
        for event in ():
            yield event


def node_datas(tree: Tree) -> set[str | None]:
    """The data payload of every node under a tree's root."""
    result: set[str | None] = set()

    def walk(node) -> None:
        for child in node.children:
            result.add(child.data)
            walk(child)

    walk(tree.root)
    return result


async def _settle(pilot) -> None:
    # The switch runs as a worker with several awaits; pump the loop a few times.
    for _ in range(6):
        await pilot.pause()


def _switch_survives_bad_config(error: Exception) -> None:
    """Switching to a vault whose config raises must not crash the worker.

    The new vault's tree loaded, so the switch stands; the bad config is
    surfaced as a warning and the UI lands on the new vault rather than tearing
    down with a raw traceback.
    """

    async def scenario() -> None:
        cli = ConfigErrorSwitchCli(error, bad_config_paths={"/vault-b"})
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Initial load is vault A.
            left = app.query_one("#left-tree", Tree)
            assert node_datas(left) == {"a1"}
            watch_before = cli.watch_starts

            app.action_switch_vault("/vault-b")
            await _settle(pilot)

            # The switch stood: the tree reloaded from B and the app is homed
            # there, even though re-reading B's config failed.
            assert app._active_vault_path == "/vault-b"
            assert app.selected_id == VAULT_ROOT
            assert node_datas(left) == {"b1"}
            # The bad config was surfaced as a warning, not a crash.
            assert any(
                "config could not be read" in n.message for n in app._notifications
            )
            # Watching was still (re)started against the new vault.
            assert cli.watch_starts > watch_before

    asyncio.run(scenario())


def test_switch_survives_malformed_new_vault_config() -> None:
    # A mis-shaped config parses to the wrong type, raising TypeError from the
    # model — not a LotError — which the switch worker must tolerate.
    _switch_survives_bad_config(TypeError("malformed [tui] config"))


def test_switch_survives_unparseable_new_vault_config() -> None:
    # Unparseable YAML raises yaml.YAMLError, likewise non-LotError.
    _switch_survives_bad_config(yaml.YAMLError("could not parse config"))


def test_switch_tolerates_lot_error_config_and_stays_switched() -> None:
    # A LotError from `config_get` is already swallowed inside `_apply_config`
    # (older `lot` without `settings get`): the switch still completes on the
    # new vault's defaults, with no crash.
    async def scenario() -> None:
        cli = ConfigErrorSwitchCli(
            LotError(("settings", "get"), 2, "unrecognized subcommand"),
            bad_config_paths={"/vault-b"},
        )
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_switch_vault("/vault-b")
            await _settle(pilot)

            # The switch stood on the new vault's tree despite the config read
            # failing (absorbed by `_apply_config`).
            assert app._active_vault_path == "/vault-b"
            assert node_datas(app.query_one("#left-tree", Tree)) == {"b1"}

    asyncio.run(scenario())
