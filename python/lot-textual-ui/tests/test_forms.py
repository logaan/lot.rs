"""Tests for the new-Thing modal form.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. The fake records
``thing_new`` calls and grows its listing so a successful create can be observed
jumping the selection to the new Thing.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import yaml
from textual.widgets import Button, Input, Label, TextArea

from lot_textual_ui.app import VAULT_ROOT, LotTextualApp
from lot_textual_ui.batch import ConfirmScreen
from lot_textual_ui.command_nav import RESERVED_CTRL_LETTERS, CommandNavScreen
from lot_textual_ui.forms import (
    _EMPTY_NAME_MESSAGE,
    BODY_TEXTAREA_ID,
    PREAMBLE_TEXTAREA_ID,
    NewThingScreen,
    preamble_argument,
    preamble_preview,
)
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


class FakeLotCli:
    """A stand-in :class:`LotCli` that records creates and grows its listing."""

    def __init__(self, *, fail: bool = False) -> None:
        self._roots: list[Thing] = [Thing(id="r1", name="Root", status="work")]
        self.new_calls: list[tuple[str, str, str | None]] = []
        self.preamble_calls: list[str | None] = []
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
        preamble: str | None = None,
    ) -> str:
        self.new_calls.append((name, body, parent))
        # Recorded separately so the long-standing `new_calls` assertions keep
        # their shape.
        self.preamble_calls.append(preamble)
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


def test_preamble_preview_comments_out_every_managed_key() -> None:
    # The preview exists to *show* the frontmatter lot will write, but those
    # keys are exactly the ones `--preamble` rejects — so every one of them must
    # be commented out, leaving a document that carries no fields at all.
    for kind in (None, "work"):
        preview = preamble_preview(kind)
        for line in preview.splitlines():
            assert not line.strip() or line.lstrip().startswith("#"), line
        assert preamble_argument(preview) is None

    # The update form names the concrete type and its timestamp field.
    work = preamble_preview("work")
    assert "status: work" in work
    assert "work-at" in work
    # The new-Thing form cannot know the vault's default type, so it says so
    # rather than guessing a `note`.
    assert "status: work" not in preamble_preview(None)


def test_preamble_argument_ignores_comments_but_keeps_real_fields() -> None:
    # Blank and comment-only boxes contribute no flag.
    assert preamble_argument("") is None
    assert preamble_argument("   \n\t\n") is None
    assert preamble_argument("# a\n#  b: c\n") is None

    # One real field is enough to pass the whole box through verbatim —
    # comments and all — because `lot` is what validates it.
    text = "# a comment\nclaude-model: opus\n"
    assert preamble_argument(text) == text


def test_submit_passes_edited_preamble_and_omits_an_untouched_one() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()

            # An untouched preamble box sends no `--preamble` at all.
            app.open_new_thing_form()
            await pilot.pause()
            app.screen.query_one("#new-thing-name", Input).value = "Plain"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()
            assert cli.preamble_calls == [None]

            # Adding a real field passes the box through to `--preamble`.
            app.open_new_thing_form()
            await pilot.pause()
            app.screen.query_one("#new-thing-name", Input).value = "Flagged"
            box = app.screen.query_one(f"#{PREAMBLE_TEXTAREA_ID}", TextArea)
            box.text = box.text + "claude-model: opus\n"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.preamble_calls[-1] is not None
            assert "claude-model: opus" in cli.preamble_calls[-1]

    asyncio.run(scenario())


def test_untouched_preamble_does_not_trigger_the_discard_prompt() -> None:
    async def scenario() -> None:
        app, _ = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # The box is pre-filled, but with comments only — an otherwise
            # empty form must still close straight away.
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmScreen)
            assert not isinstance(app.screen, NewThingScreen)

            # A real preamble field is content worth confirming the loss of.
            app.open_new_thing_form()
            await pilot.pause()
            box = app.screen.query_one(f"#{PREAMBLE_TEXTAREA_ID}", TextArea)
            box.text = box.text + "claude-model: opus\n"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

    asyncio.run(scenario())


def test_submit_creates_thing_and_jumps_selection() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "My Thing"
            app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "Some body"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.new_calls == [("My Thing", "Some body", None)]
            # Selection jumped to the freshly created Thing.
            assert app.selected_id == "new1"
            # The modal closed after a successful create.
            assert not isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_submit_passes_parent_when_form_seeded_with_one() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form(parent_id="r1", title="New child Thing")
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Child"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            assert cli.new_calls == [("Child", "", "r1")]

    asyncio.run(scenario())


def test_empty_name_blocks_submit() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # Name left blank; body set. Submit must not call the CLI.
            app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).text = "orphan body"
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.new_calls == []
            # The form stays open with an error message shown.
            assert isinstance(app.screen, NewThingScreen)
            error = app.screen.query_one("#new-thing-error", Label)
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

            app.screen.query_one("#new-thing-name", Input).value = "   "
            await pilot.press("ctrl+s")
            await pilot.pause()

            assert cli.new_calls == []
            assert isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_empty_form_cancels_with_no_confirmation() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()
            assert isinstance(app.screen, NewThingScreen)

            # Nothing typed: escape closes straight to the base screen, with no
            # discard dialog to click through.
            await pilot.press("escape")
            await pilot.pause()

            assert cli.new_calls == []
            assert not isinstance(app.screen, NewThingScreen)
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
            assert isinstance(app.screen, NewThingScreen)

            # A filled-in form does not vanish on escape — it asks first, so a
            # stray escape cannot silently discard typed work.
            app.screen.query_one("#new-thing-name", Input).value = "Discarded"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            # Confirming the discard closes the form for good.
            app.screen.query_one("#confirm-confirm", Button).press()
            await pilot.pause()

            assert cli.new_calls == []
            assert not isinstance(app.screen, NewThingScreen)
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

            app.screen.query_one("#new-thing-name", Input).value = "Kept"
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)

            # Declining the discard returns to the form with its content intact.
            await pilot.press("escape")
            await pilot.pause()
            assert isinstance(app.screen, NewThingScreen)
            assert app.screen.query_one("#new-thing-name", Input).value == "Kept"
            assert cli.new_calls == []

    asyncio.run(scenario())


def test_cli_error_surfaces_and_keeps_form_open() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail=True)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Boom"
            await pilot.press("ctrl+s")
            await pilot.pause()
            await pilot.pause()

            # The CLI was attempted but failed; the form stays open so input
            # is not lost, and the selection did not move.
            assert cli.new_calls == [("Boom", "", None)]
            assert isinstance(app.screen, NewThingScreen)
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
            assert isinstance(app.screen, NewThingScreen)

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
            assert isinstance(app.screen, NewThingScreen)
            assert app.screen._parent_id == app.selected_id == "r1"

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

            # No form is pushed; the user is notified instead.
            assert not isinstance(app.screen, NewThingScreen)
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


def test_create_and_cancel_labels_carry_an_underlined_mnemonic() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            create = app.screen.query_one("#new-thing-create", Button)
            cancel = app.screen.query_one("#new-thing-cancel", Button)

            # Cancel is assigned first on every modal screen, so it skips the
            # reserved "c"/"a"/"n" and lands on "l" (ctrl+l — the same Cancel
            # chord everywhere). "Create" then skips the still-reserved "c" for
            # "r".
            assert cancel.label.plain == "Cancel"
            assert cancel.label.markup == "Cance[underline]l[/underline]"
            assert create.label.plain == "Create"
            assert create.label.markup == "C[underline]r[/underline]eate"

            # Neither chosen letter is one of the app-wide reserved ctrl
            # letters (ctrl+c/p/q/z already mean something else entirely).
            assert "l" not in RESERVED_CTRL_LETTERS
            assert "r" not in RESERVED_CTRL_LETTERS

    asyncio.run(scenario())


def test_create_and_send_button_carries_its_own_mnemonic() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            send = app.screen.query_one("#new-thing-send", Button)

            # Assigned last (after Cancel's "n" and Create's "r"), so the first
            # free letter in "Create and send" is the "t" of "Create".
            assert send.label.plain == "Create and send"
            assert send.label.markup == "Crea[underline]t[/underline]e and send"
            assert app.screen._send_key == "ctrl+t"

    asyncio.run(scenario())


def test_create_and_send_creates_then_opens_the_claude_stage() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Hand off"
            # ctrl+t is the "Create and send" mnemonic (see the button test).
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

            app.screen.query_one("#new-thing-name", Input).value = "Just create"
            # Plain Create (ctrl+s) creates without kicking off Claude.
            await pilot.press("ctrl+s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert cli.new_calls == [("Just create", "", None)]
            assert not isinstance(app.screen, NewThingScreen)
            assert not isinstance(app.screen, CommandNavScreen)

    asyncio.run(scenario())


def test_ctrl_r_submits_even_while_the_body_textarea_has_focus() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            app.screen.query_one("#new-thing-name", Input).value = "Via mnemonic"
            # Focus the TextArea, which binds plain ctrl+r itself (word-right
            # movement) — the screen's priority binding must win regardless.
            app.screen.query_one(f"#{BODY_TEXTAREA_ID}", TextArea).focus()
            await pilot.pause()

            await pilot.press("ctrl+r")
            await pilot.pause()
            await pilot.pause()

            assert cli.new_calls == [("Via mnemonic", "", None)]
            assert not isinstance(app.screen, NewThingScreen)

    asyncio.run(scenario())


def test_ctrl_a_no_longer_cancels_it_is_a_reserved_editing_chord() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # ctrl+a used to cancel here — the data-loss trap this change
            # removes. It is now a reserved editing chord (the name Input's
            # own cursor-to-line-start), so it must NOT discard the form.
            app.screen.query_one("#new-thing-name", Input).value = "Kept"
            await pilot.press("ctrl+a")
            await pilot.pause()
            assert isinstance(app.screen, NewThingScreen)
            assert cli.new_calls == []

    asyncio.run(scenario())


def test_ctrl_n_no_longer_cancels_it_is_a_reserved_navigation_chord() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # ctrl+n used to be Cancel here — the reported data-loss trap: a
            # user pressing it to move the cursor down a line lost the whole
            # form. It is a reserved cursor-navigation chord now (the name
            # Input's own emacs cursor-down), so it must neither cancel the form
            # nor raise a discard dialog.
            app.screen.query_one("#new-thing-name", Input).value = "Kept"
            await pilot.press("ctrl+n")
            await pilot.pause()

            assert isinstance(app.screen, NewThingScreen)
            assert cli.new_calls == []

    asyncio.run(scenario())


def test_ctrl_l_cancels_the_form() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.open_new_thing_form()
            await pilot.pause()

            # ctrl+l is the Cancel chord now (every earlier letter of "Cancel"
            # is a reserved editing/navigation chord). An empty form closes with
            # no discard prompt.
            await pilot.press("ctrl+l")
            await pilot.pause()

            assert not isinstance(app.screen, NewThingScreen)
            assert not isinstance(app.screen, ConfirmScreen)
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
