"""Tests for the three-column app shell.

The app is booted headless with Textual's ``App.run_test()`` pilot against a
*fake* :class:`LotCli` so no real vault is required. Async scenarios are driven
via ``asyncio.run`` to match the rest of the suite (no pytest-asyncio needed).
"""

from __future__ import annotations

import asyncio

from textual.widgets import Tree

from lot_textual_ui import __version__
from lot_textual_ui.app import VAULT_ROOT, LotTextualApp, node_label
from lot_textual_ui.detail import DetailPane
from lot_textual_ui.keys import ACTION_BINDINGS
from lot_textual_ui.models import (
    ComputedState,
    EffectiveConfig,
    Thing,
    ThingList,
    Update,
)


class FakeLotCli:
    """A stand-in for :class:`LotCli` that returns a canned tree.

    The mounted detail pane also calls ``thing_get``/``thing_updates`` on
    selection, so those are stubbed here with trivial canned data (detail-pane
    rendering is exercised in ``test_detail.py``).
    """

    def __init__(
        self, listing: ThingList, config: EffectiveConfig | None = None
    ) -> None:
        self._listing = listing
        self._config = config if config is not None else EffectiveConfig()
        # Records every `settings set theme` call so tests can assert what the
        # theme-persistence path wrote (and that it stayed quiet when it should).
        self.set_theme_calls: list[str] = []
        # When set to an exception, `settings_set_theme` raises it, so tests can
        # exercise the best-effort "saved failed but the live change stands" path.
        self.set_theme_raises: Exception | None = None

    async def config_get(self) -> EffectiveConfig:
        return self._config

    async def settings_set_theme(self, name: str) -> str:
        if self.set_theme_raises is not None:
            raise self.set_theme_raises
        self.set_theme_calls.append(name)
        return f'set theme = "{name}" in /fake/config.toml'

    async def thing_list(self) -> ThingList:
        return self._listing

    async def thing_get(self, thing_id: str) -> ComputedState:
        return ComputedState(
            status="note", task_id=thing_id, update_id="u1", body="body"
        )

    async def thing_updates(self, thing_id: str) -> list[Update]:
        return [Update(update_id="u1", type="note", at="t", body="body")]

    async def watch(self):
        # No live events in these shell tests; the watch worker completes at once.
        for event in ():
            yield event


def sample_listing() -> ThingList:
    grandchild = Thing(id="g1", name="Grandchild", status="note")
    child = Thing(id="c1", name="Child", status="work", children=[grandchild])
    sibling = Thing(id="c2", name="Sibling", status="done")
    root = Thing(id="r1", name="Root", status="work", children=[child, sibling])
    other = Thing(id="r2", name="Other root", status="note")
    return ThingList(path="/x", things=[root, other])


def make_app() -> LotTextualApp:
    return LotTextualApp(lot_cli=FakeLotCli(sample_listing()))


def _iter_nodes(node):
    """Yield every descendant node under ``node`` (depth-first)."""
    for child in node.children:
        yield child
        yield from _iter_nodes(child)


def node_datas(tree: Tree) -> list[str | None]:
    """Flatten the data payload of every node under a tree's root."""

    result: list[str | None] = []

    def walk(node) -> None:
        for child in node.children:
            result.append(child.data)
            walk(child)

    walk(tree.root)
    return result


def test_version() -> None:
    assert __version__ == "0.1.0"


def test_app_constructs() -> None:
    app = LotTextualApp()
    assert app.TITLE == "LoT"


def test_node_label_includes_status_name() -> None:
    label = node_label(Thing(id="x", name="Thing", status="done"))
    plain = label.plain
    assert "Thing" in plain
    assert "done" in plain  # the status is spelled out, not a glyph
    # The status word is colour-coded via a Rich span.
    assert any(span.style == "grey50" for span in label.spans)


def test_node_label_custom_status_uses_the_fallback_colour() -> None:
    # A custom update type makes the Thing's status the type's name (readme
    # §5.2.5); an arbitrary name renders spelled out with the fallback colour.
    from lot_textual_ui.app import UNKNOWN_STATUS_COLOR

    label = node_label(Thing(id="x", name="Thing", status="wont-do"))
    plain = label.plain
    assert "wont-do" in plain
    assert "Thing" in plain
    assert any(span.style == UNKNOWN_STATUS_COLOR for span in label.spans)


