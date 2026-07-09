"""Tests for the type-specific new-Update flows.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records the
``add_update`` calls it receives and counts ``thing_list`` calls so a
successful submit can be observed triggering a vault reload (which repaints
the trees and re-renders the selected Thing's detail thread). Its config can
carry custom update types, so the dynamic type discovery (readme §1.3/§5.5.1)
is provable without a vault.

The update flow is type-specific (no general "pick a type" form):

* a body-taking type (``work``/``info``/custom) opens a form fixed to that
  type — body field only, no type selector;
* a bodyless type (``done``, custom ``takes-body = false``) runs immediately
  on the in-view Thing with no form at all.

(The batch variant keeps a type radio set; that is covered in
``test_batch.py``.)
"""

from __future__ import annotations

import asyncio

from textual.widgets import Button, Label, RadioSet, TextArea

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.batch import ConfirmScreen
from lot_textual_ui.forms import (
    _EMPTY_BODY_MESSAGE,
    UPDATE_BODY_TEXTAREA_ID,
    UPDATE_PREAMBLE_TEXTAREA_ID,
    CommandFormScreen,
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
)
from stock_types import stock_update_types

# A custom bodyless terminal type, as `lot settings get` would list it for a
# config carrying `[[update-types]] name="wont-do" takes-body=false
# terminal=true` (readme §1.3).
WONT_DO = UpdateType(name="wont-do", takes_body=False, terminal=True)


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
        self.preamble_calls: list[str | None] = []
        self.list_calls = 0
        self._fail = fail
        self._update_types = update_types

    async def config_get(self) -> EffectiveConfig:
        if self._update_types is None:
            # Mirror a real seeded vault: its config always carries the stock
            # set (there is no fallback in the models any more).
            return EffectiveConfig(update_types=stock_update_types())
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

    async def add_update(
        self,
        kind: str,
        thing_id: str,
        body: str | None,
        preamble: str | None = None,
    ) -> str:
        self.update_calls.append((kind, thing_id, body))
        # Recorded separately so the long-standing `update_calls` assertions
        # keep their shape.
        self.preamble_calls.append(preamble)
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
    """The stock set plus the custom ``wont-do``, as effective config lists them."""
    return [*stock_update_types(), WONT_DO]


def test_update_form_preamble_is_seeded_for_its_type_and_passed_when_edited() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()

            # The seeded preview names the type this form writes.
            app.open_new_update_form(kind="work")
            await pilot.pause()
            box = app.screen.query_one(f"#{UPDATE_PREAMBLE_TEXTAREA_ID}", TextArea)
            assert "status: work" in box.text
            assert "work-at" in box.text

            # Untouched: no `--preamble` reaches the CLI.
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()
            assert cli.preamble_calls == [None]

            # Edited: the box is passed through.
            app.open_new_update_form(kind="work")
            await pilot.pause()
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "more"
            box = app.screen.query_one(f"#{UPDATE_PREAMBLE_TEXTAREA_ID}", TextArea)
            box.text = box.text + "claude-model: fable\n"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.preamble_calls[-1] is not None
            assert "claude-model: fable" in cli.preamble_calls[-1]

    asyncio.run(scenario())


def test_submit_work_calls_add_update_and_reloads() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
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


def test_form_is_type_fixed_with_no_type_selector() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
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
            app.selected_id = "r1"
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


def test_empty_form_cancels_with_no_confirmation() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)

            # No body typed: escape closes straight away, no discard dialog.
            await pilot.press("escape")
            await pilot.pause()

            assert cli.update_calls == []
            assert not isinstance(app.screen, NewUpdateScreen)
            assert not isinstance(app.screen, ConfirmScreen)

    asyncio.run(scenario())


def test_cancel_with_a_typed_body_confirms_then_closes_on_discard() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()
            assert isinstance(app.screen, NewUpdateScreen)

            # A typed body is not thrown away on a stray escape — the form asks
            # to confirm the discard first.
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "drop"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            app.screen.query_one("#confirm-confirm", Button).press()
            await pilot.pause()

            assert cli.update_calls == []
            assert not isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_add_and_cancel_labels_carry_an_underlined_mnemonic() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            add = app.screen.query_one("#new-update-add", Button)
            cancel = app.screen.query_one("#new-update-cancel", Button)

            # Cancel is assigned first on every modal screen, so it skips the
            # reserved "c"/"a"/"n" and lands on "l" (ctrl+l — the same Cancel
            # chord everywhere). "Add" then skips the reserved "a" for "d".
            assert cancel.label.plain == "Cancel"
            assert cancel.label.markup == "Cance[underline]l[/underline]"
            assert add.label.plain == "Add"
            assert add.label.markup == "A[underline]d[/underline]d"

    asyncio.run(scenario())


