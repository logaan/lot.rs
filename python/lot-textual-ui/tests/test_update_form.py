"""Tests for the type-specific new-Update flows.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records the
``add_update`` calls it receives and counts ``thing_list`` calls so a
successful submit can be observed triggering a vault reload (which repaints
the trees and re-renders the selected Thing's detail thread). Its config can
carry custom update types, so the dynamic type discovery (readme §1.3/§5.5.1)
is provable without a vault.

The update flow is type-specific (no general "pick a type" form):

* a body-taking type (``work``/``info``/custom) opens the **inline** new-Update
  form fixed to that type — a body field at the foot of the detail pane's
  thread, where the Update will land, not a modal popup;
* a bodyless type (``done``, custom ``takes-body = false``) runs immediately
  on the in-view Thing with no form at all.

(The batch variant keeps a type radio set and stays a modal; that is covered in
``test_batch.py``.)
"""

from __future__ import annotations

import asyncio

from textual.widgets import Button, Label, RadioSet, TextArea

from lot_textual_ui.app import LotTextualApp
from lot_textual_ui.batch import ConfirmScreen
from lot_textual_ui.detail import InlineUpdateForm
from lot_textual_ui.forms import (
    _EMPTY_BODY_MESSAGE,
    UPDATE_BODY_TEXTAREA_ID,
    UPDATE_PREAMBLE_TEXTAREA_ID,
    CommandFormScreen,
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


def form_open(app: LotTextualApp) -> bool:
    """Whether the inline new-Update form is currently mounted."""
    return bool(app.query(InlineUpdateForm))


def the_form(app: LotTextualApp) -> InlineUpdateForm:
    """The mounted inline new-Update form (fails if none is open)."""
    return app.query_one(InlineUpdateForm)


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
            app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The work update targeted the selected Thing with the typed body.
            assert cli.update_calls == [("work", "r1", "wip")]
            # The inline form closed and the vault was reloaded (detail re-rendered).
            assert not form_open(app)
            assert cli.list_calls > listed_before

    asyncio.run(scenario())


def test_form_is_inline_in_the_detail_pane_and_type_fixed() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="info")
            await pilot.pause()

            # The form is a plain inline widget (no pushed screen), mounted inside
            # the detail pane — where the Update will land — not a modal.
            from lot_textual_ui.detail import DetailPane

            assert form_open(app)
            assert isinstance(the_form(app).parent, DetailPane)
            # No type radio set — the form is fixed to the type it was opened for,
            # and says so in its title.
            assert not app.query(RadioSet)
            title = app.query_one("#new-update-title", Label)
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
            assert form_open(app)
            error = app.query_one("#new-update-error", Label)
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
            assert form_open(app)

            # No body typed: escape closes straight away, no discard dialog.
            await pilot.press("escape")
            await pilot.pause()

            assert cli.update_calls == []
            assert not form_open(app)
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
            assert form_open(app)

            # A typed body is not thrown away on a stray escape — the form asks
            # to confirm the discard first.
            app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "drop"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            app.screen.query_one("#confirm-confirm", Button).press()
            await pilot.pause()

            assert cli.update_calls == []
            assert not form_open(app)

    asyncio.run(scenario())


def test_add_and_cancel_buttons_have_plain_labels() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            add = app.query_one("#new-update-add", Button)
            cancel = app.query_one("#new-update-cancel", Button)

            # Inline forms are not modal dialogs, so they carry no ctrl+letter
            # button mnemonics — just escape (cancel) and ctrl+s (submit).
            assert cancel.label.plain == "Cancel"
            assert add.label.plain == "Add"

    asyncio.run(scenario())


def test_editing_chord_does_not_submit_or_cancel_the_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # ctrl+a is the body TextArea's own cursor-to-line-start editing
            # chord; the inline form does not bind it, so it must neither submit
            # nor close the form.
            app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+a")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == []
            assert form_open(app)

    asyncio.run(scenario())


def test_space_in_the_body_does_not_open_the_command_navigator() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # ``space`` is the command navigator's priority-bound leader key. With
            # the inline form open the app gates it so a typed space stays a space
            # in the body editor rather than opening the navigator over the form.
            body = app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            body.focus()
            await pilot.press("a", "space", "b")
            await pilot.pause()

            assert len(app.screen_stack) == 1  # no navigator pushed
            assert form_open(app)
            assert body.text == "a b"

    asyncio.run(scenario())


def test_ctrl_s_submits_even_while_the_body_textarea_has_focus() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()

            # The body TextArea is focused on mount; ctrl+s must still submit.
            app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "wip"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.update_calls == [("work", "r1", "wip")]
            assert not form_open(app)

    asyncio.run(scenario())


def test_navigating_away_closes_the_inline_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        # Two roots so there is somewhere else to navigate to.
        cli._roots.append(Thing(id="r2", name="Other", status="note"))

        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app.open_new_update_form(kind="work")
            await pilot.pause()
            assert form_open(app)

            # Selecting another Thing abandons the half-written form (it targeted
            # the Thing we left) rather than leaving it stranded on the new one.
            app.selected_id = "r2"
            await pilot.pause()

            assert not form_open(app)
            assert cli.update_calls == []

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

            app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea).text = "boom"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The CLI was attempted but failed; the form stays open so input is
            # not lost, and can be resubmitted.
            assert cli.update_calls == [("work", "r1", "boom")]
            assert form_open(app)

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

            # Nothing to target: no form opened and no CLI call.
            assert not form_open(app)
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
            assert form_open(app)
            assert the_form(app).kind == "work"

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
            assert not form_open(app)
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

            assert form_open(app)
            assert the_form(app).kind == "blocked"
            body = app.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
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

            assert not form_open(app)
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
    # `update path` is not a creatable update type, so it never opens the inline
    # new-Update form; it is a read-only lookup, routed to the generic
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
            assert not form_open(app)
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
