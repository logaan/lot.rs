"""Tests for the new-Update modal form.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records the
``add_update`` calls it receives and counts ``thing_list`` calls so a
successful submit can be observed triggering a vault reload (which repaints
the trees and re-renders the selected Thing's detail thread). Its config can
carry custom update types, so the dynamic type discovery (readme §1.3/§5.5.1)
is provable without a vault.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Label, RadioButton, RadioSet, TextArea

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.forms import (
    _EMPTY_BODY_MESSAGE,
    TERMINAL_TAG,
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
    UpdateType,
    builtin_update_types,
)

# A custom bodyless terminal type, as `lot settings get` would list it for a
# config carrying `[[update-types]] name="wont-do" takes-body=false
# terminal=true` (readme §1.3).
WONT_DO = UpdateType(name="wont-do", takes_body=False, terminal=True, built_in=False)


class FakeLotCli:
    """A stand-in :class:`LotCli` recording Update calls and reload counts."""

    def __init__(
        self,
        *,
        fail: bool = False,
        update_types: list[UpdateType] | None = None,
    ) -> None:
        self._roots: list[Thing] = [Thing(id="r1", name="Root", status="work")]
        self.update_calls: list[tuple[str, str, str | None]] = []
        self.list_calls = 0
        self._fail = fail
        self._update_types = update_types

    async def config_get(self) -> EffectiveConfig:
        if self._update_types is None:
            return EffectiveConfig()
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

    async def add_update(self, kind: str, thing_id: str, body: str | None) -> str:
        self.update_calls.append((kind, thing_id, body))
        if self._fail:
            raise LotError(("update", kind), 1, "boom")
        return f"upd{len(self.update_calls)}"


def make_app(
    *,
    fail: bool = False,
    update_types: list[UpdateType] | None = None,
) -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(fail=fail, update_types=update_types)
    return LotTextualApp(lot_cli=cli), cli


def custom_types() -> list[UpdateType]:
    """The built-ins plus the custom ``wont-do``, as effective config lists them."""
    return [*builtin_update_types(), WONT_DO]


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
            assert cli.update_calls == [("work", "r1", "wip")]
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

            # `done` takes no body: the CLI is called with body=None.
            assert cli.update_calls == [("done", "r1", None)]
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

            assert cli.update_calls == []
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

            assert cli.update_calls == []
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
            assert cli.update_calls == [("work", "r1", "boom")]
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
            assert cli.update_calls == []

    asyncio.run(scenario())


# --- dynamic update types (custom types from config) -------------------------


def test_form_offers_the_configured_types_in_order() -> None:
    # The radio set mirrors the effective creatable set: the built-ins minus
    # `note` (written by `thing new`, not `lot update`), then the customs.
    async def scenario() -> None:
        app, _cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            buttons = app.screen.query_one("#new-update-type", RadioSet).query(
                RadioButton
            )
            labels = [str(button.label) for button in buttons]
            assert [label.split()[0] for label in labels] == [
                "work",
                "info",
                "done",
                "wont-do",
            ]

    asyncio.run(scenario())


def test_terminal_types_carry_the_terminal_tag() -> None:
    # Terminal types (built-in `done` and the custom `wont-do`) are tagged so
    # it is obvious they retire the Thing's status; the others are not.
    async def scenario() -> None:
        app, _cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            buttons = app.screen.query_one("#new-update-type", RadioSet).query(
                RadioButton
            )
            tagged = {
                str(button.label).split()[0]: TERMINAL_TAG in str(button.label)
                for button in buttons
            }
            assert tagged == {
                "work": False,
                "info": False,
                "done": True,
                "wont-do": True,
            }

    asyncio.run(scenario())


def test_custom_bodyless_type_hides_body_and_submits_none() -> None:
    # A custom takes-body=false type behaves exactly like `done`: the body
    # field is hidden and the CLI is called with body=None.
    async def scenario() -> None:
        app, cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="wont-do")
            await pilot.pause()

            body = app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            assert body.display is False

            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == [("wont-do", "r1", None)]
            assert not isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_custom_body_taking_type_submits_its_body() -> None:
    # A custom type with default flags (takes-body=true) behaves like `work`.
    blocked = UpdateType(name="blocked", takes_body=True, terminal=False)
    types = [*builtin_update_types(), blocked]

    async def scenario() -> None:
        app, cli = make_app(update_types=types)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="blocked")
            await pilot.pause()

            body = app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            assert body.display is True
            body.text = "waiting on review"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == [("blocked", "r1", "waiting on review")]

    asyncio.run(scenario())


def test_unknown_initial_kind_falls_back_to_the_first_type() -> None:
    async def scenario() -> None:
        app, _cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_update_form(kind="bogus")
            await pilot.pause()

            radio_set = app.screen.query_one("#new-update-type", RadioSet)
            assert radio_set.pressed_index == 0  # "work", the first offered

    asyncio.run(scenario())


def test_vault_switch_refreshes_the_offered_types() -> None:
    # The offered set is read from the app's config, which is re-loaded on a
    # vault switch — so a vault defining its own custom types shows them as
    # soon as the app points at it.
    class SwitchingFake(FakeLotCli):
        def __init__(self) -> None:
            super().__init__()
            self.vault_path = ""

        def set_vault_path(self, path: str) -> None:
            self.vault_path = path

        async def config_get(self) -> EffectiveConfig:
            if self.vault_path == "/custom-vault":
                return EffectiveConfig(update_types=custom_types())
            return EffectiveConfig()

    async def scenario() -> None:
        cli = SwitchingFake()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Before the switch only the creatable built-ins are offered.
            assert [t.name for t in app.creatable_update_types()] == [
                "work",
                "info",
                "done",
            ]

            app.action_switch_vault("/custom-vault")
            for _ in range(6):
                await pilot.pause()

            assert [t.name for t in app.creatable_update_types()] == [
                "work",
                "info",
                "done",
                "wont-do",
            ]

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


def test_palette_update_custom_type_opens_the_form_preset_to_it() -> None:
    # A custom type discovered from config gets the same form route as the
    # built-ins when its `update <name>` leaf is picked in the palette.
    async def scenario() -> None:
        app, _cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "wont-do")))
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)
            radio_set = app.screen.query_one("#new-update-type", RadioSet)
            assert radio_set.pressed_index == 3  # work, info, done, wont-do

    asyncio.run(scenario())


def test_palette_update_path_still_falls_through_to_the_placeholder() -> None:
    # `update path` is not a creatable type; it keeps the placeholder toast.
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "path")))
            await pilot.pause()
            assert not isinstance(app.screen, NewUpdateScreen)
            assert any("Not available yet" in n.title for n in app._notifications)

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
