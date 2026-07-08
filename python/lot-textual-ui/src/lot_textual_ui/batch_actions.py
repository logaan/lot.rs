"""Multi-select marks and the batch operations over them (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from rich.text import Text
from textual import work
from textual.widgets import Tree
from textual.widgets.tree import TreeNode

from .batch import TOP_LEVEL, ConfirmScreen, ThingPickerScreen
from .forms import BatchUpdateScreen
from .lot_cli import LotError
from .models import Thing


class BatchActionsMixin:
    """Marking Things and running batch operations over the marked set.

    Multi-select is a set of marked Thing ids (``_marked``) the batch actions
    operate on. Marking is per-Thing, not per-row: a Thing shown in both tree
    columns is marked in both at once. Marked rows carry the
    :data:`~lot_textual_ui.app.MARK_INDICATOR` glyph; labels are re-rendered
    in place so toggling never rebuilds a tree (and so never disturbs its
    cursor).

    Each batch action collects its remaining input through a modal (a
    destination picker, a confirmation, the batch-update form), then runs the
    per-item ``lot`` calls **sequentially** in one worker (:meth:`_run_batch`):
    progress is shown in the header subtitle, a failed item never aborts the
    rest, successes are unmarked as they land, and at the end the vault is
    reloaded and a summary (with every failure's Thing and error) is shown.
    Failed items stay marked so they can be retried after fixing the cause.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it owns no state of its own — ``self._marked`` lives on the app —
    and drives the app's index, trees, subtitle and reload path.
    """

    @property
    def marked_ids(self) -> frozenset[str]:
        """The ids of the currently marked Things (a read-only snapshot)."""
        return frozenset(self._marked)

    def _node_label(self, thing: Thing) -> Text:
        """A tree label for ``thing``, mark-aware (see :func:`node_label`)."""
        # Imported at call time: the label renderer lives with the colour
        # table in app.py, which imports this module (avoids the cycle).
        from .app import node_label

        return node_label(
            thing, marked=thing.id in self._marked, colors=self._status_colors
        )

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
        if thing_id is None or thing_id not in self._index.by_id:
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
        thing = self._index.by_id.get(node.data) if isinstance(node.data, str) else None
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
        self._marked &= set(self._index.by_id)

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
        return [thing_id for thing_id in self._index.by_id if thing_id in self._marked]

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
            ThingPickerScreen(self._index.roots, exclude=set(ids)),
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
        in a terminal status (an update type with ``terminal = true``, like
        the stock ``done``), commits them, and commits all their deletions in
        a single commit. The CLI refuses when ``vault.auto-commit`` is
        ``false``; that error text is surfaced in the failure toast.
        """
        self.push_screen(
            ConfirmScreen(
                "Archive every done Thing in the vault? Each Thing in a "
                "terminal status is removed "
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
        types = self.creatable_update_types()
        if not types:
            self._notify_no_update_types()
            return
        self.push_screen(
            BatchUpdateScreen(len(ids), update_types=types),
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
            thing_id: (
                thing.name if (thing := self._index.by_id.get(thing_id)) else thing_id
            )
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
