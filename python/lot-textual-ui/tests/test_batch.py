"""Tests for multi-select marks and the batch operations over the marked set.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` (the established pattern — see ``test_app.py`` /
``test_update_form.py``) that records the ``thing_move`` / ``thing_archive`` /
``update_*`` calls it receives and can be told to fail for specific ids, so
per-item error handling is provable without a vault.
"""

from __future__ import annotations

import asyncio

from textual.widgets import Button, Label, OptionList, TextArea, Tree

from lot_textual_ui.app import MARK_INDICATOR, LotTextualApp, node_label
from lot_textual_ui.batch import (
    TOP_LEVEL,
    ConfirmScreen,
    ThingPickerScreen,
    flatten_things,
)
from lot_textual_ui.forms import UPDATE_BODY_TEXTAREA_ID, BatchUpdateScreen
from lot_textual_ui.keys import ACTION_BINDINGS
from lot_textual_ui.lot_cli import LotError
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)
from stock_types import stock_update_types


class BatchFakeLotCli:
    """A fake :class:`LotCli` recording batch mutations, with injectable failures.

    ``fail_ids`` makes any mutation targeting those ids raise
    :class:`LotError` with ``fail_message`` — standing in for the CLI's
    single-line errors (cycles, collisions, the auto-commit refusal, …).
    ``thing_archive`` also removes the Thing from the canned listing, so the
    post-batch reload sees it gone (as the real CLI would).
    """

    def __init__(
        self,
        listing: ThingList,
        fail_ids: set[str] | None = None,
        fail_message: str = "boom",
    ) -> None:
        self._listing = listing
        self._fail_ids = fail_ids or set()
        self._fail_message = fail_message
        self.move_calls: list[tuple[str, str | None, bool]] = []
        self.archive_calls: list[str] = []
        self.vault_archive_calls = 0
        self.update_calls: list[tuple[str, str, str | None]] = []
        self.list_calls = 0

    async def config_get(self) -> EffectiveConfig:
        # Mirror a real seeded vault: its config always carries the stock set.
        return EffectiveConfig(update_types=stock_update_types())

    async def thing_list(self) -> ThingList:
        self.list_calls += 1
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        for event in ():
            yield event

    def _maybe_fail(self, thing_id: str, args: tuple[str, ...]) -> None:
        if thing_id in self._fail_ids:
            raise LotError(args, 1, self._fail_message)

    async def thing_move(
        self, thing_id: str, parent: str | None = None, root: bool = False
    ) -> str:
        self._maybe_fail(thing_id, ("thing", "move"))
        self.move_calls.append((thing_id, parent, root))
        return thing_id

    async def thing_archive(self, thing_id: str) -> str:
        self._maybe_fail(thing_id, ("thing", "archive"))
        self.archive_calls.append(thing_id)
        self._remove(self._listing.things, thing_id)
        return thing_id

    async def vault_archive(self) -> list[str]:
        # The vault-wide archive targets no particular id; the sentinel
        # "vault" in ``fail_ids`` makes it fail (the auto-commit refusal).
        self._maybe_fail("vault", ("vault", "archive"))
        self.vault_archive_calls += 1
        archived = self._done_ids(self._listing.things)
        for thing_id in archived:
            self._remove(self._listing.things, thing_id)
        return archived

    def _done_ids(self, things: list[Thing]) -> list[str]:
        """Done Things top-down, as `lot vault archive` selects them (§5.4.2)."""
        ids: list[str] = []
        for thing in things:
            if thing.status == "done":
                ids.append(thing.id)
            else:
                ids.extend(self._done_ids(thing.children))
        return ids

    async def add_update(self, kind: str, thing_id: str, body: str | None) -> str:
        self._maybe_fail(thing_id, ("update", kind))
        self.update_calls.append((kind, thing_id, body))
        return "lot:new-update"

    def _remove(self, things: list[Thing], thing_id: str) -> None:
        things[:] = [t for t in things if t.id != thing_id]
        for thing in things:
            self._remove(thing.children, thing_id)


def sample_listing() -> ThingList:
    grandchild = Thing(id="g1", name="Grandchild", status="note")
    child = Thing(id="c1", name="Child", status="work", children=[grandchild])
    sibling = Thing(id="c2", name="Sibling", status="done")
    root = Thing(id="r1", name="Root", status="work", children=[child, sibling])
    other = Thing(id="r2", name="Other root", status="note")
    return ThingList(path="/x", things=[root, other])