def test_ctrl_a_no_longer_submits_it_is_a_reserved_editing_chord() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # ctrl+a used to submit here; it is now a reserved editing chord
            # (the body TextArea's own cursor-to-line-start), so it must NOT
            # add the update.
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+a")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == []
            assert isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_ctrl_d_submits_even_while_the_body_textarea_has_focus() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # Add is ctrl+d. The body TextArea is focused on mount, so the
            # screen's priority binding must win over it.
            app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+d")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == [("work", "r1", "wip")]
            assert not isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_ctrl_n_no_longer_cancels_the_update_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # ctrl+n was the Cancel chord; it is now a reserved cursor-navigation
            # chord (the body TextArea's own emacs cursor-down), so it must not
            # close the form.
            await pilot.press("ctrl+n")
            await pilot.pause()

            assert cli.update_calls == []
            assert isinstance(app.screen, NewUpdateScreen)

    asyncio.run(scenario())


def test_ctrl_l_cancels_the_update_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # ctrl+l is the Cancel chord now; an empty form closes with no prompt.
            await pilot.press("ctrl+l")
            await pilot.pause()

            assert cli.update_calls == []
            assert not isinstance(app.screen, NewUpdateScreen)
            assert not isinstance(app.screen, ConfirmScreen)

    asyncio.run(scenario())


def test_cli_error_surfaces_and_keeps_form_open() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
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


def test_palette_update_work_opens_the_work_form() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
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
            app.selected_id = "r1"
            await pilot.pause()
            listed_before = cli.list_calls
            app.run_lot_command(LeafUpdate(("update", "done")))
            await pilot.pause()
            await pilot.pause()

            # No form: the bodyless update ran straight away on the in-view
            # Thing, and the vault reloaded so its status marker repaints.
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.update_calls == [("done", "r1", None)]
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

            assert cli.update_calls == []

    asyncio.run(scenario())


def test_custom_body_taking_type_opens_its_own_form() -> None:
    # A custom type with default flags (takes-body=true) behaves like `work`:
    # its `update <name>` leaf opens a form fixed to it, and the typed body is
    # submitted through the same generic add_update seam.
    blocked = UpdateType(name="blocked", takes_body=True, terminal=False)
    types = [*stock_update_types(), blocked]

    async def scenario() -> None:
        app, cli = make_app(update_types=types)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "blocked")))
            await pilot.pause()

            assert isinstance(app.screen, NewUpdateScreen)
            assert app.screen._selected_kind() == "blocked"
            body = app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            assert body.display is True
            body.text = "waiting on review"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == [("blocked", "r1", "waiting on review")]

    asyncio.run(scenario())


def test_custom_bodyless_type_runs_immediately() -> None:
    # A custom takes-body=false type behaves exactly like `done`: no form, the
    # CLI is called straight away with body=None.
    async def scenario() -> None:
        app, cli = make_app(update_types=custom_types())
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.run_lot_command(LeafUpdate(("update", "wont-do")))
            await pilot.pause()
            await pilot.pause()

            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.update_calls == [("wont-do", "r1", None)]

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
            return EffectiveConfig(update_types=stock_update_types())

    async def scenario() -> None:
        cli = SwitchingFake()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Before the switch the stock set is offered (every configured
            # type is creatable, `note` included).
            assert [t.name for t in app.creatable_update_types()] == [
                "note",
                "work",
                "info",
                "done",
            ]

            app.action_switch_vault("/custom-vault")
            for _ in range(6):
                await pilot.pause()

            assert [t.name for t in app.creatable_update_types()] == [
                "note",
                "work",
                "info",
                "done",
                "wont-do",
            ]

    asyncio.run(scenario())


def test_palette_update_path_opens_the_generic_command_form() -> None:
    # `update path` is not a creatable update type, so it never opens the
    # NewUpdateScreen; it is a read-only lookup, routed to the generic
    # CommandFormScreen (and never recorded as an update).
    from lot_textual_ui.palette import ArgSpec, LeafCommand

    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            command = LeafCommand(
                path=("update", "path"),
                about="Print the filesystem path of an Update file",
                args=(
                    ArgSpec(
                        name="update",
                        help="Update id",
                        required=True,
                        takes_value=True,
                    ),
                ),
            )
            app.run_lot_command(command)
            await pilot.pause()
            assert isinstance(app.screen, CommandFormScreen)
            assert not isinstance(app.screen, NewUpdateScreen)
            assert cli.update_calls == []

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
