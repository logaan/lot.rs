"""Applying the live ``lot watch`` stream to the UI (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

import yaml
from textual import work

from .detail import DetailPane
from .index import VAULT_ROOT
from .lot_cli import LotError
from .models import Update, WatchEvent


class WatchMixin:
    """Consuming ``lot watch`` events into live, minimal UI refreshes.

    ``lot watch`` (see ``lot help watch``) streams one minimal, incremental
    event per settled vault change. The app loads the baseline with
    ``thing_list()`` on mount — the watcher emits no initial-state event —
    then this layer patches a *single* node of the in-memory index per event
    so edits from the CLI, Claude sessions, or git appear without a restart.
    All subprocess/parsing lives in :class:`~lot_textual_ui.lot_cli.LotCli`;
    only already-typed events are folded into in-memory state here. Only a
    ``reload`` event (the rare no-single-Thing fallback) reloads the whole
    baseline.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it drives the app's index, selection reactives, tree rebuilds and
    the detail pane.
    """

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
            # A failed/absent `lot watch` (or a `thing_list` reload against a
            # vanished vault) is swallowed so the browser still works statically.
            pass
        except (OSError, ValueError, TypeError, yaml.YAMLError) as error:
            # `watch()` parses each frame internally and `_apply_event` may reload
            # via `thing_list`, so a malformed watch/list document raises a parse
            # error (not `LotError`) that would otherwise crash this long-lived
            # worker mid-session. Surface it once and stop watching gracefully.
            self.notify(str(error), title="Live updates stopped", severity="error")

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
        refreshed only when the changed Thing *is* the selection — so an
        unrelated event never disturbs its scroll position. A created/modified
        event's pre-parsed ``updates`` thread is handed through so that refresh
        needs no ``lot thing updates`` round-trip; the event's ``state`` is
        deliberately unused — the pane renders only the update thread (see
        :mod:`lot_textual_ui.detail`), never the computed state.
        """
        previous = self.selected_id
        old_parent = (
            self._index.parent_of.get(previous) if previous is not None else None
        )
        old_parent_id = old_parent.id if old_parent is not None else None

        if event.kind == "deleted":
            if event.id is not None:
                # The index returns the dropped ids; marks are the app's state,
                # so it prunes them itself (a mark must never outlive its Thing).
                self._marked -= self._index.remove_subtree(event.id)
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
            # The most-recent-update timestamp for the recency sort lives in the
            # event's computed state under `<status>-at` (mirroring the `updated`
            # key `lot thing list` emits); absent when the event carries no state.
            updated = None
            if event.state is not None and event.status:
                updated = event.state.timestamps.get(f"{event.status}-at")
            self._index.upsert_node(
                event.id, event.name or "", event.status or "", event.parent, updated
            )
            self._refresh_after(
                previous, old_parent_id, changed_id=event.id, updates=event.updates
            )

    def _refresh_after(
        self,
        previous: str | None,
        old_parent_id: str | None,
        changed_id: str | None,
        updates: list[Update] | None = None,
    ) -> None:
        """Re-resolve both selections and repaint the minimum after an index patch.

        If the left selection id changed (its Thing was removed), assigning it
        fires ``watch_selected_id`` — which rebuilds both trees and resets the
        centre's active item to the new root, reloading the detail pane. Otherwise
        the left reactive stays quiet, so the trees are rebuilt in place and the
        centre's active item is re-resolved: it survives if its Thing is still
        present, else it falls back to the root. The detail pane is refreshed only
        when the active item moved, or when ``changed_id`` *is* the (unchanged)
        active item — so an unrelated event never disturbs its scroll position.
        For that in-place refresh, a watch event's pre-parsed ``updates`` thread
        (when given) is rendered directly — sparing the ``lot thing updates``
        subprocess :meth:`DetailPane.reload` would spawn.
        """
        prev_active = self.active_id
        resolved = self._index.resolve_selection(previous, old_parent_id)
        if resolved != previous:
            self.selected_id = resolved
            return

        self._rebuild_left_tree(resolved)
        self._rebuild_centre_tree(resolved)

        # With the vault root selected the reset target is "no active item"
        # (the vault root is not a Thing the detail pane could show).
        fallback_active = None if resolved == VAULT_ROOT else resolved
        resolved_active = (
            prev_active
            if prev_active is not None and prev_active in self._index.by_id
            else fallback_active
        )
        if resolved_active != prev_active:
            # Assigning fires watch_active_id (highlight) and the detail watcher.
            self.active_id = resolved_active
            return
        self._highlight_centre(resolved_active)
        if changed_id is not None and changed_id == resolved_active:
            pane = self.query_one(DetailPane)
            if updates is not None:
                pane.render_updates(updates)
            else:
                pane.reload()

    def _resolve_selection(
        self, previous: str | None, old_parent_id: str | None
    ) -> str | None:
        """Re-resolve the selection against the freshly rebuilt index.

        A still-present selection survives; a vanished Thing falls back to its
        old parent, then the first root, then the vault root (which, unlike a
        Thing, always exists — so after mount the selection is never ``None``).
        """
        if previous == VAULT_ROOT:
            # The vault root is not in the index but always exists; a selection
            # on it survives any vault change.
            return VAULT_ROOT
        if previous is not None and previous in self._index.by_id:
            return previous
        if old_parent_id is not None and old_parent_id in self._index.by_id:
            return old_parent_id
        return self._index.roots[0].id if self._index.roots else VAULT_ROOT
