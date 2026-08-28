"""Tests for the inline new-Thing form.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records
``thing_new`` calls and grows its listing so a successful create can be observed
jumping the selection to the new Thing.

The form is the inline :class:`~lot_textual_ui.detail.InlineNewThingForm` widget
(mounted over the detail pane), not a modal screen — so a test asks
``form_open(app)`` / ``the_form(app)`` rather than ``isinstance(app.screen, …)``,
and reads its fields straight off the app (the ids are unique to the one form).
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from textual.widgets import Button, Input, Label, TextArea, Tree

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.batch import ConfirmScreen
from lot_textual_ui.command_nav import CommandNavScreen
from lot_textual_ui.detail import DetailPane, InlineNewThingForm
from lot_textual_ui.forms import (
    _EMPTY_NAME_MESSAGE,
    BODY_TEXTAREA_ID,
)
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


def form_open(app: LotTextualApp) -> bool:
    """Whether the inline new-Thing form is currently mounted."""
    return bool(app.query(InlineNewThingForm))


def the_form(app: LotTextualApp) -> InlineNewThingForm:
    """The mounted inline new-Thing form (fails if none is open)."""
    return app.query_one(InlineNewThingForm)


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

    async def thing_new(
        self,
        name: str,
        body: str,
        parent: str | None = None,
    ) -> str:
        self.new_calls.append((name, body, parent))
        if self._fail:
            raise LotError(("thing", "new"), 1, "boom")
        self._counter += 1
        new_id = f"new{self._counter}"
        self._roots.append(Thing(id=new_id, name=name, status="note"))
        return new_id

    async def help_yaml(self) -> dict:
        # The real command tree, so the "Create and send" follow-up can walk
        # into the ``claude`` command exactly as it would in production.
        fixtures = Path(__file__).parent / "fixtures" / "help.yaml"
        return yaml.safe_load(fixtures.read_text())


def make_app(*, fail: bool = False) -> tuple[LotTextualApp, FakeLotCli]:
    cli = FakeLotCli(fail=fail)
    return LotTextualApp(lot_cli=cli), cli


def test_form_carries_no_preamble_box() -> None:
    # Preamble is an Update-level concern (the Update forms keep their box); the
    # new-Thing form collects a name and a body and nothing else, so an empty
    # form closes with no discard prompt and no `--preamble` reaches the CLI.
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            form = the_form(app)
            assert not form.query(TextArea).filter("#new-thing-preamble")
            assert [area.id for area in form.query(TextArea)] == [BODY_TEXTAREA_ID]

            app.query_one("#new-thing-name", Input).value = "Plain"
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert cli.new_calls == [("Plain", "", None)]

    asyncio.run(scenario())


def test_empty_form_closes_without_a_discard_prompt() -> None:
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert not form_open(app)

    asyncio.run(scenario())


def test_title_sits_at_the_top_and_the_body_fills_the_column() -> None:
    # The form covers the detail column rather than sizing to its content: the
    # title lands on the pane's first line, and the body editor — the only
    # flexible row — grows into everything the other fields leave over.
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            form = the_form(app)
            detail = app.query_one("#detail")
            title = app.query_one("#new-thing-title", Label)
            body = app.query_one(f"#{BODY_TEXTAREA_ID}", TextArea)

            # Nothing but #detail's own one-row padding sits above the title.
            assert title.region.y == detail.content_region.y

            # The body takes the leftover height: it is far taller than the
            # fixed 8 rows it used to be, and reaches the buttons at the foot.
            buttons = app.query_one("#new-thing-buttons")
            assert body.region.height > 8
            assert body.region.bottom <= buttons.region.y
            assert form.region.bottom == detail.content_region.bottom

    asyncio.run(scenario())


def test_form_expands_across_the_working_columns() -> None:
    """Creation gets the descendants + detail canvas, without hiding navigation."""

    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            left = app.query_one("#left-tree", Tree)
            centre = app.query_one("#centre-tree", Tree)
            detail = app.query_one("#detail")
            form = the_form(app)

            assert left.display is True
            assert centre.display is False
            assert detail.region.x == left.region.right
            assert detail.region.width > left.region.width
            assert form.region.width == detail.content_region.width

            await pilot.press("escape")
            await pilot.pause()
            assert centre.display is True

    asyncio.run(scenario())


def test_submit_creates_thing_and_jumps_selection() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "My Thing"
            app.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "Some body"
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.new_calls == [("My Thing", "Some body", None)]
            # Selection jumped to the freshly created Thing.
            assert app.selected_id == "new1"
            # The form closed after a successful create.
            assert not form_open(app)

    asyncio.run(scenario())


def test_submit_passes_parent_when_form_seeded_with_one() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form(parent_id="r1", title="New child Thing")
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Child"
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.new_calls == [("Child", "", "r1")]

    asyncio.run(scenario())


def test_form_covers_the_detail_pane_and_restores_it_on_close() -> None:
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            detail = app.query_one(DetailPane)
            assert detail.display is True

            # Opening the inline form hides the update thread it is mounted over.
            app.open_new_thing_form()
            await pilot.pause()
            assert form_open(app)
            assert detail.display is False

            # Cancelling restores the pane.
            await pilot.press("escape")
            await pilot.pause()
            assert not form_open(app)
            assert detail.display is True

    asyncio.run(scenario())


def test_empty_name_blocks_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # Name left blank; body set. Submit must not call the CLI.
            app.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "orphan body"
            await pilot.press("ctrl+r")
            await pilot.pause()

            assert cli.new_calls == []
            # The form stays open with an error message shown.
            assert form_open(app)
            error = app.query_one("#new-thing-error", Label)
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

            app.query_one("#new-thing-name", Input).value = "   "
            await pilot.press("ctrl+r")
            await pilot.pause()

            assert cli.new_calls == []
            assert form_open(app)

    asyncio.run(scenario())


def test_empty_form_cancels_with_no_confirmation() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()
            assert form_open(app)

            # Nothing typed: escape closes straight away, with no discard dialog.
            await pilot.press("escape")
            await pilot.pause()

            assert cli.new_calls == []
            assert not form_open(app)
            assert not isinstance(app.screen, ConfirmScreen)
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_cancel_with_content_confirms_then_closes_on_discard() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()
            assert form_open(app)

            # A filled-in form does not vanish on escape — it asks first, so a
            # stray escape cannot silently discard typed work.
            app.query_one("#new-thing-name", Input).value = "Discarded"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            # Confirming the discard closes the form for good.
            app.screen.query_one("#confirm-confirm", Button).press()
            await pilot.pause()

            assert cli.new_calls == []
            assert not form_open(app)
            # Selection unchanged (still the launch-time vault root).
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_cancel_with_content_can_be_kept_editing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Kept"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            # Declining the discard returns to the form with its content intact.
            await pilot.press("escape")
            await pilot.pause()
            assert form_open(app)
            assert app.query_one("#new-thing-name", Input).value == "Kept"
            assert cli.new_calls == []

    asyncio.run(scenario())


def test_cli_error_surfaces_and_keeps_form_open() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Boom"
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The CLI was attempted but failed; the form stays open so input
            # is not lost, and the selection did not move.
            assert cli.new_calls == [("Boom", "", None)]
            assert form_open(app)
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
            assert form_open(app)

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
            assert form_open(app)
            assert the_form(app).parent_id == app.selected_id == "r1"

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

            # No form is opened; the user is notified instead.
            assert not form_open(app)
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


def test_buttons_carry_underlined_mnemonics_matching_their_shortcuts() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            cancel = app.query_one("#new-thing-cancel", Button)
            create = app.query_one("#new-thing-create", Button)
            send = app.query_one("#new-thing-send", Button)

            # Each button underlines the letter that is its own ctrl-shortcut:
            # Cancel→ctrl+l, Create→ctrl+r, Create and send→ctrl+t.
            assert cancel.label.markup == "Cance[underline]l[/underline]"
            assert create.label.markup == "C[underline]r[/underline]eate"
            assert send.label.markup == "Crea[underline]t[/underline]e and send"

            # Create is the primary (default) action; Create and send is not.
            assert create.variant == "primary"
            assert send.variant == "default"
            assert cancel.variant == "default"

    asyncio.run(scenario())


def test_create_and_send_creates_then_opens_the_claude_stage() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Hand off"
            # ctrl+t is the "Create and send" chord.
            await pilot.press("ctrl+t")
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The Thing was created exactly like plain Create, and the
            # selection jumped to it.
            assert cli.new_calls == [("Hand off", "", None)]
            assert app.active_id == "new1"
            # …then the command navigator opened, parked at the ``claude``
            # command — the user picks how to hand it off (send + model, or a
            # future claude action) rather than it firing send blind.
            assert isinstance(app.screen, CommandNavScreen)
            assert app.screen._nav.breadcrumb() == "lot claude"

    asyncio.run(scenario())


def test_plain_create_does_not_open_the_claude_stage() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Just create"
            # Plain Create (ctrl+r) creates without kicking off Claude.
            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.new_calls == [("Just create", "", None)]
            assert not form_open(app)
            assert not isinstance(app.screen, CommandNavScreen)

    asyncio.run(scenario())


def test_create_chord_submits_even_while_the_body_textarea_has_focus() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.query_one("#new-thing-name", Input).value = "Via chord"
            # The body TextArea is not focused on mount (the name is), so focus
            # it explicitly: the Create chord (ctrl+r) is bound priority, so it
            # must still submit from there.
            app.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).focus()
            await pilot.pause()

            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.new_calls == [("Via chord", "", None)]
            assert not form_open(app)

    asyncio.run(scenario())


def test_editing_chord_does_not_submit_or_cancel_the_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # ctrl+a / ctrl+n are the name Input's own emacs editing chords
            # (cursor-to-line-start, cursor-down). The inline form binds neither,
            # so — unlike the old modal, where they were the data-loss cancel
            # trap — they must neither submit nor close the form.
            app.query_one("#new-thing-name", Input).value = "Kept"
            await pilot.press("ctrl+a")
            await pilot.press("ctrl+n")
            await pilot.pause()

            assert form_open(app)
            assert cli.new_calls == []

    asyncio.run(scenario())


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