def make_app(
    fail_ids: set[str] | None = None, fail_message: str = "boom"
) -> tuple[LotTextualApp, BatchFakeLotCli]:
    cli = BatchFakeLotCli(
        sample_listing(), fail_ids=fail_ids, fail_message=fail_message
    )
    return LotTextualApp(lot_cli=cli), cli


def find_node(tree: Tree, data: str):
    def walk(node):
        for child in node.children:
            if child.data == data:
                return child
            found = walk(child)
            if found is not None:
                return found
        return None

    if tree.root.data == data:
        return tree.root
    return walk(tree.root)


async def _settle(pilot) -> None:
    # Batch workers await several times per item; pump the loop generously.
    for _ in range(8):
        await pilot.pause()


# --- bindings and labels ------------------------------------------------------


def test_mark_and_batch_bindings_come_from_the_central_table() -> None:
    keys_by_action = {b.action: b.key for b in ACTION_BINDINGS}
    # Space is the command navigator's leader, so the toggle lives on `x`.
    assert keys_by_action["toggle_mark"] == "x"
    assert keys_by_action["clear_marks"] == "u"
    assert keys_by_action["batch_move"] == "m"
    assert keys_by_action["batch_archive"] == "d"
    assert keys_by_action["batch_update"] == "U"


def test_node_label_carries_the_mark_indicator() -> None:
    thing = Thing(id="x", name="Thing", status="work")
    assert MARK_INDICATOR in node_label(thing, marked=True).plain
    assert MARK_INDICATOR not in node_label(thing, marked=False).plain
    # Marked and unmarked rows stay column-aligned: same prefix width.
    marked = node_label(thing, marked=True).plain
    unmarked = node_label(thing, marked=False).plain
    assert len(marked) == len(unmarked)
    assert marked.index("work") == unmarked.index("work")


def test_flatten_things_orders_and_excludes() -> None:
    rows = flatten_things(sample_listing().things, exclude={"c1", "c2"})
    assert [(t.id, depth) for t, depth in rows] == [
        ("r1", 0),
        ("g1", 2),  # keeps its real depth even with its parent excluded
        ("r2", 0),
    ]


# --- marking ------------------------------------------------------------------


def test_x_toggles_a_mark_with_a_visible_indicator() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            assert app.focused is left
            # The cursor starts on the "LoT" placeholder root; step onto r1.
            await pilot.press("j")
            assert left.cursor_node.data == "r1"

            await pilot.press("x")
            assert app.marked_ids == {"r1"}
            assert MARK_INDICATOR in str(find_node(left, "r1").label)
            # The same Thing roots the centre column: marked there too.
            centre = app.query_one("#centre-tree", Tree)
            assert MARK_INDICATOR in str(centre.root.label)

            await pilot.press("x")  # toggling again unmarks
            assert app.marked_ids == frozenset()
            assert MARK_INDICATOR not in str(find_node(left, "r1").label)

    asyncio.run(scenario())


def test_marking_in_the_centre_tree_targets_its_cursor() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"  # root the centre column at r1
            await pilot.pause()
            await pilot.press("l")  # focus the centre column
            await pilot.press("j")  # cursor onto the first child
            centre = app.query_one("#centre-tree", Tree)
            target = centre.cursor_node.data
            assert target != "r1"

            await pilot.press("x")
            assert app.marked_ids == {target}
            assert MARK_INDICATOR in str(find_node(centre, target).label)

    asyncio.run(scenario())


def test_u_clears_all_marks() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("j")  # off the placeholder root, onto r1
            await pilot.press("x")  # mark r1
            await pilot.press("j")  # onto the c1 branch (cursor-driven)
            await pilot.press("x")  # mark c1
            assert app.marked_ids == {"r1", "c1"}

            await pilot.press("u")
            assert app.marked_ids == frozenset()
            left = app.query_one("#left-tree", Tree)
            assert MARK_INDICATOR not in str(find_node(left, "r1").label)
            assert MARK_INDICATOR not in str(find_node(left, "c1").label)

    asyncio.run(scenario())


