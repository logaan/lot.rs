"""The LoT Textual application: a three-column, selection-driven browser.

Layout (left to right):

* **Left** — a :class:`~textual.widgets.Tree` of the whole vault's root and
  branch Things (every Thing that has children), nested by parentage; leaf
  Things (no children) are omitted. The selected Thing — always a root or a
  branch — is highlighted here.
* **Centre** — a :class:`~textual.widgets.Tree` of the selected Thing's
  descendants (this is where leaf Things are reached).
* **Right** — a container with id ``detail`` holding the
  :class:`~lot_textual_ui.detail.DetailPane` (see :ref:`detail-seam` below),
  which renders the selected Thing's computed state and update thread.

Two reactive attributes model the selection, one per navigable column:

* :attr:`LotTextualApp.selected_id` is the **left** column's selection — the
  root or branch Thing the left tree highlights, and which roots the centre
  tree. The item under the *left* cursor assigns it: moving the cursor (or
  clicking) selects, no separate confirm keypress needed.
* :attr:`LotTextualApp.active_id` is the **centre** column's active item — the
  Thing shown in the right column. It resets to :attr:`selected_id` whenever the
  left selection changes, but the item under the *centre* cursor moves only
  ``active_id`` (the left column is left untouched). So each column keeps its own
  active item, and drilling into a descendant in the centre never resets the
  left column.

Selection follows the cursor in both trees (see
:meth:`LotTextualApp.on_tree_node_highlighted`); Enter/click still work via
:meth:`LotTextualApp.on_tree_node_selected`.

:meth:`LotTextualApp.watch_selected_id` re-derives the left and centre trees;
:meth:`LotTextualApp.watch_active_id` highlights the active centre node (the
detail pane watches ``active_id`` itself). The root/branch skeleton and the
descendants are all computed from the nested tree returned by ``lot thing
list`` — no extra CLI round-trips.

.. _detail-seam:

Hooking up the detail pane
--------------------------

The detail pane (:class:`~lot_textual_ui.detail.DetailPane`) is mounted inside
the ``#detail`` container in :meth:`LotTextualApp.compose`. It stays decoupled
from the trees: rather than being pushed to, it watches the app's reactive in
its own ``on_mount``::

    self.watch(self.app, "selected_id", self._on_selected_id_changed)

Textual's :meth:`~textual.dom.DOMNode.watch` can watch a reactive on any node,
so selection propagates without the shell knowing about the pane. The pane
loads each Thing's state/updates through the app's shared
:class:`~lot_textual_ui.lot_cli.LotCli` instance.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import events, work
from textual.app import App, ComposeResult
from textual.binding import BindingsMap
from textual.containers import Container, Horizontal
from textual.dom import DOMNode
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Footer, Header, Tree
from textual.widgets.tree import TreeNode

from .batch import TOP_LEVEL, ConfirmScreen, ThingPickerScreen
from .command_nav import RESERVED_CTRL_LETTERS, CommandNav, CommandNavScreen
from .detail import DetailPane, UpdateItem
from .forms import BatchUpdateScreen, NewThingScreen, NewUpdateScreen
from .keys import ACTION_BINDINGS, apply_overrides
from .lot_cli import LotCli, LotError
from .models import (
    EffectiveConfig,
    Thing,
    UpdateType,
    WatchEvent,
    creatable_update_types,
)
from .palette import PALETTE_PROVIDERS, LeafCommand
from .vault_picker import VaultPickerScreen
from .webmode import is_web_mode
from .wrapping_tree import WrappingTree

# A distinct colour per status, so the tree conveys state at a glance.
STATUS_COLORS = {
    "note": "blue",
    "work": "yellow",
    "info": "green",
    "done": "grey50",
}
# Fallback colour for any status not in the table above.
UNKNOWN_STATUS_COLOR = "magenta"

# No default theme of our own: when config sets none we leave Textual's built-in
# default in place so the user's chosen Textual colourscheme is respected. Users
# can still switch theme at runtime via the palette's "Switch theme" command.


# The glyph shown in front of a multi-select-marked row. A named constant so
# the marked-row indicator is one obvious thing to restyle (and for tests).
MARK_INDICATOR = "●"

# The copy-confirmation toast in web mode. The app can only *send* the text to
# the browser (via OSC 52 through textual-serve); whether the browser actually
# writes its clipboard depends on the page being secure (localhost/HTTPS) — the
# app cannot observe the outcome, so the wording promises only the handoff.
WEB_COPY_NOTICE = (
    "Sent {text} to the browser clipboard — the browser may block the write "
    "unless the page is on localhost or HTTPS."
)


def node_label(thing: Thing, marked: bool = False) -> Text:
    """Render a Thing as a tree label: a colour-coded status name plus its name.

    The status is spelled out (e.g. ``work``) rather than shown as a glyph, and
    padded to a fixed width so the Thing names line up in the tree. A leading
    two-cell column carries the multi-select :data:`MARK_INDICATOR` when the
    Thing is ``marked`` (and stays blank otherwise, so marked and unmarked rows
    keep their columns aligned).
    """
    status = thing.status or "?"
    color = STATUS_COLORS.get(thing.status, UNKNOWN_STATUS_COLOR)
    label = Text()
    label.append(f"{MARK_INDICATOR} " if marked else "  ", style="bold cyan")
    label.append(f"{status:<4}", style=color)
    label.append(f"  {thing.name}")
    return label


def label_name_offset(thing: Thing) -> int:
    """Cells before the name in :func:`node_label`'s label.

    The name is preceded by the two-cell mark column, the (min-four-wide) status
    column, and a two-space gutter; :class:`~lot_textual_ui.wrapping_tree.WrappingTree`
    uses this so a wrapped name lines up under itself in its own column rather
    than under the status. Kept in sync with :func:`node_label` by construction.
    """
    status = thing.status or "?"
    return 2 + len(f"{status:<4}") + 2


class LotTextualApp(App[None]):
    """The LoT Textual application shell.

    Args:
        lot_cli: The vault adapter. Defaults to a real :class:`LotCli`; tests
            inject a fake exposing an async ``thing_list()`` so no vault is
            required.
    """

    TITLE = "LoT"
    SUB_TITLE = "Lists of Things"

    CSS = """
    #columns {
        height: 1fr;
    }

    /* All three columns share one theme-derived background. Without this the
       three diverge: the two Trees default to $surface while the #detail
       container falls through to the darker screen $background, and Textual's
       Tree:focus adds a `background-tint` that lightens whichever tree has
       focus. The overrides below keep every column at $surface regardless of
       focus. */
    #left-tree, #centre-tree, #detail {
        width: 1fr;
        background: $surface;
    }

    #left-tree, #centre-tree {
        border-right: solid $panel-lighten-2;
    }

    #left-tree:focus, #centre-tree:focus {
        background-tint: $surface 0%;
    }

    #detail {
        padding: 1;
    }
    """

    # Every app-level key comes from the one central table (see
    # :mod:`lot_textual_ui.keys`), which is the single seam Phase 5's
    # keybinding overrides will target. Do not add ``Binding``\s here or on
    # child widgets; add them to ``ACTION_BINDINGS`` instead.
    BINDINGS = ACTION_BINDINGS

    # The command palette (``ctrl+p``) draws from these providers on top of
    # Textual's default system commands: the dynamically-discovered ``lot``
    # command tree and the TUI's own internal commands. See
    # :mod:`lot_textual_ui.palette` (and its forms seam).
    COMMANDS = App.COMMANDS | set(PALETTE_PROVIDERS)

    # The left column's selection: the id of the Thing whose siblings the left
    # tree shows and which roots the centre tree. ``init=False`` keeps the
    # watcher from firing for the initial ``None`` before the vault is loaded;
    # ``on_mount`` assigns the first real selection.
    selected_id: reactive[str | None] = reactive(None, init=False)

    # The centre column's active item: the id of the Thing shown in the right
    # (detail) column. Reset to ``selected_id`` on every left-selection change
    # (see :meth:`watch_selected_id`), then moved independently by selecting a
    # node in the centre tree. The detail-pane work item watches this (see module
    # docstring). ``init=False`` mirrors ``selected_id`` so the watcher stays
    # quiet until the first real selection cascades into it.
    active_id: reactive[str | None] = reactive(None, init=False)

    def __init__(self, lot_cli: LotCli | None = None) -> None:
        super().__init__()
        self._lot_cli = lot_cli if lot_cli is not None else LotCli()
        # The merged effective config from `lot settings get`, loaded on mount.
        # Defaults to an empty config so it is always a valid model even before
        # (or if) the CLI load fails. See :meth:`_apply_config`.
        self._config = EffectiveConfig()
        # Indexes over the whole vault, built once on load.
        self._by_id: dict[str, Thing] = {}
        self._parent_of: dict[str, Thing | None] = {}
        self._roots: list[Thing] = []
        # The vault currently targeted, tracked so a *failed* switch can revert
        # the shared adapter to it. Seeded from ``config.vault_path`` on mount and
        # updated on every successful switch (see :meth:`action_switch_vault`).
        self._active_vault_path: str = ""
        # The `lot help --format=yaml` tree, discovered lazily the first time
        # the command navigator opens and cached (see :meth:`_open_command_nav`).
        self._help_tree: dict | None = None
        # Set while a left-tree *cursor* move drives the selection, so
        # :meth:`watch_selected_id` skips the left-tree rebuild that would yank
        # the cursor back to the top. See :meth:`_select_node`.
        self._suppress_left_rebuild = False
        # The multi-select mark set: ids of the Things batch operations act on.
        # Kept consistent with the index — ids that leave ``_by_id`` (archive,
        # deletion, a vault switch) are pruned so a mark can never point at a
        # Thing that no longer exists. See the "multi-select marks" section.
        self._marked: set[str] = set()
        # Guards the theme-persistence watcher (see :meth:`on_mount`) against
        # *programmatic* theme changes: applying the configured theme on launch,
        # or a theme carried by a vault switched into, sets it via
        # :meth:`_apply_theme`, which raises this flag so the watcher does not
        # write that value straight back to config. Only a deliberate runtime
        # pick (the palette's "Switch theme") persists. See :meth:`_persist_theme`.
        self._suppress_theme_persist = False

    # --- composition -------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="columns"):
            yield WrappingTree("LoT", id="left-tree")
            yield WrappingTree("Descendants", id="centre-tree")
            with Container(id="detail"):
                yield DetailPane(self._lot_cli)
        yield Footer()

    async def on_mount(self) -> None:
        """Load config + the vault, select an initial Thing, focus the left tree."""
        # Clicking (or selecting) a branch must only *select* it, never fold
        # it. Textual's Tree otherwise toggles a branch on every select (its
        # ``auto_expand`` default), so turn that off on both trees. The trees
        # draw no fold arrows (see WrappingTree) and every node is added
        # expanded, so both columns read as fixed, fully-expanded outlines.
        for tree_id in ("#left-tree", "#centre-tree"):
            self.query_one(tree_id, Tree).auto_expand = False
        # Config first, so the configured theme is applied before the first paint.
        await self._apply_config()
        # Only now — after the *configured* theme has been applied — start
        # watching ``theme`` for changes to persist. Attaching it here (rather
        # than overriding Textual's own ``watch_theme``) means the mount-time
        # application above never triggers a write-back; subsequent runtime picks
        # do (guarded by ``_suppress_theme_persist`` for vault-switch reapplies).
        self.watch(self, "theme", self._on_theme_changed, init=False)
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        # Initial selection: the first top-level Thing, if any.
        self.selected_id = self._roots[0].id if self._roots else None
        # Start focus in the left column so vim motions have a deterministic
        # home; ``h``/``l`` walk focus from here across the columns.
        self.query_one("#left-tree", Tree).focus()
        # Baseline is loaded; now apply external changes live off `lot watch`.
        self._watch_vault()

    # --- config & theme ----------------------------------------------------
    #
    # Config is read *only* through the CLI (``lot settings get`` via
    # :meth:`LotCli.config_get`) — the TUI never reads config files itself. The
    # whole merged config is parsed into :class:`EffectiveConfig` and kept on the
    # app; downstream Phase 5 work items (keybinding overrides, vault switching)
    # read it via :attr:`config`. This work item wires only the theme.

    @property
    def config(self) -> EffectiveConfig:
        """The merged effective config loaded from ``lot settings get`` on mount.

        Exposed for the keybinding-override and vault-switching work items, which
        read :attr:`EffectiveConfig.keybindings` / :attr:`EffectiveConfig.vaults`
        from here rather than shelling out to ``lot`` themselves.
        """
        return self._config

    def creatable_update_types(self) -> list[UpdateType]:
        """The update types the Update forms offer, from the loaded config.

        The effective set (built-ins plus config-defined custom types) comes
        from ``lot settings get``'s ``update-types`` key
        (:attr:`EffectiveConfig.update_types`), filtered to the creatable ones
        (the built-in ``note`` is written by ``lot thing new``, never by ``lot
        update``). **Caching:** the set is read from the config the app already
        holds — no extra ``lot`` call per form open — and that config is
        (re)loaded on mount and on every vault switch (see
        :meth:`_apply_config` / :meth:`action_switch_vault`), so a vault's own
        custom types appear as soon as the app points at it. A mid-session
        config-file edit needs a vault re-switch (or restart) to show up, like
        every other config key.
        """
        return creatable_update_types(self._config.update_types)

    async def _apply_config(self) -> None:
        """Load config via the CLI and apply the configured theme, if any.

        Config is best-effort: a failed ``lot settings get`` (e.g. an older ``lot``
        binary predating the ``config`` subcommand) is swallowed so the browser
        still runs on defaults. On success the whole config is stored (for the
        keybinding/vault work items) and its :attr:`~EffectiveConfig.theme`, when
        set, is applied via :meth:`_apply_theme`.
        """
        try:
            config = await self._lot_cli.config_get()
        except LotError:
            # No config to read (e.g. an older `lot` without `settings get`);
            # leave Textual's built-in default theme in place.
            return
        self._config = config
        # Track the resolved active vault so a failed switch can revert to it,
        # and surface it in the header.
        if config.vault_path:
            self._active_vault_path = config.vault_path
        self._update_vault_subtitle()
        self._apply_theme(config.theme)
        self._apply_keybindings(config.keybindings)

    def _update_vault_subtitle(self) -> None:
        """Reflect the active vault in the header subtitle (light-touch indicator).

        Shows the active vault's configured
        :attr:`~lot_textual_ui.models.VaultEntry.name` when it has one, else its
        path; falls back to the app's default subtitle when no vault is known
        (e.g. an older ``lot`` without ``settings get``).
        """
        path = self._config.vault_path
        label: str | None = None
        if path:
            for entry in self._config.vaults:
                if entry.path == path and entry.name:
                    label = entry.name
                    break
            else:
                label = path
        self.sub_title = label if label is not None else type(self).SUB_TITLE

    def _apply_keybindings(self, overrides: dict[str, str]) -> None:
        """Rebuild the app's active bindings with the configured key overrides.

        Textual reads ``BINDINGS`` at *class* definition and freezes each node's
        merged bindings when it is constructed, but config is only known after
        the async load in :meth:`_apply_config` (on mount). So this rebuilds the
        app instance's merged :class:`~textual.binding.BindingsMap` from the
        class MRO — exactly as :meth:`DOMNode._merge_bindings` does — but with the
        central :data:`~lot_textual_ui.keys.ACTION_BINDINGS` table replaced by
        :func:`~lot_textual_ui.keys.apply_overrides`'s rewritten copy. Rebuilding
        from the MRO (rather than replacing ``self._bindings`` outright)
        preserves the bindings Textual itself contributes — notably the built-in
        ``ctrl+q`` quit and ``ctrl+c`` — which are *not* part of our table and so
        are never remapped; only the app's own keys move. An empty ``overrides``
        (the default) is a no-op, leaving the mount-time bindings untouched.
        Finally :meth:`refresh_bindings` repaints the footer so its hints show
        the new keys.
        """
        if not overrides:
            return
        overridden = apply_overrides(ACTION_BINDINGS, overrides)
        merged: dict[str, list] = {}
        for base in reversed(type(self).__mro__):
            if not (isinstance(base, type) and issubclass(base, DOMNode)):
                continue
            if not base._inherit_bindings:
                merged.clear()
            own = base.__dict__.get("BINDINGS", [])
            source = overridden if own is ACTION_BINDINGS else own
            for key, key_bindings in BindingsMap(source).key_to_bindings.items():
                merged[key] = key_bindings
        self._bindings = BindingsMap.from_keys(merged)
        self.refresh_bindings()

    def _apply_theme(self, theme: str | None) -> None:
        """Apply the configured theme by name, if one is set.

        ``theme`` is the config's value: ``None`` (unset) is a no-op — Textual's
        built-in default colourscheme is left untouched, so the user's default is
        respected. A name in :attr:`App.available_themes` (Textual's built-ins
        plus any registered theme) is applied by assigning the reactive
        :attr:`App.theme`. An unknown name notifies a warning and leaves the
        current theme in place rather than crashing.

        The assignment is wrapped in :attr:`_suppress_theme_persist` so the
        theme-persistence watcher (see :meth:`on_mount`) treats a *configured*
        theme — applied on launch and re-applied on every vault switch — as
        already-persisted and does not write it straight back to config. Only a
        runtime pick through the palette goes unguarded and persists.
        """
        if theme is None:
            return
        if theme in self.available_themes:
            self._suppress_theme_persist = True
            try:
                self.theme = theme
            finally:
                self._suppress_theme_persist = False
        else:
            self.notify(
                f"Unknown theme {theme!r} in config; keeping {self.theme!r}.",
                title="Theme",
                severity="warning",
            )

    def _on_theme_changed(self, theme: str) -> None:
        """Persist a runtime theme pick to config (the theme-reactive watcher).

        Attached to the app's ``theme`` reactive in :meth:`on_mount`, so it fires
        whenever the theme changes — most importantly when the user picks one in
        the palette's "Switch theme". Programmatic applications from config
        (launch and vault switches) raise :attr:`_suppress_theme_persist` around
        their assignment (see :meth:`_apply_theme`) and are skipped here, so only
        a deliberate pick is written back. The write runs in a worker (see
        :meth:`_persist_theme`) since it shells out to ``lot``.
        """
        if self._suppress_theme_persist:
            return
        self._persist_theme(theme)

    @work(exclusive=True, group="persist-theme")
    async def _persist_theme(self, theme: str) -> None:
        """Write the chosen theme to the user config via ``lot settings set``.

        Runs ``lot settings set theme <name>`` through the shared adapter (see
        :meth:`LotCli.settings_set_theme`) so the runtime pick survives a
        restart. Persistence is best-effort: the live theme change already
        stands, so a failure (e.g. an older ``lot`` without ``settings set``)
        only warns rather than undoing the switch.
        """
        try:
            await self._lot_cli.settings_set_theme(theme)
        except LotError as error:
            self.notify(
                f"Theme changed to {theme!r} for this session, but saving it to "
                f"config failed: {error}",
                title="Theme not saved",
                severity="warning",
            )

    def action_switch_theme(self) -> None:
        """Open Textual's theme picker to switch theme at runtime (palette entry).

        Reuses Textual's built-in theme search palette (:meth:`App.search_themes`,
        the same one behind the default *Change theme* system command), listing
        every registered theme for fuzzy selection. The chosen theme applies
        live *and* is persisted to the user config: picking one assigns
        :attr:`App.theme`, which the watcher installed in :meth:`on_mount` writes
        back through ``lot settings set theme`` (see :meth:`_on_theme_changed`),
        so the choice survives a restart.
        """
        self.search_themes()

    # --- vault switching ---------------------------------------------------
    #
    # The TUI can be pointed at any of the vaults declared in config
    # (``[[tui.vaults]]``, surfaced by ``lot settings get`` as
    # :attr:`EffectiveConfig.vaults`). Switching retargets the *one* shared
    # :class:`LotCli` at the new vault's ``LOT_VAULT_PATH`` (see
    # :meth:`LotCli.set_vault_path`) and reloads everything against it — the whole
    # UI (app, detail pane, palette providers) shares that instance, so pointing
    # it at the new vault re-homes them all at once.

    def action_switch_vault_picker(self) -> None:
        """Open the vault picker to switch vault at runtime (palette command).

        Presents the configured :attr:`EffectiveConfig.vaults` in
        :class:`~lot_textual_ui.vault_picker.VaultPickerScreen`; the chosen vault
        path is handed to :meth:`action_switch_vault` via :meth:`_vault_chosen`.
        With no vaults configured there is nothing to switch to, so it notifies
        the user (they add vaults under ``[[tui.vaults]]`` in config) rather than
        opening an empty picker.
        """
        vaults = self._config.vaults
        if not vaults:
            self.notify(
                "No vaults configured. Add them under [[tui.vaults]] in your "
                "config to switch between them.",
                title="Switch vault",
                severity="warning",
            )
            return
        self.push_screen(
            VaultPickerScreen(vaults, active_path=self._config.vault_path),
            self._vault_chosen,
        )

    def _vault_chosen(self, path: str | None) -> None:
        """Handle the picker's dismiss value: switch to ``path`` unless cancelled."""
        if path is None:
            return
        self.action_switch_vault(path)

    @work(exclusive=True, group="switch-vault")
    async def action_switch_vault(self, path: str) -> None:
        """Retarget the whole app at the vault at ``path`` and reload it live.

        The switch, in order:

        1. Cancel the running ``lot watch`` worker — it streams from the *old*
           vault, and a late event would corrupt the freshly-loaded index.
        2. Retarget the shared :class:`LotCli` (:meth:`LotCli.set_vault_path`), so
           every subsequent ``lot`` call — the detail pane's and the palette
           providers' included — resolves the new vault.
        3. Reload the tree from the new vault, rebuild the index, and select the
           first root (mirroring the initial mount load), repainting all columns.
        4. Re-read config and re-apply theme/keybindings (the new vault may carry
           its own ``[tui]`` config), and refresh the header/active-vault marker.
        5. Restart the ``lot watch`` worker against the new vault.

        It is **robust**: if the new vault path is invalid or ``lot thing list``
        fails, the adapter is reverted to the current vault, an error is toasted,
        the watcher is restarted against the old vault, and the UI is left exactly
        as it was — never half-switched. Switching to the already-active vault is
        a harmless reload.
        """
        previous = self._active_vault_path
        # Stop the old-vault watcher before retargeting so its in-flight events
        # cannot patch the new vault's index.
        self.workers.cancel_group(self, "watch")
        self._lot_cli.set_vault_path(path)
        try:
            listing = await self._lot_cli.thing_list()
        except LotError as error:
            # Bad vault: put the adapter back and resume as if nothing happened.
            if previous:
                self._lot_cli.set_vault_path(previous)
            self.notify(
                f"Could not switch vault: {error}",
                title="Switch vault",
                severity="error",
            )
            self._watch_vault()
            return

        self._active_vault_path = path
        # Drop the cached `lot help` tree: the new vault's config may define
        # its own custom update types, which `lot help --format=yaml` grafts
        # onto the `update` subtree, so the command navigator must re-discover.
        # (The fuzzy palette re-fetches help on every open and needs no cache
        # bust; the update *forms* read `config.update_types`, re-read below.)
        self._help_tree = None
        self._reindex(listing.things)
        new_selection = self._roots[0].id if self._roots else None
        # Assigning fires the reactive only when the id changes; the index is
        # wholesale different, so repaint unconditionally (covers a same-id root).
        self.selected_id = new_selection
        self._rebuild_left_tree(new_selection)
        self._rebuild_centre_tree(new_selection)
        # Re-home the centre's active item on the new root too, then reload the
        # detail pane (unconditionally, for a same-id root the reactives skip).
        self.active_id = new_selection
        self.query_one(DetailPane).reload()
        self.query_one("#left-tree", Tree).focus()
        # The new vault may carry its own theme/keybindings/vaults list; re-read
        # so the switch list stays populated and the theme follows.
        await self._apply_config()
        self.notify(f"Switched to {self._active_vault_label()}.", title="Vault")
        # Baseline is loaded; watch the new vault for live changes.
        self._watch_vault()

    def _active_vault_label(self) -> str:
        """A human label for the active vault: its config name, else its path."""
        path = self._active_vault_path or self._config.vault_path
        for entry in self._config.vaults:
            if entry.path == path and entry.name:
                return entry.name
        return path or "the vault"

    # --- live updates ------------------------------------------------------
    #
    # ``lot watch`` (readme §5.6) streams one minimal, incremental event per
    # settled vault change. We load the baseline above with ``thing_list()`` —
    # the watcher emits no initial-state event — then patch a *single* node of
    # the in-memory index per event so edits from the CLI, Claude sessions, or
    # git appear without a restart. All subprocess/parsing lives in ``LotCli``;
    # the app only folds already-typed events into its in-memory state. Only a
    # ``reload`` event (the rare no-single-Thing fallback) reloads the whole
    # baseline.

    @work(exclusive=False, group="watch")
    async def _watch_vault(self) -> None:
        """Consume the watch stream, applying each event to the UI.

        Runs as a long-lived background worker. Textual cancels it on app exit,
        which unwinds :meth:`LotCli.watch` and terminates the ``lot watch``
        subprocess (no orphan is left). A failed/absent ``lot watch`` is
        swallowed so the browser still works statically.
        """
        try:
            async for event in self._lot_cli.watch():
                await self._apply_event(event)
        except LotError:
            pass

    async def _apply_event(self, event: WatchEvent) -> None:
        """Patch one watch event into the in-memory index and refresh columns.

        The index is mutated incrementally rather than rebuilt: a created /
        modified event upserts one node (id + name + status + parent), a deleted
        event drops that id and its descendants, and the rare id-less ``reload``
        event reloads the full ``thing_list()`` baseline. The selection is
        tracked by id and preserved; if the selected Thing is gone it falls back
        to its old parent, then to the first root, then to nothing. Only what
        changed is repainted: when the selection id is unchanged the reactive
        watcher would not fire, so the trees are rebuilt explicitly
        (names/statuses/structure may have moved), and the detail pane is
        reloaded only when the changed Thing *is* the selection — so an unrelated
        event never disturbs its scroll position.
        """
        previous = self.selected_id
        old_parent = self._parent_of.get(previous) if previous is not None else None
        old_parent_id = old_parent.id if old_parent is not None else None

        if event.kind == "deleted":
            if event.id is not None:
                self._remove_subtree(event.id)
            # A deletion never reloads the detail pane in place: if the selected
            # Thing was the one deleted, the selection changes and the reactive
            # path reloads it instead.
            self._refresh_after(previous, old_parent_id, changed_id=None)
        elif event.kind == "reload" or event.id is None:
            # Fallback: a batch that maps to no single Thing. Reload the whole
            # baseline — the one case a full refresh is acceptable (and rare).
            listing = await self._lot_cli.thing_list()
            self._reindex(listing.things)
            self._refresh_after(previous, old_parent_id, changed_id=None)
        else:
            self._upsert_node(
                event.id, event.name or "", event.status or "", event.parent
            )
            self._refresh_after(previous, old_parent_id, changed_id=event.id)

    def _refresh_after(
        self, previous: str | None, old_parent_id: str | None, changed_id: str | None
    ) -> None:
        """Re-resolve both selections and repaint the minimum after an index patch.

        If the left selection id changed (its Thing was removed), assigning it
        fires ``watch_selected_id`` — which rebuilds both trees and resets the
        centre's active item to the new root, reloading the detail pane. Otherwise
        the left reactive stays quiet, so the trees are rebuilt in place and the
        centre's active item is re-resolved: it survives if its Thing is still
        present, else it falls back to the root. The detail pane is reloaded only
        when the active item moved, or when ``changed_id`` *is* the (unchanged)
        active item — so an unrelated event never disturbs its scroll position.
        """
        prev_active = self.active_id
        resolved = self._resolve_selection(previous, old_parent_id)
        if resolved != previous:
            self.selected_id = resolved
            return

        self._rebuild_left_tree(resolved)
        self._rebuild_centre_tree(resolved)

        resolved_active = (
            prev_active
            if prev_active is not None and prev_active in self._by_id
            else resolved
        )
        if resolved_active != prev_active:
            # Assigning fires watch_active_id (highlight) and the detail watcher.
            self.active_id = resolved_active
            return
        self._highlight_centre(resolved_active)
        if changed_id is not None and changed_id == resolved_active:
            self.query_one(DetailPane).reload()

    def _resolve_selection(
        self, previous: str | None, old_parent_id: str | None
    ) -> str | None:
        """Re-resolve the selection against the freshly rebuilt index."""
        if previous is not None and previous in self._by_id:
            return previous
        if old_parent_id is not None and old_parent_id in self._by_id:
            return old_parent_id
        return self._roots[0].id if self._roots else None

    # --- keyboard/mouse navigation -----------------------------------------
    #
    # These back the actions declared in the central binding table
    # (:mod:`lot_textual_ui.keys`). Each motion is pane-agnostic: it looks up
    # whichever column currently holds focus and does the right thing there —
    # move a Tree's cursor or scroll the detail pane. The mouse needs no code
    # here: Textual's Tree handles click-to-select and every pane
    # (both trees and the DetailPane's VerticalScroll) handles the wheel.

    def _focus_chain(self) -> list[Widget]:
        """The three columns, left to right, as the focus/drill order."""
        return [
            self.query_one("#left-tree", Tree),
            self.query_one("#centre-tree", Tree),
            self.query_one(DetailPane),
        ]

    def _focused_index(self) -> int:
        """Index into :meth:`_focus_chain` of the column that holds focus.

        Walks up from the actually-focused widget so a focused descendant of
        the detail pane still resolves to the pane. Defaults to the left
        column when nothing is focused.
        """
        chain = self._focus_chain()
        node: Widget | None = self.focused
        while node is not None:
            for index, column in enumerate(chain):
                if node is column:
                    return index
            node = node.parent if isinstance(node.parent, Widget) else None
        return 0

    def _nav_target(self) -> Widget:
        """The column vertical motions (``j``/``k``/``g``/``G``) act on."""
        return self._focus_chain()[self._focused_index()]

    def _move_focus(self, delta: int) -> None:
        chain = self._focus_chain()
        index = max(0, min(len(chain) - 1, self._focused_index() + delta))
        chain[index].focus()

    def action_focus_right(self) -> None:
        """Drill in: move focus one column to the right (clamped)."""
        self._move_focus(1)

    def action_focus_left(self) -> None:
        """Drill out: move focus one column to the left (clamped)."""
        self._move_focus(-1)

    def action_cursor_down(self) -> None:
        """Move down one row in the focused pane."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.action_cursor_down()
        else:
            target.scroll_down()

    def action_cursor_up(self) -> None:
        """Move up one row in the focused pane."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.action_cursor_up()
        else:
            target.scroll_up()

    def action_cursor_top(self) -> None:
        """Jump to the first row of the focused pane (vim ``g``)."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.move_cursor_to_line(0)
        else:
            target.scroll_home(animate=False)

    def action_cursor_bottom(self) -> None:
        """Jump to the last row of the focused pane (vim ``G``)."""
        target = self._nav_target()
        if isinstance(target, Tree):
            target.move_cursor_to_line(target.last_line)
        else:
            target.scroll_end(animate=False)

    # --- copy to clipboard -------------------------------------------------
    #
    # Four actions copy the current selection's identifiers to the system
    # clipboard via Textual's built-in OSC 52 `copy_to_clipboard` (works over
    # SSH / most terminals, no extra dependency). A URI is already in memory so
    # its copy is synchronous; a path comes from `lot thing path` / `lot update
    # path` through the shared `LotCli`, so those run in a worker. The "current
    # update" is resolved by the detail pane (whichever UpdateItem is focused,
    # else the Thing's latest update). A fifth action, `copy_selection`, copies
    # the free-form mouse text-selection (see `action_copy_selection`).
    #
    # Web mode: textual-serve relays the OSC 52 sequence to xterm.js in the
    # browser, whose clipboard addon hands it to `navigator.clipboard` — an API
    # that only exists on secure pages (http://localhost or HTTPS). Served over
    # plain HTTP on a LAN address the copy silently does nothing, and the app
    # has no way to observe either outcome, so the web toast (`WEB_COPY_NOTICE`)
    # says "sent to the browser" rather than over-promising "copied".

    def _copy(self, text: str, label: str) -> None:
        """Put ``text`` on the clipboard and confirm with a toast.

        The web-mode toast is honest about the handoff: the browser may block
        the write (see the section comment above), and the app cannot tell.
        """
        self.copy_to_clipboard(text)
        if is_web_mode():
            self.notify(WEB_COPY_NOTICE.format(text=text), title=label)
        else:
            self.notify(f"Copied {text} to clipboard", title=label)

    def action_copy_selection(self) -> None:
        """Copy the current mouse text-selection to the clipboard.

        Text selection itself is native to Textual (widgets default
        ``ALLOW_SELECT = True`` and the screen tracks a mouse-drag selection),
        and ``ctrl+c`` already copies it silently via the screen's own
        ``copy_text`` action. This action is the app's discoverable, *toasting*
        entry point for the same thing — bound to a key (see
        :mod:`lot_textual_ui.keys`) and offered in the palette. It reads the
        screen's selected text (spanning the detail pane's computed-state and
        update-body widgets); with nothing selected it notifies rather than
        clobbering the clipboard with an empty string.
        """
        selection = self.screen.get_selected_text()
        if not selection:
            self.notify(
                "Select some text first (drag with the mouse).",
                title="Nothing to copy",
                severity="warning",
            )
            return
        self._copy(selection, "Selection")

    def _current_update_id(self) -> str | None:
        """The update the copy-Update actions target (from the detail pane)."""
        return self.query_one(DetailPane).current_update_id

    def action_copy_thing_uri(self) -> None:
        """Copy the in-view Thing's ``lot:`` id to the clipboard."""
        thing_id = self.current_thing_id
        if thing_id is None:
            self.notify(
                "Select a Thing first.",
                title="Nothing to copy",
                severity="warning",
            )
            return
        self._copy(thing_id, "Thing URI")

    @work(exclusive=False, group="copy")
    async def action_copy_thing_path(self) -> None:
        """Copy the selected Thing's filesystem path to the clipboard.

        The path comes from ``lot thing path`` via :class:`LotCli`, so this runs
        in a worker; a failed lookup surfaces as an error toast.
        """
        thing_id = self.current_thing_id
        if thing_id is None:
            self.notify(
                "Select a Thing first.",
                title="Nothing to copy",
                severity="warning",
            )
            return
        try:
            path = await self._lot_cli.thing_path(thing_id)
        except LotError as error:
            self.notify(str(error), title="Copy failed", severity="error")
            return
        self._copy(path, "Thing path")

    def action_copy_update_uri(self) -> None:
        """Copy the focused/current Update's ``lot:`` id to the clipboard."""
        update_id = self._current_update_id()
        if update_id is None:
            self.notify(
                "No update to copy — select a Thing with updates.",
                title="Nothing to copy",
                severity="warning",
            )
            return
        self._copy(update_id, "Update URI")

    @work(exclusive=False, group="copy")
    async def action_copy_update_path(self) -> None:
        """Copy the focused/current Update's filesystem path to the clipboard.

        The path comes from ``lot update path`` via :class:`LotCli`, so this runs
        in a worker; a failed lookup surfaces as an error toast.
        """
        update_id = self._current_update_id()
        if update_id is None:
            self.notify(
                "No update to copy — select a Thing with updates.",
                title="Nothing to copy",
                severity="warning",
            )
            return
        try:
            path = await self._lot_cli.update_path(update_id)
        except LotError as error:
            self.notify(str(error), title="Copy failed", severity="error")
            return
        self._copy(path, "Update path")

    # --- expand / collapse updates -----------------------------------------
    #
    # Each UpdateItem in the detail thread can be collapsed to just its header.
    # The toggle key acts on whichever update is focused (UpdateItems join the
    # Tab order); the two palette commands collapse/expand the whole thread.

    def action_toggle_update(self) -> None:
        """Collapse/expand the focused update (or the current one as fallback).

        Bound to a dedicated key (see :mod:`lot_textual_ui.keys`). When an
        :class:`~lot_textual_ui.detail.UpdateItem` holds focus it is toggled;
        otherwise the pane's current (last-focused, else latest) update is, so
        the key still does something useful when focus sits on the pane itself.
        """
        focused = self.focused
        if isinstance(focused, UpdateItem):
            focused.toggle()
            return
        pane = self.query_one(DetailPane)
        current = pane.current_update_id
        if current is None:
            return
        for item in pane.query(UpdateItem):
            if item.update_id == current:
                item.toggle()
                return

    def action_collapse_all_updates(self) -> None:
        """Collapse every update in the thread to its header (palette command)."""
        self.query_one(DetailPane).set_all_collapsed(True)

    def action_expand_all_updates(self) -> None:
        """Expand every update in the thread to show its body (palette command)."""
        self.query_one(DetailPane).set_all_collapsed(False)

    # --- multi-select marks --------------------------------------------------
    #
    # Multi-select is a set of marked Thing ids (`_marked`) the batch actions
    # below operate on. Marking is per-Thing, not per-row: a Thing shown in
    # both tree columns is marked in both at once. Marked rows carry the
    # MARK_INDICATOR glyph (see `node_label`); labels are re-rendered in place
    # so toggling never rebuilds a tree (and so never disturbs its cursor).

    @property
    def marked_ids(self) -> frozenset[str]:
        """The ids of the currently marked Things (a read-only snapshot)."""
        return frozenset(self._marked)

    def _node_label(self, thing: Thing) -> Text:
        """A tree label for ``thing``, mark-aware (see :func:`node_label`)."""
        return node_label(thing, marked=thing.id in self._marked)

    def _cursor_thing_id(self) -> str | None:
        """The Thing the mark toggle targets: under the focused tree's cursor.

        With focus on either tree column this is the highlighted node's Thing;
        with focus elsewhere (the detail pane) it falls back to the in-view
        Thing (:attr:`current_thing_id`) so the key still does something
        sensible. ``None`` when there is nothing to target.
        """
        target = self._nav_target()
        if isinstance(target, Tree):
            node = target.cursor_node
            if node is not None and isinstance(node.data, str):
                return node.data
            return None
        return self.current_thing_id

    def action_toggle_mark(self) -> None:
        """Toggle the multi-select mark on the highlighted Thing."""
        thing_id = self._cursor_thing_id()
        if thing_id is None or thing_id not in self._by_id:
            self.notify(
                "Move the cursor onto a Thing first.",
                title="Nothing to mark",
                severity="warning",
            )
            return
        if thing_id in self._marked:
            self._marked.discard(thing_id)
        else:
            self._marked.add(thing_id)
        self._refresh_mark_indicators({thing_id})

    def action_clear_marks(self) -> None:
        """Drop every multi-select mark."""
        if not self._marked:
            return
        cleared = set(self._marked)
        self._marked.clear()
        self._refresh_mark_indicators(cleared)

    def _refresh_mark_indicators(self, ids: set[str] | None = None) -> None:
        """Re-render the labels of (the given) Things in both tree columns.

        ``ids=None`` refreshes every Thing-carrying node. Labels are set in
        place — no tree is rebuilt, so cursors and expansion are untouched.
        """
        for tree_id in ("#left-tree", "#centre-tree"):
            tree = self.query_one(tree_id, Tree)
            self._relabel(tree.root, ids)

    def _relabel(self, node: TreeNode[str], ids: set[str] | None) -> None:
        thing = self._by_id.get(node.data) if isinstance(node.data, str) else None
        if thing is not None and (ids is None or thing.id in ids):
            node.set_label(self._node_label(thing))
        for child in node.children:
            self._relabel(child, ids)

    def _prune_marks(self) -> None:
        """Drop marks whose Things are no longer in the index.

        Called from every index rebuild (:meth:`_reindex`) and single-node
        removal (:meth:`_remove_subtree`), so after an archive batch — or an
        external deletion arriving via ``lot watch``, or a vault switch — the
        mark set never references a vanished Thing.
        """
        self._marked &= set(self._by_id)

    # --- batch operations over the marked set --------------------------------
    #
    # Each batch action collects its remaining input through a modal (a
    # destination picker, a confirmation, the batch-update form), then runs
    # the per-item `lot` calls **sequentially** in one worker (`_run_batch`):
    # progress is shown in the header subtitle, a failed item never aborts the
    # rest, successes are unmarked as they land, and at the end the vault is
    # reloaded and a summary (with every failure's Thing and error) is shown.
    # Failed items stay marked so they can be retried after fixing the cause.

    def _marked_in_order(self) -> list[str]:
        """The marked ids in tree order (the index is built by a tree walk)."""
        return [thing_id for thing_id in self._by_id if thing_id in self._marked]

    def _require_marked(self, verb: str) -> list[str] | None:
        """The marked set for a batch action, or ``None`` (+ a hint) if empty."""
        ids = self._marked_in_order()
        if not ids:
            self.notify(
                f"Mark some Things first (press 'x' on them), then {verb}.",
                title="Nothing marked",
                severity="warning",
            )
            return None
        return ids

    def action_batch_move(self) -> None:
        """Move every marked Thing under a picked destination (or the root).

        Opens :class:`~lot_textual_ui.batch.ThingPickerScreen` over the whole
        vault tree plus a "Top level" entry. The marked Things themselves are
        excluded (a Thing cannot be its own destination); a destination inside
        one marked subtree is still offered, because it may be valid for the
        *other* marked Things — the CLI rejects the cyclic ones and those show
        up in the per-item failure report.
        """
        ids = self._require_marked("run Move marked Things")
        if ids is None:
            return
        self.push_screen(
            ThingPickerScreen(self._roots, exclude=set(ids)),
            self._move_target_chosen,
        )

    def _move_target_chosen(self, target: str | None) -> None:
        """Run the batch move to the picker's destination (``None`` = cancel)."""
        if target is None:
            return

        if target == TOP_LEVEL:

            async def move(thing_id: str) -> str:
                return await self._lot_cli.thing_move(thing_id, root=True)

        else:

            async def move(thing_id: str) -> str:
                return await self._lot_cli.thing_move(thing_id, parent=target)

        self._run_batch("Move", move, self._marked_in_order())

    def action_batch_archive(self) -> None:
        """Archive every marked Thing, after a count-confirming dialog.

        Archiving removes each Thing *and all its descendants* from the vault
        (history stays in git), so the confirmation states the count plainly.
        The CLI refuses to archive when ``vault.auto-commit`` is ``false``;
        that error text is surfaced per item like any other failure.
        """
        ids = self._require_marked("run Archive marked Things")
        if ids is None:
            return
        count = len(ids)
        plural = "s" if count != 1 else ""
        self.push_screen(
            ConfirmScreen(
                f"Archive {count} marked Thing{plural}? Each is removed from "
                "the vault together with all of its descendant Things "
                "(history is preserved in git).",
                title="Archive marked Things",
                confirm_label="Archive",
            ),
            self._archive_confirmed,
        )

    def _archive_confirmed(self, confirmed: bool | None) -> None:
        """Run the batch archive once the dialog confirms it."""
        if not confirmed:
            return
        self._run_batch("Archive", self._lot_cli.thing_archive, self._marked_in_order())

    def action_vault_archive(self) -> None:
        """Archive every done Thing in the vault, after a confirming dialog.

        Unlike the batch actions this needs no marks: it runs one
        ``lot vault archive`` (readme §5.4.2), which itself finds every Thing
        in a terminal status (``done``, or a custom update type with
        ``terminal = true``), commits them, and commits all their deletions in
        a single commit. The CLI refuses when ``vault.auto-commit`` is
        ``false``; that error text is surfaced in the failure toast.
        """
        self.push_screen(
            ConfirmScreen(
                "Archive every done Thing in the vault? Each Thing in a "
                "terminal status (done, or a custom terminal type) is removed "
                "together with all of its descendant Things "
                "(history is preserved in git).",
                title="Archive done Things",
                confirm_label="Archive",
            ),
            self._vault_archive_confirmed,
        )

    def _vault_archive_confirmed(self, confirmed: bool | None) -> None:
        """Run the vault-wide archive once the dialog confirms it."""
        if not confirmed:
            return
        self._run_vault_archive()

    @work(exclusive=True, group="batch")
    async def _run_vault_archive(self) -> None:
        """Run ``lot vault archive``, then reload the vault and report.

        Shares the ``batch`` worker group (and its exclusivity) with
        :meth:`_run_batch`: a vault-wide archive is a mutation of the same
        kind, so it must never run concurrently with a batch. It is a single
        CLI call rather than a per-item loop — the CLI owns finding the done
        Things and making the one deletion commit — so failure reporting is a
        single toast carrying the CLI's error text.
        """
        self.sub_title = "Archive done Things…"
        try:
            archived = await self._lot_cli.vault_archive()
        except LotError as error:
            self._update_vault_subtitle()
            self.notify(
                str(error),
                title="Archive done Things",
                severity="error",
                timeout=12,
            )
            return

        await self._reload_vault()
        self._refresh_mark_indicators()
        self._update_vault_subtitle()

        if archived:
            plural = "s" if len(archived) != 1 else ""
            self.notify(
                f"Archived {len(archived)} done Thing{plural}.",
                title="Archive done Things",
            )
        else:
            self.notify(
                "No done Things to archive.",
                title="Archive done Things",
            )

    def action_batch_update(self) -> None:
        """Append one new Update to every marked Thing.

        Opens :class:`~lot_textual_ui.forms.BatchUpdateScreen` — the batch
        variant of the new-Update form — once; the collected type + body are
        then applied to each marked Thing in turn (e.g. mark a handful of
        finished tasks and record one ``done`` across all of them).
        """
        ids = self._require_marked("run Update marked Things")
        if ids is None:
            return
        self.push_screen(
            BatchUpdateScreen(len(ids), update_types=self.creatable_update_types()),
            self._batch_update_submitted,
        )

    def _batch_update_submitted(self, result: tuple[str, str | None] | None) -> None:
        """Apply the collected Update to every marked Thing (``None`` = cancel).

        The form dismisses with the validated ``(kind, body)`` pair — ``body``
        is ``None`` for a ``takes-body = false`` type — which maps straight
        onto :meth:`LotCli.add_update` for every kind, built-in or custom.
        """
        if result is None:
            return
        kind, body = result

        async def add_update(thing_id: str) -> str:
            return await self._lot_cli.add_update(kind, thing_id, body)

        self._run_batch("Update", add_update, self._marked_in_order())

    @work(exclusive=True, group="batch")
    async def _run_batch(
        self,
        label: str,
        operation: Callable[[str], Awaitable[str]],
        ids: list[str],
    ) -> None:
        """Run one batch operation sequentially with per-item error reporting.

        Items run strictly one after another (the vault is git-backed; parallel
        mutations would race its lock and commits). A failure is recorded —
        Thing name plus the CLI's error text — and the batch *continues*; the
        failed Thing keeps its mark so the batch can be re-run after fixing the
        cause, while each success is unmarked immediately. Progress is shown in
        the header subtitle. Afterwards the vault is reloaded wholesale (one
        coherent repaint rather than N incremental ``lot watch`` patches; the
        reload also re-resolves a selection whose Thing was archived away and
        prunes marks for vanished Things), the subtitle is restored, and a
        summary — every failure spelled out — is toasted.
        """
        total = len(ids)
        # Capture names up front: a moved/archived Thing may be gone from the
        # index by the time the failure report is rendered.
        names = {
            thing_id: (thing.name if (thing := self._by_id.get(thing_id)) else thing_id)
            for thing_id in ids
        }
        failures: list[tuple[str, str]] = []
        for index, thing_id in enumerate(ids, start=1):
            self.sub_title = f"{label}: {index}/{total}…"
            try:
                await operation(thing_id)
            except LotError as error:
                failures.append((names[thing_id], str(error)))
            else:
                self._marked.discard(thing_id)

        await self._reload_vault()
        self._refresh_mark_indicators()
        self._update_vault_subtitle()

        succeeded = total - len(failures)
        if failures:
            detail = "\n".join(f"• {name}: {message}" for name, message in failures)
            self.notify(
                f"{succeeded} of {total} succeeded; {len(failures)} failed "
                f"(still marked):\n{detail}",
                title=f"{label} marked Things",
                severity="error",
                timeout=12,
            )
        else:
            plural = "s" if total != 1 else ""
            self.notify(
                f"{label}: {total} Thing{plural} processed.",
                title=f"{label} marked Things",
            )

    # --- public API for sibling widgets (e.g. the detail pane) -------------

    @property
    def lot_cli(self) -> LotCli:
        """The shared vault adapter.

        Exposed so the command palette providers (see
        :mod:`lot_textual_ui.palette`) can discover the ``lot`` command tree
        through the *one* :class:`LotCli` instance instead of spawning ``lot``
        themselves.
        """
        return self._lot_cli

    def thing_by_id(self, thing_id: str | None) -> Thing | None:
        """Return the Thing with ``thing_id``, or ``None`` if unknown."""
        if thing_id is None:
            return None
        return self._by_id.get(thing_id)

    # --- command navigator (Space / Ctrl+letter) ----------------------------
    #
    # The hierarchical command selector (see :mod:`lot_textual_ui.command_nav`):
    # ``space`` opens it at the top level of the discovered ``lot`` command
    # tree, and ``ctrl+<first letter of a top-level command>`` opens it already
    # inside that command (``ctrl+t`` → ``lot thing``, then ``n`` runs
    # ``lot thing new``). A picked leaf runs through :meth:`run_lot_command`,
    # exactly like a fuzzy-palette pick.

    # App actions that only make sense on the base (browser) screen. Their
    # non-priority bindings would otherwise still fire while a modal is up
    # (whenever the modal's focused widget doesn't consume the key), so
    # ``check_action`` disables them there — a stray ``d`` in a picker must
    # not queue a batch archive behind the modal.
    _BASE_SCREEN_ACTIONS = frozenset(
        {
            "toggle_mark",
            "clear_marks",
            "batch_move",
            "batch_archive",
            "batch_update",
        }
    )

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Gate base-screen-only actions while any modal screen is on top.

        ``command_nav``'s binding is ``priority=True`` so it beats the focused
        :class:`~textual.widgets.Tree`'s own space-to-toggle — but priority
        bindings also fire while a modal (a form with text inputs, the
        navigator itself) is on top, where a typed space must stay a space. So
        the action is disabled whenever any screen is pushed. The multi-select
        and batch actions are gated the same way (see
        :data:`_BASE_SCREEN_ACTIONS`); every other action passes through
        untouched.
        """
        if len(self.screen_stack) > 1 and (
            action == "command_nav" or action in self._BASE_SCREEN_ACTIONS
        ):
            return False
        return super().check_action(action, parameters)

    def action_command_nav(self) -> None:
        """Open the command navigator at the top level (the ``space`` leader)."""
        self._open_command_nav(None)

    def on_key(self, event: events.Key) -> None:
        """Treat ``ctrl+<letter>`` as a shortcut into a top-level command.

        Handled here rather than as bindings because the shortcut set is
        derived at runtime from the discovered command tree's first letters.
        Only unclaimed keys reach this handler, and the reserved set
        (:data:`~lot_textual_ui.command_nav.RESERVED_CTRL_LETTERS`) keeps
        quit/palette/suspend combinations out; a letter matching no top-level
        command does nothing (see :meth:`_open_command_nav`).
        """
        key = event.key
        if not (key.startswith("ctrl+") and len(key) == 6 and key[5].isalpha()):
            return
        if key[5] in RESERVED_CTRL_LETTERS or len(self.screen_stack) > 1:
            return
        event.stop()
        self._open_command_nav(key[5])

    @work(exclusive=True, group="command-nav")
    async def _open_command_nav(self, letter: str | None) -> None:
        """Open the navigator, optionally pre-navigated by a shortcut letter.

        Discovers (and caches) the command tree first. A ``letter`` behaves
        exactly as if typed with the navigator open: a unique top-level match
        lands inside that command — a leaf runs immediately, without the
        navigator ever showing — a first-letter collision opens it with the
        chooser up, and a letter matching no top-level command does nothing.
        """
        if self._help_tree is None:
            try:
                self._help_tree = await self._lot_cli.help_yaml()
            except LotError as error:
                self.notify(str(error), title="Commands", severity="error")
                return
        nav = CommandNav(self._help_tree)
        if letter is not None:
            outcome = nav.on_letter(letter)
            if isinstance(outcome, LeafCommand):
                self.run_lot_command(outcome)
                return
            if not nav.path and nav.chooser is None:
                return
        self.push_screen(CommandNavScreen(nav), self._command_nav_done)

    def _command_nav_done(self, command: LeafCommand | None) -> None:
        """Run the navigator's pick (``None`` = cancelled) via the forms seam."""
        if command is not None:
            self.run_lot_command(command)

    # --- command palette ---------------------------------------------------
    #
    # The palette (``ctrl+p``) is Textual's native fuzzy palette, fed by the
    # providers in :mod:`lot_textual_ui.palette`. Two entry points land here:
    # picking a ``lot`` leaf command calls :meth:`run_lot_command`, and the
    # internal "Refresh vault" command calls :meth:`action_refresh_vault`.

    def run_lot_command(self, command: LeafCommand) -> None:
        """Run a ``lot`` leaf command chosen in the palette.

        .. _run-lot-command-seam:

        **Forms seam** (see :ref:`lot_textual_ui.palette <palette-forms-seam>`).
        A ``lot`` leaf command falls into one of two buckets:

        * **No input needed** (``command.needs_input`` is ``False`` — every
          argument is optional and defaulted): the command is run as-is through
          the shared :class:`LotCli` and the vault view is refreshed.
        * **Input needed** (a required positional, a value-taking flag, content
          on stdin, …): dispatch on ``command.path`` to the matching handler.
          ``("thing", "new")`` opens :meth:`open_new_thing_form`;
          ``("update", <type>)`` — for any *creatable* update type in the
          loaded config, custom types included (see
          :meth:`creatable_update_types`) — is **type-specific**: a
          body-taking type opens :meth:`open_new_update_form` fixed to it,
          while a bodyless type (``done``-likes) runs straight away on the
          in-view Thing via :meth:`add_bodyless_update`, no form at all;
          ``("claude", "send", <model>)`` launches a background Claude session
          on the in-view Thing via :meth:`send_to_claude` (its only argument,
          the Thing, is the one the user is looking at); other input-needing
          commands (e.g. ``update path``) still fall through to a placeholder
          toast until their own form work items land.
        """
        if command.needs_input:
            if command.path == ("thing", "new"):
                self.open_new_thing_form()
                return
            if command.path[:1] == ("update",) and len(command.path) == 2:
                update_type = next(
                    (
                        t
                        for t in self.creatable_update_types()
                        if t.name == command.path[1]
                    ),
                    None,
                )
                # Only real update types — `update path` is a leaf here too.
                if update_type is not None:
                    if update_type.takes_body:
                        self.open_new_update_form(kind=update_type.name)
                    else:
                        self.add_bodyless_update(update_type.name)
                    return
            if command.path[:2] == ("claude", "send") and len(command.path) == 3:
                self.send_to_claude(command.path[2])
                return
            self.notify(
                f"'lot {command.label}' needs input — a form for it is coming "
                "in a later phase.",
                title="Not available yet",
                severity="warning",
            )
            return
        self._run_leaf_command(command)

    def open_new_thing_form(
        self, parent_id: str | None = None, title: str = "New Thing"
    ) -> None:
        """Push the new-Thing form; on submit, select the created Thing.

        The reusable entry point for creating a Thing: the palette's ``thing
        new`` leaf calls it with no arguments (a top-level Thing), and the
        create-child-Things work item calls it with ``parent_id`` set (and a
        fitting ``title``) to seed the parent. The
        :class:`~lot_textual_ui.forms.NewThingScreen` dismisses with the new
        Thing's ``lot:`` id on success or ``None`` on cancel; the id is handled
        by :meth:`_new_thing_created`.
        """
        self.push_screen(
            NewThingScreen(parent_id=parent_id, title=title),
            self._new_thing_created,
        )

    def action_new_thing(self) -> None:
        """Create a new top-level Thing (keyboard/palette entry point).

        A thin wrapper over :meth:`open_new_thing_form` with no parent, giving
        the top-level create a first-class app action (bound in
        :mod:`lot_textual_ui.keys`) alongside the ``thing new`` palette leaf.
        """
        self.open_new_thing_form()

    def action_new_child_thing(self) -> None:
        """Create a new Thing as a child of the in-view Thing.

        Seeds :meth:`open_new_thing_form` with the in-view Thing's id (the centre
        column's active item) as the parent, so the created Thing lands under the
        Thing the user is looking at (and the reload path jumps the selection to
        the new child, which the centre column then shows). With nothing selected
        there is no parent to hang it under, so it notifies and does nothing
        rather than opening a form that would create a stray root.
        """
        parent_id = self.current_thing_id
        if parent_id is None:
            self.notify(
                "Select a Thing first to add a child to it.",
                title="No Thing selected",
                severity="warning",
            )
            return
        self.open_new_thing_form(parent_id=parent_id, title="New child Thing")

    @work(exclusive=False, group="new-thing-select")
    async def _new_thing_created(self, new_id: str | None) -> None:
        """Reload the vault and jump the view to a freshly created Thing.

        Called with the form's dismiss value. ``None`` means the form was
        cancelled — nothing to do. Otherwise the vault is reloaded first (the
        live ``lot watch`` stream would bring the node in eventually, but a
        reload avoids the race) and only then is the view moved, so the target id
        is already in the index. If the node is somehow still unknown the move is
        skipped rather than selecting a phantom id.

        A new top-level Thing is a root, so it becomes the left selection
        directly. A new child is a leaf, which the left tree does not show (only
        roots and branches); its parent — now a branch — becomes the left
        selection, rooting the centre column there, and the new child is made the
        centre's active item so it is highlighted and shown in the detail pane.
        """
        if new_id is None:
            return
        await self._reload_vault()
        if new_id not in self._by_id:
            return
        container = self._left_visible_id(new_id)
        # Assigning selected_id fires watch_selected_id (re-rooting the centre at
        # the container and resetting active_id); a same-id no-op leaves the
        # already-current centre in place. Either way, point the active item at
        # the new Thing so the centre highlights it and the detail pane shows it.
        self.selected_id = container
        self.active_id = new_id

    def open_new_update_form(
        self, kind: str = "work", thing_id: str | None = None
    ) -> None:
        """Push the type-fixed new-Update form; on submit, refresh the detail.

        The reusable entry point for adding a **body-taking** Update. Each
        ``update <type>`` leaf (palette or command navigator) — built-ins and
        custom types alike — calls it with its own ``kind`` and no
        ``thing_id``, so it defaults to the in-view Thing
        (:attr:`current_thing_id`, the centre column's active item) — "add an
        update" almost always means "to the Thing I'm looking at" on the
        right. Other flows may pass an explicit ``thing_id``. With no target
        available (nothing selected and no id given) it notifies and does
        nothing rather than opening a form that cannot submit. Bodyless types
        never come here — :meth:`add_bodyless_update` runs them without a
        form.

        The :class:`~lot_textual_ui.forms.NewUpdateScreen` dismisses with the new
        update's ``lot:`` id on success or ``None`` on cancel; the result is
        handled by :meth:`_update_created`.
        """
        target = thing_id if thing_id is not None else self.current_thing_id
        if target is None:
            self.notify(
                "Select a Thing first to add an update to it.",
                title="No Thing selected",
                severity="warning",
            )
            return
        thing = self.thing_by_id(target)
        self.push_screen(
            NewUpdateScreen(
                thing_id=target,
                thing_label=thing.name if thing is not None else target,
                kind=kind,
            ),
            self._update_created,
        )

    def add_bodyless_update(self, kind: str) -> None:
        """Append a bodyless Update (``done``-likes) to the in-view Thing.

        A bodyless type carries nothing but its marker, so there is no form to
        fill in: picking ``update done`` (palette, or ``ctrl+u`` ``d`` in the
        command navigator) — or any custom ``takes-body = false`` type — lands
        here and runs ``lot update <kind>`` straight away on the in-view Thing
        (:attr:`current_thing_id`). With nothing selected it notifies and does
        nothing.
        """
        target = self.current_thing_id
        if target is None:
            self.notify(
                "Select a Thing first to add an update to it.",
                title="No Thing selected",
                severity="warning",
            )
            return
        thing = self.thing_by_id(target)
        label = thing.name if thing is not None else target
        self._add_bodyless_update(kind, target, label)

    @work(exclusive=False, group="new-update-reload")
    async def _add_bodyless_update(self, kind: str, thing_id: str, label: str) -> None:
        """Run the bodyless ``lot update`` and refresh; toast either outcome.

        A success is toasted (there was no form, so the toast is the only
        feedback that the key press landed) and the vault reloaded so the
        Thing's status marker repaints; a failure surfaces the CLI's error.
        """
        try:
            await self._lot_cli.add_update(kind, thing_id, None)
        except LotError as error:
            self.notify(str(error), title="Could not add Update", severity="error")
            return
        self.notify(f"{kind} recorded on {label}.", title="Update added")
        await self._reload_vault()

    @work(exclusive=False, group="new-update-reload")
    async def _update_created(self, new_id: str | None) -> None:
        """Reload the vault so a freshly added Update shows in the detail pane.

        Called with the form's dismiss value. ``None`` means the form was
        cancelled — nothing to do. Otherwise the vault is reloaded: the Update
        landed on the selected Thing, so :meth:`_reload_vault` repaints the trees
        (its status marker may have changed, e.g. ``done``) and forces the detail
        pane to re-render the selected Thing's thread with the new Update. The
        live ``lot watch`` stream would deliver the change too, but reloading
        here avoids the race.
        """
        if new_id is None:
            return
        await self._reload_vault()

    # --- send to Claude ----------------------------------------------------
    #
    # The ``claude send <model>`` leaves (sonnet/opus/fable) launch a background
    # ``claude`` session working on a Thing via ``lot claude send`` (readme §5.3).
    # Their only argument is the Thing, so — like the update actions — they act
    # on the Thing the user is looking at (:attr:`current_thing_id`) rather than
    # opening a form; the id is passed explicitly so the CLI never falls back to
    # ``LOT_THING_ID``.

    def send_to_claude(self, model: str) -> None:
        """Send the in-view Thing to a background Claude session (palette/nav).

        Backs the ``claude send <model>`` command leaves. ``model`` is the model
        sub-command (``sonnet``/``opus``/``fable``). Targets the centre column's
        active item (:attr:`current_thing_id`) — "send this Thing" almost always
        means the one on the right — passing its id explicitly. With nothing
        selected there is no Thing to send, so it notifies and does nothing.
        """
        target = self.current_thing_id
        if target is None:
            self.notify(
                "Select a Thing first to send it to Claude.",
                title="No Thing selected",
                severity="warning",
            )
            return
        self._send_to_claude(model, target)

    @work(exclusive=False, group="claude-send")
    async def _send_to_claude(self, model: str, thing_id: str) -> None:
        """Run ``lot claude send`` in a worker, then reload so the launch shows.

        Kept off the event loop because ``lot claude send`` spawns the ``claude``
        CLI; a failure (e.g. ``claude`` not installed) surfaces as an error toast
        rather than crashing. On success the vault is reloaded so the ``work``
        update the CLI records for the launch appears in the detail pane without
        waiting for the live ``lot watch`` stream.
        """
        try:
            await self._lot_cli.claude_send(model, thing_id)
        except LotError as error:
            self.notify(str(error), title="Send to Claude failed", severity="error")
            return
        await self._reload_vault()
        self.notify(
            f"Launched a background Claude session (model: {model}).",
            title="Sent to Claude",
        )

    @work(exclusive=False, group="palette-run")
    async def _run_leaf_command(self, command: LeafCommand) -> None:
        """Run a no-input leaf command, then refresh the vault view.

        Kept in a background worker so the ``lot`` subprocess never blocks the
        event loop; failures surface as an error toast rather than crashing.
        """
        try:
            await self._lot_cli.run_command(*command.path)
        except LotError as error:
            self.notify(str(error), title="Command failed", severity="error")
            return
        await self._reload_vault()
        self.notify(f"Ran 'lot {command.label}'.")

    @work(exclusive=False, group="palette-run")
    async def action_refresh_vault(self) -> None:
        """Reload the whole vault from disk and repaint (palette "Refresh")."""
        await self._reload_vault()

    async def _reload_vault(self) -> None:
        """Re-read the full ``thing_list`` baseline and repaint, keeping selection.

        Mirrors the ``reload`` path in :meth:`_apply_event`: rebuild the index
        from a fresh listing, then re-resolve the selection and repaint the
        minimum (forcing a detail reload for the current selection so freshly
        written updates appear).
        """
        previous = self.selected_id
        old_parent = self._parent_of.get(previous) if previous is not None else None
        old_parent_id = old_parent.id if old_parent is not None else None
        listing = await self._lot_cli.thing_list()
        self._reindex(listing.things)
        self._refresh_after(previous, old_parent_id, changed_id=previous)

    # --- selection model ---------------------------------------------------

    @property
    def current_thing_id(self) -> str | None:
        """The Thing currently in view — the centre column's active item.

        This is what the right/detail column shows, so it is also what the
        Thing-scoped actions target (copy Thing URI/path, add update, add child):
        they act on the Thing the user is actually looking at, not the left
        column's root. Falls back to :attr:`selected_id` before any active item
        is set.
        """
        return self.active_id if self.active_id is not None else self.selected_id

    def watch_selected_id(self, old: str | None, new: str | None) -> None:
        """Re-derive the left and centre trees, and reset the centre's active item.

        A new left selection re-roots the centre column at ``new`` and makes it
        the centre's active item too, so the right column starts on the newly
        selected Thing. Assigning :attr:`active_id` fires
        :meth:`watch_active_id` (which highlights the centre node) and the detail
        pane's own watcher (which reloads it).

        The left tree is rebuilt unless the change came from the left tree's own
        cursor (:attr:`_suppress_left_rebuild`): the cursor already sits on the
        selected node, so rebuilding would only reset it to the top. The centre
        column is always re-rooted regardless.
        """
        if not self._suppress_left_rebuild:
            self._rebuild_left_tree(new)
        self._rebuild_centre_tree(new)
        self.active_id = new

    def watch_active_id(self, old: str | None, new: str | None) -> None:
        """Highlight the active item in the centre tree on change.

        The right column (:class:`~lot_textual_ui.detail.DetailPane`) refreshes
        itself by watching ``active_id`` directly, so it is not touched here.
        """
        self._highlight_centre(new)

    def on_tree_node_highlighted(self, event: Tree.NodeHighlighted[str]) -> None:
        """Follow the cursor: the item *under* it becomes the column's selection.

        Any cursor move — ``j``/``k``, ``g``/``G``, arrow keys, a click — emits
        ``NodeHighlighted``, so acting on it here selects the highlighted item
        without a separate confirm keypress (the behaviour Enter used to be
        required for). Routing matches :meth:`on_tree_node_selected`: a left-tree
        highlight moves the left selection (re-rooting the centre column), a
        centre-tree highlight moves only the centre's active item.

        This does not loop. Our own programmatic ``move_cursor`` calls — a tree
        rebuild's re-cursor and :meth:`_highlight_centre` — re-emit this for the
        node we just selected, so the resulting assignment writes the *same* id
        back to the reactive, which no-ops rather than firing another rebuild.
        """
        self._select_node(event.node)

    def on_tree_node_selected(self, event: Tree.NodeSelected[str]) -> None:
        """Route an explicit selection (Enter or click) to its column.

        Highlighting already follows the cursor (see
        :meth:`on_tree_node_highlighted`), so this now mostly reaffirms the
        current selection; it still matters when Enter/click lands without moving
        the cursor (re-selecting the already-highlighted node).
        """
        self._select_node(event.node)

    def _select_node(self, node: TreeNode[str]) -> None:
        """Assign the selection reactive that ``node``'s column owns.

        A node in the **left** tree moves the left selection (and re-roots the
        centre column); a node in the **centre** tree moves only the centre's
        active item, leaving the left column exactly where it is. Nodes with no
        Thing id (e.g. the left tree's ``LoT`` root) are ignored.

        The cursor already sits on ``node``, so a left selection suppresses
        :meth:`watch_selected_id`'s left-tree rebuild (which would reset the
        cursor to the top); only the centre column is re-rooted off the new
        selection.
        """
        thing_id = node.data
        if thing_id is None:
            return
        left_tree = self.query_one("#left-tree", Tree)
        if node.tree is left_tree:
            if thing_id == self.selected_id:
                return
            self._suppress_left_rebuild = True
            try:
                self.selected_id = thing_id
            finally:
                self._suppress_left_rebuild = False
        else:
            self.active_id = thing_id

    # --- derivation --------------------------------------------------------

    def _reindex(self, things: list[Thing]) -> None:
        """Build the id→Thing and id→parent indexes from the nested tree."""
        self._roots = things
        self._by_id = {}
        self._parent_of = {}

        def walk(items: list[Thing], parent: Thing | None) -> None:
            for thing in items:
                self._by_id[thing.id] = thing
                self._parent_of[thing.id] = parent
                walk(thing.children, thing)

        walk(things, None)
        # Marks follow the index: a Thing that vanished (archive, external
        # deletion, vault switch) can no longer be marked.
        self._prune_marks()

    def _upsert_node(
        self, thing_id: str, name: str, status: str, parent_id: str | None
    ) -> None:
        """Insert or update a single node, keeping every index consistent.

        A never-seen id creates a fresh (childless) :class:`Thing`, linked under
        its parent (or as a root). A known id updates its ``name``/``status`` in
        place — preserving its existing ``children`` so descendants survive — and
        is re-linked only if its parent actually moved. ``_by_id``,
        ``_parent_of`` and the ``children``/``_roots`` sibling lists are all kept
        in agreement so ``_rebuild_*`` and ``_left_visible_id`` stay correct.
        """
        existing = self._by_id.get(thing_id)
        if existing is None:
            node = Thing(id=thing_id, name=name, status=status, children=[])
            self._by_id[thing_id] = node
            self._link(node, parent_id)
            return

        existing.name = name
        existing.status = status
        current_parent = self._parent_of.get(thing_id)
        current_parent_id = current_parent.id if current_parent is not None else None
        if current_parent_id != parent_id:
            self._unlink(existing)
            self._link(existing, parent_id)

    def _remove_subtree(self, thing_id: str) -> None:
        """Drop a Thing and all its descendants from every index."""
        node = self._by_id.get(thing_id)
        if node is None:
            return
        for child in list(node.children):
            self._remove_subtree(child.id)
        self._unlink(node)
        self._by_id.pop(thing_id, None)
        self._parent_of.pop(thing_id, None)
        self._marked.discard(thing_id)

    def _link(self, node: Thing, parent_id: str | None) -> None:
        """Attach ``node`` under ``parent_id`` (or as a root), name-sorted."""
        parent = self._by_id.get(parent_id) if parent_id is not None else None
        self._parent_of[node.id] = parent
        siblings = parent.children if parent is not None else self._roots
        siblings.append(node)
        siblings.sort(key=lambda thing: thing.name)

    def _unlink(self, node: Thing) -> None:
        """Detach ``node`` from its parent's children (or the root list)."""
        parent = self._parent_of.get(node.id)
        siblings = parent.children if parent is not None else self._roots
        siblings[:] = [thing for thing in siblings if thing.id != node.id]

    def _left_visible_id(self, thing_id: str) -> str:
        """The nearest Thing shown in the left tree for ``thing_id``.

        The left tree holds only roots and branches (see
        :meth:`_rebuild_left_tree`), so a leaf never appears there. This returns
        ``thing_id`` itself when it is a root or a branch, else its parent's id —
        the parent is a branch (it has this Thing as a child), so it is always
        left-visible. Used to pick the left selection that *contains* a Thing
        (e.g. jumping to a freshly created leaf child, which the centre column
        then shows). Unknown ids are returned unchanged.
        """
        thing = self._by_id.get(thing_id)
        if thing is None:
            return thing_id
        parent = self._parent_of.get(thing_id)
        if parent is None or thing.children:
            return thing_id
        return parent.id

    # --- rendering ---------------------------------------------------------

    def _rebuild_left_tree(self, selected_id: str | None) -> None:
        """Rebuild the left tree: the whole vault's root and branch Things.

        Every root Thing and every branch (a Thing with children) is shown,
        nested by parentage; leaf Things (no children) are omitted — the centre
        column reaches them by rooting at their branch. The selected Thing is
        highlighted when present (it always is: a left selection is a root or a
        branch), which does not re-fire ``NodeSelected`` (``move_cursor`` emits
        only ``NodeHighlighted``, which we treat as a no-op for the same id).
        """
        tree = self.query_one("#left-tree", Tree)
        tree.clear()
        tree.root.expand()
        for root in self._roots:
            self._add_left_subtree(tree.root, root)
        if selected_id is not None:
            selected_node = self._find_node(tree.root, selected_id)
            if selected_node is not None:
                tree.move_cursor(selected_node)

    def _set_name_offset(self, node: TreeNode[str], thing: Thing) -> None:
        """Tell the tree how wide ``thing``'s fixed label columns are.

        So the :class:`~lot_textual_ui.wrapping_tree.WrappingTree` keeps the
        mark/status columns on the node's first row and wraps only the name
        under itself (see :func:`label_name_offset`). A no-op on a plain tree.
        """
        tree = node.tree
        if isinstance(tree, WrappingTree):
            tree.set_name_offset(node, label_name_offset(thing))

    def _add_left_subtree(self, parent_node: TreeNode[str], thing: Thing) -> None:
        """Add ``thing`` and its branch descendants to the left tree.

        Every root reaches here (so a childless root still shows); a non-root
        Thing is only reached when it is itself a branch. A Thing whose only
        children are leaves is added as a leaf node — it shows, but its leaf
        children do not — so the tree is the vault's root/branch skeleton.
        """
        branches = [child for child in thing.children if child.children]
        if branches:
            node = parent_node.add(self._node_label(thing), data=thing.id, expand=True)
            for branch in branches:
                self._add_left_subtree(node, branch)
        else:
            node = parent_node.add_leaf(self._node_label(thing), data=thing.id)
        self._set_name_offset(node, thing)

    def _rebuild_centre_tree(self, selected_id: str | None) -> None:
        tree = self.query_one("#centre-tree", Tree)
        tree.clear()
        selected = self.thing_by_id(selected_id)
        if selected is None:
            tree.root.set_label("Descendants")
            tree.root.data = None
            # The reused root object may still carry a previous selection's name
            # offset; the plain "Descendants" label has no fixed columns.
            if isinstance(tree, WrappingTree):
                tree.set_name_offset(tree.root, 0)
            return

        tree.root.set_label(self._node_label(selected))
        tree.root.data = selected.id
        self._set_name_offset(tree.root, selected)
        tree.root.expand()
        for child in selected.children:
            self._add_subtree(tree.root, child)

    def _highlight_centre(self, active_id: str | None) -> None:
        """Move the centre tree's cursor to the active node, if present.

        ``move_cursor`` emits ``NodeHighlighted`` (which we don't act on), not
        ``NodeSelected``, so highlighting the active item does not re-fire
        selection. A ``None`` id or an id not currently in the centre tree (e.g.
        the active item lives outside the rooted subtree) leaves the cursor as-is.
        """
        if active_id is None:
            return
        tree = self.query_one("#centre-tree", Tree)
        node = self._find_node(tree.root, active_id)
        if node is not None:
            tree.move_cursor(node)

    def _find_node(self, node: TreeNode[str], data: str) -> TreeNode[str] | None:
        """Depth-first search for the node carrying ``data`` under ``node``."""
        if node.data == data:
            return node
        for child in node.children:
            found = self._find_node(child, data)
            if found is not None:
                return found
        return None

    def _add_subtree(self, parent_node: TreeNode[str], thing: Thing) -> None:
        if thing.children:
            node = parent_node.add(self._node_label(thing), data=thing.id, expand=True)
            for child in thing.children:
                self._add_subtree(node, child)
        else:
            node = parent_node.add_leaf(self._node_label(thing), data=thing.id)
        self._set_name_offset(node, thing)


def main() -> None:
    """Console-script entry point: run the Textual app."""
    LotTextualApp().run()


if __name__ == "__main__":
    main()
