"""Config and theme handling for the app shell (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim so the
2000-line app module shrinks to its shell/selection/rendering core; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from collections.abc import Iterable

from textual import work
from textual.app import SystemCommand
from textual.binding import BindingsMap
from textual.dom import DOMNode
from textual.screen import Screen

from .keys import ACTION_BINDINGS, apply_overrides
from .lot_cli import LotError
from .models import EffectiveConfig, UpdateType


class ConfigThemeMixin:
    """Config loading and theme application/persistence for the LoT app.

    Config is read *only* through the CLI (``lot settings get`` via
    :meth:`LotCli.config_get`) — the TUI never reads config files itself. The
    whole merged config is parsed into :class:`EffectiveConfig` and kept on the
    app; the keybinding-override and vault-switching layers read it via
    :attr:`config`.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it relies on the app's ``self._lot_cli`` / ``self._config`` /
    ``self._status_colors`` / ``self._suppress_theme_persist`` state and on
    Textual's :class:`~textual.app.App` surface (``notify``, ``theme``,
    ``sub_title``, ``search_themes``, ``refresh_bindings``).
    """

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

        The effective set — entirely vault-configured (readme §1.3) — comes
        from ``lot settings get``'s ``update-types`` key
        (:attr:`EffectiveConfig.update_types`). Every configured type is
        creatable via ``lot update <name>``, the initial type (stock ``note``)
        included, so the whole set is offered. **Caching:** the set is read
        from the config the app already holds — no extra ``lot`` call per form
        open — and that config is (re)loaded on mount and on every vault
        switch (see :meth:`_apply_config` / :meth:`action_switch_vault`), so a
        vault's own types appear as soon as the app points at it. A
        mid-session config-file edit needs a vault re-switch (or restart) to
        show up, like every other config key.
        """
        return list(self._config.update_types)

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
        # Imported at call time: the colour table lives with the label
        # rendering in app.py, which imports this module (avoids the cycle).
        from .app import status_colors

        self._config = config
        # Recolour statuses for this vault's configured types. The trees are
        # (re)built after config loads (on mount and on vault switch), so the
        # new colours take effect with the next rebuild.
        self._status_colors = status_colors(config.update_types)
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

    def get_system_commands(self, screen: Screen) -> Iterable[SystemCommand]:
        """Textual's built-in palette commands, minus the ones we already offer.

        Textual contributes a *Theme* (theme picker) and *Quit* command to the
        command palette by default. Our own
        :data:`~lot_textual_ui.palette.INTERNAL_COMMANDS` already surface both
        — as *Switch theme* and *Quit* — so letting Textual's through as well
        listed each action twice (the confusing "two theme pickers"). Drop
        those two here and keep Textual's other utilities (help-panel,
        screenshot, minimize/maximize), which we do not reimplement.
        """
        redundant = {self.action_change_theme, self.action_quit}
        for command in super().get_system_commands(screen):
            if command.callback in redundant:
                continue
            yield command

    def action_switch_theme(self) -> None:
        """Open Textual's theme picker to switch theme at runtime.

        The single palette entry point for choosing a theme — surfaced as
        *Switch theme* (see :data:`~lot_textual_ui.palette.INTERNAL_COMMANDS`)
        and reused by the ``settings set theme`` leaf (see
        :meth:`~lot_textual_ui.commands.CommandsMixin.run_lot_command`). Reuses
        Textual's built-in theme search palette (:meth:`App.search_themes`),
        listing every registered theme for fuzzy selection. The chosen theme
        applies live *and* is persisted to the user config: picking one assigns
        :attr:`App.theme`, which the watcher installed in :meth:`on_mount` writes
        back through ``lot settings set theme`` (see :meth:`_on_theme_changed`),
        so the choice survives a restart.
        """
        self.search_themes()