def test_marks_survive_a_tree_rebuild() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("j")  # off the placeholder root, onto r1
            await pilot.press("x")  # mark r1
            app.selected_id = "c1"  # re-derives both trees
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            assert MARK_INDICATOR in str(find_node(left, "r1").label)

    asyncio.run(scenario())


def test_marking_the_placeholder_root_warns() -> None:
    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("g")  # cursor to the left tree's "LoT" root
            await pilot.press("x")
            assert app.marked_ids == frozenset()
            assert any("Nothing to mark" in n.title for n in app._notifications)

    asyncio.run(scenario())


# --- batch move -----------------------------------------------------------------


def test_batch_move_with_nothing_marked_warns() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_batch_move()
            await pilot.pause()
            assert not isinstance(app.screen, ThingPickerScreen)
            assert any("Nothing marked" in n.title for n in app._notifications)
            assert cli.move_calls == []

    asyncio.run(scenario())


def test_batch_move_via_picker_moves_each_marked_thing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app.action_batch_move()
            await pilot.pause()
            assert isinstance(app.screen, ThingPickerScreen)

            # Options: Top level, then the tree minus the marked Things
            # (r1, g1, r2). Choose r2 — the last option.
            option_list = app.screen.query_one(OptionList)
            assert option_list.option_count == 4
            option_list.highlighted = 3
            await pilot.pause()
            await pilot.press("enter")
            await _settle(pilot)

            # One sequential move per marked Thing, in tree order.
            assert cli.move_calls == [("c1", "r2", False), ("c2", "r2", False)]
            # All succeeded: unmarked, and a success summary was shown.
            assert app.marked_ids == frozenset()
            assert any(
                "Move marked Things" in n.title and "2 Things processed" in n.message
                for n in app._notifications
            )

    asyncio.run(scenario())


def test_batch_move_top_level_uses_root() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1"})
            app._move_target_chosen(TOP_LEVEL)
            await _settle(pilot)
            assert cli.move_calls == [("c1", None, True)]

    asyncio.run(scenario())


def test_cancelling_the_picker_moves_nothing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1"})
            app.action_batch_move()
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot)
            assert cli.move_calls == []
            assert app.marked_ids == {"c1"}  # marks are kept for a retry

    asyncio.run(scenario())


def test_batch_move_failure_continues_and_reports_per_item() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail_ids={"c1"}, fail_message="destination collision")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app._move_target_chosen("r2")
            await _settle(pilot)

            # c1 failed but c2 was still attempted (no silent abort)...
            assert cli.move_calls == [("c2", "r2", False)]
            # ...the failure keeps its mark for a retry, the success is unmarked...
            assert app.marked_ids == {"c1"}
            # ...and the report names the failed Thing with the CLI's error text.
            failure = next(n for n in app._notifications if n.severity == "error")
            assert "1 of 2 succeeded" in failure.message
            assert "Child" in failure.message
            assert "destination collision" in failure.message

    asyncio.run(scenario())


# --- batch archive ---------------------------------------------------------------


def test_batch_archive_confirms_with_the_count_then_runs() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app.action_batch_archive()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            # The dialog states how many Things are about to be archived.
            message = app.screen.query_one("#confirm-message", Label)
            assert "2 marked Things" in str(getattr(message, "_Static__content", ""))

            app.screen.query_one("#confirm-confirm", Button).press()
            await _settle(pilot)

            assert cli.archive_calls == ["c1", "c2"]
            assert app.marked_ids == frozenset()

    asyncio.run(scenario())


def test_batch_archive_cancel_archives_nothing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1"})
            app.action_batch_archive()
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot)
            assert cli.archive_calls == []
            assert app.marked_ids == {"c1"}

    asyncio.run(scenario())


def test_archive_surfaces_the_cli_error_text() -> None:
    # `lot thing archive` refuses when vault.auto-commit=false; its error text
    # must reach the user verbatim in the per-item report.
    refusal = "archiving requires vault.auto-commit=true"

    async def scenario() -> None:
        app, cli = make_app(fail_ids={"c1"}, fail_message=refusal)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1"})
            app._archive_confirmed(True)
            await _settle(pilot)
            assert cli.archive_calls == []
            failure = next(n for n in app._notifications if n.severity == "error")
            assert refusal in failure.message
            assert app.marked_ids == {"c1"}

    asyncio.run(scenario())


# --- vault archive (every done Thing, no marks) ----------------------------------