def test_three_columns_exist_and_initial_selection() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # All three columns are present.
            app.query_one("#left-tree", Tree)
            app.query_one("#centre-tree", Tree)
            app.query_one("#detail")
            # Initial selection is the vault root: the left cursor starts on
            # the "LoT" row, and the app opens on the whole-vault view.
            assert app.selected_id == VAULT_ROOT
            assert app.active_id is None
            # Centre tree shows the full vault: every root Thing with all of
            # its descendants, under a "LoT" root row that carries no Thing id.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data is None
            assert set(node_datas(centre)) == {"r1", "c1", "c2", "g1", "r2"}
            # Left tree shows the whole vault's roots and branches: the roots
            # r1/r2 and the branch c1 (it has a grandchild); leaf Things c2/g1
            # are omitted.
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "c1", "r2"}
            assert left.root.data == VAULT_ROOT

    asyncio.run(scenario())


def test_all_three_columns_share_one_background() -> None:
    # The three columns must render with the same background regardless of which
    # holds focus: no per-column shade, and no lightening focus tint on the tree
    # that currently has focus (the left one starts focused).
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            centre = app.query_one("#centre-tree", Tree)
            detail = app.query_one("#detail")
            assert app.focused is left
            # `background_colors[1]` is the effective background *after* any
            # `background-tint` (Textual's Tree:focus tint) is blended in, so this
            # catches both a per-column shade and a focus-only lightening.
            backgrounds = {col.background_colors[1] for col in (left, centre, detail)}
            assert len(backgrounds) == 1, backgrounds

    asyncio.run(scenario())


def test_selection_propagates_to_all_columns() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Select a nested Thing directly through the selection model.
            app.selected_id = "c1"
            await pilot.pause()

            # Left column: the root/branch skeleton, unchanged by the selection
            # (roots r1/r2 and the branch c1).
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "c1", "r2"}

            # Centre column: the selection's descendants.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data == "c1"
            assert set(node_datas(centre)) == {"g1"}

    asyncio.run(scenario())


def test_app_bindings_come_from_central_table() -> None:
    # The seam: the app declares no bindings of its own; it takes the whole
    # central table verbatim so Phase 5 has a single place to override.
    assert LotTextualApp.BINDINGS is ACTION_BINDINGS
    actions = {binding.action for binding in ACTION_BINDINGS}
    assert {
        "quit",
        "cursor_down",
        "cursor_up",
        "cursor_top",
        "cursor_bottom",
        "focus_left",
        "focus_right",
    } <= actions


def test_j_k_move_the_focused_tree_cursor() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            # The left tree is focused on mount.
            assert app.focused is left
            start = left.cursor_line

            await pilot.press("j")
            assert left.cursor_line == start + 1
            await pilot.press("j")
            assert left.cursor_line == start + 2
            await pilot.press("k")
            assert left.cursor_line == start + 1

    asyncio.run(scenario())


def test_g_and_shift_g_jump_to_top_and_bottom() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)

            await pilot.press("G")
            assert left.cursor_line == left.last_line
            await pilot.press("g")
            assert left.cursor_line == 0

    asyncio.run(scenario())


def test_l_and_h_move_focus_across_columns() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            centre = app.query_one("#centre-tree", Tree)
            detail = app.query_one(DetailPane)
            assert app.focused is left

            await pilot.press("l")  # drill in: left -> centre
            assert app.focused is centre
            await pilot.press("l")  # centre -> detail
            assert app.focused is detail
            await pilot.press("l")  # clamped at the rightmost column
            assert app.focused is detail

            await pilot.press("h")  # drill out: detail -> centre
            assert app.focused is centre
            await pilot.press("h")  # centre -> left
            assert app.focused is left
            await pilot.press("h")  # clamped at the leftmost column
            assert app.focused is left

    asyncio.run(scenario())


def test_j_scrolls_the_detail_pane_when_it_is_focused() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            detail = app.query_one(DetailPane)
            # Move focus to the detail pane; j/k then act on it, not the trees.
            await pilot.press("l")
            await pilot.press("l")
            assert app.focused is detail
            # No assertion on offset (canned content may not overflow); the
            # point is j/k route to the pane without touching tree cursors.
            centre = app.query_one("#centre-tree", Tree)
            before = centre.cursor_line
            await pilot.press("j")
            await pilot.press("k")
            assert centre.cursor_line == before

    asyncio.run(scenario())


