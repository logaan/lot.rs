"""Runtime vault switching for the app shell (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from textual import work
from textual.widgets import Tree

from .detail import DetailPane
from .index import VAULT_ROOT
from .lot_cli import LotError
from .vault_picker import VaultPickerScreen


class VaultSwitchingMixin:
    """Pointing the whole UI at another configured vault, live.

    The TUI can be pointed at any of the vaults declared in config
    (``[[tui.vaults]]``, surfaced by ``lot settings get`` as
    :attr:`EffectiveConfig.vaults`). Switching retargets the *one* shared
    :class:`~lot_textual_ui.lot_cli.LotCli` at the new vault's
    ``LOT_VAULT_PATH`` (see :meth:`LotCli.set_vault_path`) and reloads
    everything against it — the whole UI (app, detail pane, palette providers)
    shares that instance, so pointing it at the new vault re-homes them all at
    once.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it drives the app's index/tree state, the watch worker group, and
    the config layer (:class:`~lot_textual_ui.config_theme.ConfigThemeMixin`).
    """

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
        # Land on the new vault's root — the whole-vault view, as on launch.
        # Assigning fires the reactive only when the id changes; the index is
        # wholesale different, so repaint unconditionally (covers the common
        # case of the vault root already being selected).
        self.selected_id = VAULT_ROOT
        self._rebuild_left_tree(VAULT_ROOT)
        self._rebuild_centre_tree(VAULT_ROOT)
        # Re-home the centre's active item too (the vault root is not a Thing,
        # so there is none), then reload the detail pane (unconditionally, in
        # case the reactives skipped).
        self.active_id = None
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