def test_vault_archive_confirms_then_archives_every_done_thing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_vault_archive()
            await pilot.pause()
            assert isinstance(app.screen, ConfirmScreen)
            # The dialog says what a vault-wide archive takes: every done Thing.
            message = app.screen.query_one("#confirm-message", Label)
            assert "done Thing" in str(getattr(message, "_Static__content", ""))

            app.screen.query_one("#confirm-confirm", Button).press()
            await _settle(pilot)

            # One CLI call archived the vault's only done Thing (c2), and the
            # reload dropped it from the index; everything else survived.
            assert cli.vault_archive_calls == 1
            assert app.thing_by_id("c2") is None
            assert app.thing_by_id("r1") is not None
            assert any(
                "Archived 1 done Thing." in n.message for n in app._notifications
            )

    asyncio.run(scenario())


def test_vault_archive_cancel_archives_nothing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_vault_archive()
            await pilot.pause()
            await pilot.press("escape")
            await _settle(pilot)
            assert cli.vault_archive_calls == 0
            assert app.thing_by_id("c2") is not None

    asyncio.run(scenario())


def test_vault_archive_with_no_done_things_reports_so() -> None:
    async def scenario() -> None:
        listing = ThingList(
            path="/x", things=[Thing(id="r1", name="Root", status="work")]
        )
        cli = BatchFakeLotCli(listing)
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._vault_archive_confirmed(True)
            await _settle(pilot)
            assert cli.vault_archive_calls == 1
            assert any(
                "No done Things to archive." in n.message for n in app._notifications
            )

    asyncio.run(scenario())


def test_vault_archive_surfaces_the_cli_error_text() -> None:
    # `lot vault archive` refuses when vault.auto-commit=false; its error text
    # must reach the user verbatim.
    refusal = "archiving requires vault.auto-commit=true"

    async def scenario() -> None:
        app, cli = make_app(fail_ids={"vault"}, fail_message=refusal)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._vault_archive_confirmed(True)
            await _settle(pilot)
            assert cli.vault_archive_calls == 0
            failure = next(n for n in app._notifications if n.severity == "error")
            assert refusal in failure.message
            # Nothing was archived: the done Thing is still in the index.
            assert app.thing_by_id("c2") is not None

    asyncio.run(scenario())


# --- archive and the selection ----------------------------------------------------


def test_archiving_the_selection_falls_back_coherently() -> None:
    # Archiving the selected Thing must leave the app on a live selection with
    # no stale mark pointing at the vanished Thing.
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            app._marked.update({"r1"})
            app._archive_confirmed(True)
            await _settle(pilot)

            assert cli.archive_calls == ["r1"]
            # The fake's listing dropped r1; the reload re-resolved everything.
            assert app.selected_id == "r2"
            assert app.marked_ids == frozenset()
            left = app.query_one("#left-tree", Tree)
            assert find_node(left, "r1") is None

    asyncio.run(scenario())


# --- batch update ----------------------------------------------------------------


def test_batch_update_form_applies_one_update_to_every_marked_thing() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app.action_batch_update()
            await pilot.pause()
            assert isinstance(app.screen, BatchUpdateScreen)
            # The form names the batch target, not a single Thing.
            target_label = app.screen.query_one("#new-update-target", Label)
            assert "2 marked Things" in str(
                getattr(target_label, "_Static__content", "")
            )

            body = app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            body.text = "swept"
            await pilot.press("ctrl+s")
            await _settle(pilot)

            # The initially selected type is the first configured one (the
            # stock set starts with `note`).
            assert cli.update_calls == [
                ("note", "c1", "swept"),
                ("note", "c2", "swept"),
            ]
            assert app.marked_ids == frozenset()  # marks cleared on success

    asyncio.run(scenario())


def test_batch_update_done_needs_no_body() -> None:
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app._batch_update_submitted(("done", None))
            await _settle(pilot)
            assert cli.update_calls == [("done", "c1", None), ("done", "c2", None)]

    asyncio.run(scenario())