def select_node(tree: Tree, target) -> None:
    """Drive a tree selection exactly as a click/enter on ``target`` would."""
    tree.select_node(target)
    tree.post_message(Tree.NodeSelected(target))


def test_selecting_a_left_node_moves_the_left_selection() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            # c1 is a branch, so it appears in the left tree; selecting it moves
            # the left selection (and, with it, the centre root / active item).
            target = next(node for node in _iter_nodes(left.root) if node.data == "c1")
            select_node(left, target)
            await pilot.pause()
            assert app.selected_id == "c1"
            assert app.active_id == "c1"

    asyncio.run(scenario())


def test_selecting_a_centre_node_moves_only_the_active_item() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Select the first root; the centre column is rooted there.
            app.selected_id = "r1"
            await pilot.pause()
            centre = app.query_one("#centre-tree", Tree)
            target = next(node for node in centre.root.children if node.data == "c1")
            select_node(centre, target)
            await pilot.pause()
            # A centre-tree selection moves only the active item — the right
            # column follows it, but the left selection stays put.
            assert app.active_id == "c1"
            assert app.selected_id == "r1"
            # The left column is untouched: still the whole root/branch skeleton.
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"r1", "c1", "r2"}

    asyncio.run(scenario())


def test_clicking_a_branch_selects_without_folding_it() -> None:
    # Selecting a branch (as a click or Enter does) must not expand/collapse it:
    # only the toggle arrow left of the status folds a node. Textual's Tree would
    # otherwise toggle on every select via its ``auto_expand`` default, so both
    # trees turn it off.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            centre = app.query_one("#centre-tree", Tree)
            assert left.auto_expand is False
            assert centre.auto_expand is False

            # r1 is a branch shown expanded in the left tree; selecting it (the
            # click path) leaves it expanded rather than collapsing it.
            target = next(node for node in _iter_nodes(left.root) if node.data == "r1")
            assert target.is_expanded
            select_node(left, target)
            await pilot.pause()
            assert target.is_expanded
            # The arrow's own path still folds it — that is the one way to toggle.
            left._toggle_node(target)
            await pilot.pause()
            assert target.is_collapsed

    asyncio.run(scenario())


def test_moving_the_left_cursor_selects_without_enter() -> None:
    # The item under the left cursor becomes the selection as the cursor moves;
    # no Enter/confirm is needed. Moving the cursor also re-roots the centre
    # column and follows through to the active item.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            assert app.focused is left
            # The cursor starts on the LoT root row: the vault is selected.
            assert app.selected_id == VAULT_ROOT

            # Cursor down onto the first root (r1) then the branch c1 nested
            # under it — each move selects the item under the cursor.
            await pilot.press("j")
            assert app.selected_id == "r1"
            await pilot.press("j")
            assert app.selected_id == "c1"
            # The centre column re-roots and the active item follows.
            assert app.active_id == "c1"
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data == "c1"

            await pilot.press("k")
            assert app.selected_id == "r1"
            # Cursor back up onto the LoT root: the whole vault again.
            await pilot.press("k")
            assert app.selected_id == VAULT_ROOT

    asyncio.run(scenario())


def test_moving_the_left_cursor_keeps_the_cursor_position() -> None:
    # Following the cursor with the selection must not yank the cursor back to
    # the top (the left tree is not rebuilt for a cursor-driven selection).
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            await pilot.press("j")
            assert left.cursor_line == 1
            await pilot.press("j")
            assert left.cursor_line == 2
            assert app.selected_id == "c1"

    asyncio.run(scenario())


def test_moving_the_centre_cursor_shows_updates_without_enter() -> None:
    # Whichever item is under the centre cursor has its detail shown: moving the
    # centre cursor moves the active item with no Enter/confirm.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            centre = app.query_one("#centre-tree", Tree)
            # Focus the centre column, then move the cursor onto the first child.
            await pilot.press("l")
            assert app.focused is centre
            await pilot.press("j")
            # The active item (what the right column shows) is the node under the
            # cursor, and the left selection stays put.
            assert app.active_id == centre.cursor_node.data
            assert app.active_id != "r1"
            assert app.selected_id == "r1"

    asyncio.run(scenario())


def test_new_left_selection_resets_the_active_item_to_the_root() -> None:
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.selected_id = "r1"
            await pilot.pause()
            # Drill the centre active into a descendant...
            centre = app.query_one("#centre-tree", Tree)
            child = next(node for node in centre.root.children if node.data == "c1")
            select_node(centre, child)
            await pilot.pause()
            assert app.active_id == "c1"
            # ...then move the left selection: the centre active resets to it.
            app.selected_id = "r2"
            await pilot.pause()
            assert app.active_id == "r2"

    asyncio.run(scenario())


def test_left_tree_excludes_leaf_things() -> None:
    # The left tree is the vault's root/branch skeleton: every root and every
    # branch (a Thing with children), and nothing else. Leaf Things (c2, g1)
    # never appear there — they are reached through the centre column.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            datas = node_datas(left)
            assert set(datas) == {"r1", "c1", "r2"}
            assert "c2" not in datas  # a leaf under r1
            assert "g1" not in datas  # a leaf under the branch c1

    asyncio.run(scenario())


def test_selecting_the_lot_root_shows_the_whole_vault() -> None:
    # The left tree's "LoT" root row stands for the vault as a whole: selecting
    # it (cursor or click) roots the centre column at the full vault tree, and
    # the detail pane empties — the vault root is not a Thing it could show.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            # Move off the vault root onto a Thing first.
            app.selected_id = "c1"
            await pilot.pause()
            assert app.active_id == "c1"

            left = app.query_one("#left-tree", Tree)
            assert left.root.data == VAULT_ROOT
            select_node(left, left.root)
            await pilot.pause()

            assert app.selected_id == VAULT_ROOT
            # The centre column shows the whole vault, leaves included.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data is None
            assert set(node_datas(centre)) == {"r1", "c1", "c2", "g1", "r2"}
            # No Thing is in view: Thing-scoped actions have nothing to target.
            assert app.active_id is None
            assert app.current_thing_id is None

    asyncio.run(scenario())


def test_vault_root_selection_survives_a_reload() -> None:
    # The vault root is not in the Thing index, but it always exists: a reload
    # (or any live watch event) must not bounce the selection off it.
    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.selected_id == VAULT_ROOT
            await app._reload_vault()
            await pilot.pause()
            assert app.selected_id == VAULT_ROOT
            centre = app.query_one("#centre-tree", Tree)
            assert set(node_datas(centre)) == {"r1", "c1", "c2", "g1", "r2"}

    asyncio.run(scenario())


def test_creating_a_leaf_child_selects_its_branch_and_activates_the_child() -> None:
    # A newly created child is a leaf, which the left tree (roots + branches
    # only) cannot show. Its parent branch becomes the left selection — rooting
    # the centre column there — and the new child becomes the centre's active
    # item, so it is highlighted and shown in the detail pane.
    async def scenario() -> None:
        grandchild = Thing(id="g1", name="Grandchild", status="note")
        new_leaf = Thing(id="n1", name="New leaf", status="note")
        child = Thing(
            id="c1", name="Child", status="work", children=[grandchild, new_leaf]
        )
        root = Thing(id="r1", name="Root", status="work", children=[child])
        listing = ThingList(path="/x", things=[root])
        app = LotTextualApp(lot_cli=FakeLotCli(listing))
        async with app.run_test() as pilot:
            await pilot.pause()
            # Simulate the form callback firing for the freshly created leaf.
            # It runs as a worker, so let it settle before asserting.
            app._new_thing_created("n1")
            await app.workers.wait_for_complete()
            await pilot.pause()

            # The left selection is the leaf's branch parent (c1), not the leaf.
            assert app.selected_id == "c1"
            # The right column shows the new leaf.
            assert app.active_id == "n1"
            # The centre column is rooted at the branch and includes the leaf.
            centre = app.query_one("#centre-tree", Tree)
            assert centre.root.data == "c1"
            assert "n1" in node_datas(centre)

    asyncio.run(scenario())


# --- theme / config ---------------------------------------------------------


def app_with_config(config: EffectiveConfig) -> LotTextualApp:
    return LotTextualApp(lot_cli=FakeLotCli(sample_listing(), config=config))


def test_configured_theme_is_applied_on_mount() -> None:
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme="nord"))
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.theme == "nord"

    asyncio.run(scenario())