def test_batch_update_form_offers_custom_types_and_submits_none_body() -> None:
    # Parity with the single-Thing form: the batch form's radio set carries the
    # config-discovered custom types, hides the body for a takes-body=false
    # pick, and the batch applies `add_update(<custom>, <id>, None)` per Thing.
    from textual.widgets import RadioButton, RadioSet

    from lot_textual_ui.models import UpdateType
    from stock_types import stock_update_types

    wont_do = UpdateType(name="wont-do", takes_body=False, terminal=True)

    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Stand in for a config whose vault defines the custom type (the
            # mount-time config load already ran, so patch the loaded config).
            app._config = EffectiveConfig(update_types=[*stock_update_types(), wont_do])
            app._marked.update({"c1", "c2"})
            app.action_batch_update()
            await pilot.pause()
            assert isinstance(app.screen, BatchUpdateScreen)

            radio_set = app.screen.query_one("#new-update-type", RadioSet)
            buttons = list(radio_set.query(RadioButton))
            labels = [str(b.label).split()[0] for b in buttons]
            assert labels == ["note", "work", "info", "done", "wont-do"]

            # Pick the custom bodyless type: the body field hides, and
            # submitting needs no body.
            buttons[4].value = True  # press the wont-do radio
            await pilot.pause()
            body = app.screen.query_one(f"#{UPDATE_BODY_TEXTAREA_ID}", TextArea)
            assert body.display is False

            await pilot.press("ctrl+s")
            await _settle(pilot)

            assert cli.update_calls == [
                ("wont-do", "c1", None),
                ("wont-do", "c2", None),
            ]
            assert app.marked_ids == frozenset()

    asyncio.run(scenario())


def test_batch_update_terminal_types_carry_the_terminal_tag() -> None:
    # Terminal types (built-in `done` and the custom `wont-do`) are tagged in
    # the batch form's radio set so it is obvious they retire the Thing's
    # status; the others are not. (The single-Thing form has no radio set —
    # this is the one update form with a type selector.)
    from textual.widgets import RadioButton, RadioSet

    from lot_textual_ui.forms import TERMINAL_TAG
    from lot_textual_ui.models import UpdateType
    from stock_types import stock_update_types

    wont_do = UpdateType(name="wont-do", takes_body=False, terminal=True)

    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._config = EffectiveConfig(update_types=[*stock_update_types(), wont_do])
            app._marked.update({"c1"})
            app.action_batch_update()
            await pilot.pause()

            radio_set = app.screen.query_one("#new-update-type", RadioSet)
            tagged = {
                str(b.label).split()[0]: TERMINAL_TAG in str(b.label)
                for b in radio_set.query(RadioButton)
            }
            assert tagged == {
                "note": False,
                "work": False,
                "info": False,
                "done": True,
                "wont-do": True,
            }

    asyncio.run(scenario())


def test_batch_update_partial_failure_keeps_the_failed_mark() -> None:
    async def scenario() -> None:
        app, cli = make_app(fail_ids={"c2"}, fail_message="no such thing")
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1", "c2"})
            app._batch_update_submitted(("info", "result"))
            await _settle(pilot)

            assert cli.update_calls == [("info", "c1", "result")]
            assert app.marked_ids == {"c2"}
            failure = next(n for n in app._notifications if n.severity == "error")
            assert "Sibling" in failure.message
            assert "no such thing" in failure.message

    asyncio.run(scenario())


# --- coherence -------------------------------------------------------------------


def test_batch_keys_are_inert_while_a_modal_is_up() -> None:
    # A stray batch key typed while a modal is open must not queue an operation
    # behind it (check_action gates the base-screen-only actions).
    async def scenario() -> None:
        app, cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c1"})
            app.action_batch_move()  # a modal is now on top
            await pilot.pause()
            assert isinstance(app.screen, ThingPickerScreen)

            await pilot.press("d")  # would be batch_archive on the base screen
            await pilot.press("x")  # would be toggle_mark
            await _settle(pilot)
            assert cli.archive_calls == []
            assert app.marked_ids == {"c1"}
            assert isinstance(app.screen, ThingPickerScreen)

    asyncio.run(scenario())


def test_external_deletion_prunes_the_mark() -> None:
    # A watch-driven deletion of a marked Thing must drop its mark too.
    from lot_textual_ui.models import WatchEvent

    async def scenario() -> None:
        app, _cli = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app._marked.update({"c2"})
            await app._apply_event(WatchEvent(kind="deleted", id="c2"))
            await pilot.pause()
            assert app.marked_ids == frozenset()

    asyncio.run(scenario())