def test_full_config_is_loaded_and_exposed() -> None:
    # The whole config (not just theme) is parsed and exposed on the app for the
    # downstream keybinding-override and vault-switching work items.
    from lot_textual_ui.models import VaultEntry

    config = EffectiveConfig(
        theme="gruvbox",
        keybindings={"quit": "Q", "new_thing": "n"},
        vaults=[
            VaultEntry(path="~/lot-vault", name="Personal"),
            VaultEntry(path="/srv/shared"),
        ],
        vault_path="/Users/you/lot-vault",
    )

    async def scenario() -> None:
        app = app_with_config(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.config.theme == "gruvbox"
            assert app.config.keybindings == {"quit": "Q", "new_thing": "n"}
            assert [v.name for v in app.config.vaults] == ["Personal", None]
            assert app.config.vault_path == "/Users/you/lot-vault"

    asyncio.run(scenario())


def test_unknown_theme_keeps_default_and_notifies() -> None:
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme="not-a-real-theme"))
        async with app.run_test() as pilot:
            await pilot.pause()
            # A valid (default) theme is retained rather than the bad name.
            assert app.theme in app.available_themes
            assert app.theme != "not-a-real-theme"
            # The problem is surfaced to the user as a notification.
            assert any("not-a-real-theme" in n.message for n in app._notifications)

    asyncio.run(scenario())


def test_no_configured_theme_keeps_textual_default() -> None:
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme=None))
        async with app.run_test() as pilot:
            await pilot.pause()
            # An unset theme leaves Textual's own default untouched (we don't
            # override the user's default colourscheme).
            assert app.theme == LotTextualApp().theme

    asyncio.run(scenario())


def test_switch_theme_palette_command_is_registered() -> None:
    from lot_textual_ui.palette import INTERNAL_COMMANDS

    titles = {command.title for command in INTERNAL_COMMANDS}
    assert "Switch theme" in titles


def test_switch_theme_opens_the_theme_picker() -> None:
    from textual.command import CommandPalette

    async def scenario() -> None:
        app = make_app()
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_switch_theme()
            await pilot.pause()
            # Textual's theme picker is itself a CommandPalette screen.
            assert isinstance(app.screen, CommandPalette)

    asyncio.run(scenario())


def test_runtime_theme_pick_is_persisted_to_config() -> None:
    # Picking a theme at runtime (the palette's ThemeProvider sets ``app.theme``)
    # is written back via ``lot settings set theme`` so it survives a restart.
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme="nord"))
        async with app.run_test() as pilot:
            await pilot.pause()
            # Simulate the theme picker's selection.
            app.theme = "gruvbox"
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.theme == "gruvbox"
            assert app._lot_cli.set_theme_calls == ["gruvbox"]

    asyncio.run(scenario())


def test_configured_theme_on_mount_is_not_persisted() -> None:
    # Applying the *configured* theme on launch must not write it straight back
    # to config — only a deliberate runtime pick persists.
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme="nord"))
        async with app.run_test() as pilot:
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.theme == "nord"
            assert app._lot_cli.set_theme_calls == []

    asyncio.run(scenario())


def test_programmatic_theme_reapply_is_not_persisted() -> None:
    # A programmatic re-apply — as on a vault switch, which re-reads config and
    # calls ``_apply_theme`` — is guarded and does not persist, even after mount.
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme=None))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._apply_theme("gruvbox")
            await pilot.pause()
            await app.workers.wait_for_complete()
            assert app.theme == "gruvbox"
            assert app._lot_cli.set_theme_calls == []

    asyncio.run(scenario())


def test_failed_theme_persist_warns_but_keeps_the_live_theme() -> None:
    # Persistence is best-effort: if `lot settings set theme` fails (e.g. an
    # older `lot`), the live theme change still stands and the user is warned.
    from lot_textual_ui.lot_cli import LotError

    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(theme="nord"))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._lot_cli.set_theme_raises = LotError(
                ["settings", "set", "theme", "gruvbox"],
                2,
                "unrecognized subcommand 'set'",
            )
            app.theme = "gruvbox"
            await pilot.pause()
            await app.workers.wait_for_complete()
            # The live theme still switched…
            assert app.theme == "gruvbox"
            # …and the failure surfaced as a notification.
            assert any(
                "saving it to config failed" in n.message for n in app._notifications
            )

    asyncio.run(scenario())


# --- keybinding overrides ----------------------------------------------------


def test_configured_override_remaps_the_key_on_mount() -> None:
    # A config keybinding (action -> key) rewires the central table on mount:
    # the new key drives the action and the old default no longer does.
    config = EffectiveConfig(keybindings={"cursor_down": "s"})

    async def scenario() -> None:
        app = app_with_config(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            assert app.focused is left
            start = left.cursor_line

            await pilot.press("s")  # the overridden key now moves the cursor
            assert left.cursor_line == start + 1
            await pilot.press("j")  # the old default no longer triggers it
            assert left.cursor_line == start + 1

    asyncio.run(scenario())


def test_override_is_reflected_in_the_active_bindings() -> None:
    # The footer reads the screen's active bindings; the override must appear
    # there (so the hint updates) under the new key, not the old one.
    config = EffectiveConfig(keybindings={"cursor_down": "s"})

    async def scenario() -> None:
        app = app_with_config(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            actions_by_key = {
                key: active.binding.action
                for key, active in app.screen.active_bindings.items()
            }
            assert actions_by_key.get("s") == "cursor_down"
            # The old app-level ``j`` binding is gone (Tree's own keys are
            # unaffected, but the app no longer binds ``j`` to cursor_down).
            assert actions_by_key.get("j") != "cursor_down"

    asyncio.run(scenario())


def test_override_preserves_builtin_quit_bindings() -> None:
    # Rebuilding from the MRO keeps Textual's own ctrl+q / ctrl+c bindings,
    # which are not part of the app's central table and so are never remapped.
    config = EffectiveConfig(keybindings={"cursor_down": "s"})

    async def scenario() -> None:
        app = app_with_config(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            keys = app._bindings.key_to_bindings
            assert "ctrl+q" in keys
            assert "ctrl+c" in keys

    asyncio.run(scenario())


def test_unknown_override_action_is_ignored_without_crashing() -> None:
    config = EffectiveConfig(keybindings={"not_a_real_action": "x"})

    async def scenario() -> None:
        app = app_with_config(config)
        async with app.run_test() as pilot:
            await pilot.pause()
            # The app boots fine and the defaults are intact (j still moves).
            left = app.query_one("#left-tree", Tree)
            start = left.cursor_line
            await pilot.press("j")
            assert left.cursor_line == start + 1

    asyncio.run(scenario())


def test_no_overrides_leaves_default_bindings() -> None:
    # With no keybindings configured the mount-time defaults are untouched.
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig())
        async with app.run_test() as pilot:
            await pilot.pause()
            left = app.query_one("#left-tree", Tree)
            start = left.cursor_line
            await pilot.press("j")
            assert left.cursor_line == start + 1

    asyncio.run(scenario())


# --- vault switching ---------------------------------------------------------

from lot_textual_ui.lot_cli import LotError  # noqa: E402
from lot_textual_ui.models import VaultEntry  # noqa: E402
from lot_textual_ui.vault_picker import VaultPickerScreen  # noqa: E402


class SwitchFakeLotCli:
    """A fake :class:`LotCli` whose tree/config depend on the targeted vault.

    ``set_vault_path`` records the retarget and flips which vault the read
    methods answer for, so a test can prove the app both retargets the adapter
    and reloads from the new vault. A path listed in ``bad_paths`` makes
    ``thing_list`` raise :class:`LotError`, standing in for an invalid vault.
    """

    def __init__(
        self,
        listings: dict[str, ThingList],
        configs: dict[str, EffectiveConfig],
        bad_paths: set[str] | None = None,
    ) -> None:
        self._listings = listings
        self._configs = configs
        self._bad_paths = bad_paths or set()
        self.vault_path = ""  # the currently targeted vault ("" is the initial)
        self.set_calls: list[str] = []
        self.watch_starts = 0

    def set_vault_path(self, path: str) -> None:
        self.vault_path = path
        self.set_calls.append(path)

    async def config_get(self) -> EffectiveConfig:
        return self._configs.get(self.vault_path) or self._configs[""]

    async def thing_list(self) -> ThingList:
        if self.vault_path in self._bad_paths:
            raise LotError(("thing", "list"), 1, "no such vault")
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


def _two_vault_cli(bad_paths: set[str] | None = None) -> SwitchFakeLotCli:
    vaults = [
        VaultEntry(path="/vault-a", name="A"),
        VaultEntry(path="/vault-b", name="B"),
    ]
    listings = {
        "": ThingList(
            path="/vault-a", things=[Thing(id="a1", name="A root", status="note")]
        ),
        "/vault-b": ThingList(
            path="/vault-b", things=[Thing(id="b1", name="B root", status="note")]
        ),
    }
    configs = {
        "": EffectiveConfig(vaults=vaults, vault_path="/vault-a"),
        "/vault-b": EffectiveConfig(vaults=vaults, vault_path="/vault-b"),
    }
    return SwitchFakeLotCli(listings, configs, bad_paths=bad_paths)


async def _settle(pilot) -> None:
    # The switch runs as a worker with several awaits; pump the loop a few times.
    for _ in range(6):
        await pilot.pause()


def test_switch_vault_command_is_registered() -> None:
    from lot_textual_ui.palette import INTERNAL_COMMANDS

    titles = {command.title for command in INTERNAL_COMMANDS}
    assert "Switch vault" in titles


def test_switch_vault_picker_lists_configured_vaults() -> None:
    async def scenario() -> None:
        cli = _two_vault_cli()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_switch_vault_picker()
            await pilot.pause()
            assert isinstance(app.screen, VaultPickerScreen)

    asyncio.run(scenario())


def test_switch_vault_picker_notifies_when_no_vaults() -> None:
    async def scenario() -> None:
        app = app_with_config(EffectiveConfig(vaults=[]))
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_switch_vault_picker()
            await pilot.pause()
            # No modal is pushed; the user is told to configure vaults.
            assert not isinstance(app.screen, VaultPickerScreen)
            assert any("No vaults configured" in n.message for n in app._notifications)

    asyncio.run(scenario())


def test_choosing_a_vault_switches_and_reloads() -> None:
    async def scenario() -> None:
        cli = _two_vault_cli()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Initial load is vault A (whole-vault view of A's tree).
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"a1"}
            watch_before = cli.watch_starts

            app.action_switch_vault_picker()
            await pilot.pause()
            # Highlight the second entry (B) and choose it with enter.
            from textual.widgets import OptionList

            option_list = app.screen.query_one(OptionList)
            option_list.highlighted = 1
            await pilot.pause()
            await pilot.press("enter")
            await _settle(pilot)

            # The adapter was retargeted at B's path...
            assert "/vault-b" in cli.set_calls
            # ...and the tree was reloaded from the new vault, landing on its
            # whole-vault view.
            assert app.selected_id == VAULT_ROOT
            assert set(node_datas(left)) == {"b1"}
            centre = app.query_one("#centre-tree", Tree)
            assert set(node_datas(centre)) == {"b1"}
            assert app._active_vault_path == "/vault-b"
            # Watching was restarted against the new vault.
            assert cli.watch_starts > watch_before

    asyncio.run(scenario())


def test_direct_switch_vault_action_retargets_and_reloads() -> None:
    async def scenario() -> None:
        cli = _two_vault_cli()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app.action_switch_vault("/vault-b")
            await _settle(pilot)
            assert cli.set_calls == ["/vault-b"]
            assert app.selected_id == VAULT_ROOT
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"b1"}

    asyncio.run(scenario())


def test_switch_vault_drops_the_cached_help_tree() -> None:
    # The command navigator caches `lot help`'s tree, which grafts custom
    # update types from the vault's config onto the `update` subtree — so a
    # vault switch must invalidate the cache for re-discovery.
    async def scenario() -> None:
        cli = _two_vault_cli()
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            app._help_tree = {"name": "lot", "subcommands": []}
            app.action_switch_vault("/vault-b")
            await _settle(pilot)
            assert app._help_tree is None

    asyncio.run(scenario())


def test_failed_switch_reverts_and_keeps_current_vault() -> None:
    async def scenario() -> None:
        cli = _two_vault_cli(bad_paths={"/vault-b"})
        app = LotTextualApp(lot_cli=cli)
        async with app.run_test() as pilot:
            await pilot.pause()
            # Pick a real Thing so the revert provably keeps the selection.
            app.selected_id = "a1"
            await pilot.pause()
            watch_before = cli.watch_starts

            app.action_switch_vault("/vault-b")
            await _settle(pilot)

            # The bad switch was attempted then reverted to the old vault.
            assert cli.set_calls == ["/vault-b", "/vault-a"]
            assert cli.vault_path == "/vault-a"
            # The UI stayed on vault A — selection and tree unchanged.
            assert app.selected_id == "a1"
            assert app._active_vault_path == "/vault-a"
            left = app.query_one("#left-tree", Tree)
            assert set(node_datas(left)) == {"a1"}
            # The failure was surfaced and watching was restarted on the old vault.
            assert any(
                "Could not switch vault" in n.message for n in app._notifications
            )
            assert cli.watch_starts > watch_before

    asyncio.run(scenario())
