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
from .command_nav import CommandNav, CommandNavScreen
from .forms import BatchUpdateScreen
from .lot_cli import LotError
from .models import Thing
from .palette import LeafCommand


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

    def action_toggle_mark_siblings(self) -> None:
        """Toggle marks on the highlighted Thing and all of its siblings at once.

        Targets the same Thing as :meth:`action_toggle_mark` (under the focused
        tree's cursor, falling back to the in-view Thing) but acts on its whole
        sibling group — the Things sharing its parent, or its fellow roots when
        it is itself a root. The group is a single toggle: if every sibling is
        already marked they are all unmarked, otherwise the whole group is
        marked (marking any siblings that were not yet marked). The highlighted
        Thing is one of its own siblings, so it is always included.
        """
        thing_id = self._cursor_thing_id()
        if thing_id is None or thing_id not in self._index.by_id:
            self.notify(
                "Move the cursor onto a Thing first.",
                title="Nothing to mark",
                severity="warning",
            )
            return
        parent = self._index.parent_of.get(thing_id)
        siblings = parent.children if parent is not None else self._index.roots
        sibling_ids = {sibling.id for sibling in siblings}
        if sibling_ids <= self._marked:
            self._marked -= sibling_ids
        else:
            self._marked |= sibling_ids
        self._refresh_mark_indicators(sibling_ids)

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

    def _terminal_statuses(self) -> frozenset[str]:
        """The set of status names that count as *done* (terminal).

        Read from the vault's configured update types (``terminal = true``, like
        the stock ``done``) — the same notion of "done" the CLI archives by. When
        no types are configured (or none are terminal) nothing is done, so the
        active-descendant warning never fires and archiving behaves as before.
        """
        return frozenset(t.name for t in self._config.update_types if t.terminal)

    def _active_descendants(
        self, thing: Thing, terminal: frozenset[str], out: dict[str, Thing]
    ) -> None:
        """Collect ``thing``'s not-done descendants into ``out`` (keyed by id).

        Walks the whole subtree below ``thing`` (``thing`` itself excluded) and
        records every descendant whose status is not terminal — the unfinished
        work that archiving ``thing`` would delete along with it. Mirrors the
        core's ``collect_active_descendants`` so the UI's warning matches what
        the CLI would refuse.
        """
        for child in thing.children:
            if child.status not in terminal:
                out[child.id] = child
            self._active_descendants(child, terminal, out)

    def _marked_active_descendants(self) -> list[Thing]:
        """Every not-done Thing nested inside the marked set's subtrees.

        The union of every marked Thing's not-done descendants. Its *presence*
        decides whether the archive needs ``--force`` (the CLI refuses otherwise,
        even for a descendant that is itself marked — the marked parent is
        archived first and would take the still-active child with it). The
        *warning* shown to the user, however, drops the descendants that are
        themselves marked (see :meth:`_marked_active_surprises`), since archiving
        those is intended, not a surprise.
        """
        terminal = self._terminal_statuses()
        found: dict[str, Thing] = {}
        for thing_id in self._marked_in_order():
            thing = self._index.by_id.get(thing_id)
            if thing is not None:
                self._active_descendants(thing, terminal, found)
        return list(found.values())

    def _marked_active_surprises(self) -> list[Thing]:
        """Not-done Things a batch archive would delete that the user didn't mark.

        :meth:`_marked_active_descendants` minus any that are themselves marked —
        the ones worth naming in the confirmation, because the user did not ask
        for them directly.
        """
        return [
            thing
            for thing in self._marked_active_descendants()
            if thing.id not in self._marked
        ]

    def _vault_active_descendants(self) -> list[Thing]:
        """Not-done Things that ``lot vault archive`` would delete.

        Mirrors the CLI's selection: the outermost done Things (a terminal Thing
        is not descended into — its subtree goes with it), then the union of
        their not-done descendants. Empty when nothing is done.
        """
        terminal = self._terminal_statuses()
        if not terminal:
            return []
        done: list[Thing] = []

        def select(things: list[Thing]) -> None:
            for thing in things:
                if thing.status in terminal:
                    done.append(thing)
                else:
                    select(thing.children)

        select(self._index.roots)
        found: dict[str, Thing] = {}
        for thing in done:
            self._active_descendants(thing, terminal, found)
        return list(found.values())

    def _active_descendants_warning(self, active: list[Thing]) -> str:
        """A one-paragraph warning naming the not-done Things that would go too.

        Appended to an archive confirmation when :meth:`_marked_active_surprises`
        / :meth:`_vault_active_descendants` finds any, so the user sees exactly
        what unfinished work confirming would delete. The list is capped so a
        large sweep does not overflow the dialog.
        """
        names = [thing.name for thing in active]
        shown = ", ".join(names[:8])
        if len(names) > 8:
            shown += f", and {len(names) - 8} more"
        count = len(names)
        plural = "s" if count != 1 else ""
        return (
            f"\n\nWarning: this also deletes {count} not-done Thing{plural} "
            f"nested inside: {shown}."
        )

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
        When any marked Thing has not-done descendants that would be deleted
        with it, the confirmation names them and the run passes ``--force`` (the
        CLI refuses such an archive otherwise). The CLI also refuses when
        ``vault.auto-commit`` is ``false``; that error is surfaced per item like
        any other failure.
        """
        ids = self._require_marked("run Archive marked Things")
        if ids is None:
            return
        count = len(ids)
        plural = "s" if count != 1 else ""
        # `force` follows what the CLI would refuse (any not-done descendant);
        # the warning only names the ones the user didn't mark themselves.
        force = bool(self._marked_active_descendants())
        surprises = self._marked_active_surprises()
        message = (
            f"Archive {count} marked Thing{plural}? Each is removed from "
            "the vault together with all of its descendant Things "
            "(history is preserved in git)."
        )
        if surprises:
            message += self._active_descendants_warning(surprises)
        self.push_screen(
            ConfirmScreen(
                message,
                title="Archive marked Things",
                confirm_label="Archive",
            ),
            # A closure over `force`: the dialog is the confirmation, so a
            # confirmed archive with not-done descendants passes `--force`.
            lambda confirmed: self._archive_confirmed(confirmed, force=force),
        )

    def _archive_confirmed(self, confirmed: bool | None, force: bool = False) -> None:
        """Run the batch archive once the dialog confirms it."""
        if not confirmed:
            return

        async def archive(thing_id: str) -> str:
            return await self._lot_cli.thing_archive(thing_id, force=force)

        self._run_batch("Archive", archive, self._marked_in_order())

    def action_vault_archive(self) -> None:
        """Archive every done Thing in the vault, after a confirming dialog.

        Unlike the batch actions this needs no marks: it runs one
        ``lot vault archive`` (readme §5.4.2), which itself finds every Thing
        in a terminal status (an update type with ``terminal = true``, like
        the stock ``done``), commits them, and commits all their deletions in
        a single commit. When any done Thing has not-done descendants that would
        be swept away with it, the confirmation names them and the run passes
        ``--force`` (the CLI refuses the sweep otherwise). The CLI also refuses
        when ``vault.auto-commit`` is ``false``; that error text is surfaced in
        the failure toast.
        """
        active = self._vault_active_descendants()
        message = (
            "Archive every done Thing in the vault? Each Thing in a "
            "terminal status is removed "
            "together with all of its descendant Things "
            "(history is preserved in git)."
        )
        if active:
            message += self._active_descendants_warning(active)
        self.push_screen(
            ConfirmScreen(
                message,
                title="Archive done Things",
                confirm_label="Archive",
            ),
            lambda confirmed: self._vault_archive_confirmed(
                confirmed, force=bool(active)
            ),
        )

    def _vault_archive_confirmed(
        self, confirmed: bool | None, force: bool = False
    ) -> None:
        """Run the vault-wide archive once the dialog confirms it."""
        if not confirmed:
            return
        self._run_vault_archive(force)

    @work(exclusive=True, group="batch")
    async def _run_vault_archive(self, force: bool = False) -> None:
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
            archived = await self._lot_cli.vault_archive(force=force)
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

        Mirrors the single-Thing ``ctrl+u`` flow: the update **type** is picked
        first, in the command navigator (opened pre-navigated into ``update``),
        not from a form. What happens next depends on the chosen type:

        * a **body-taking** type (``work``/``info``, or a custom type) opens
          :class:`~lot_textual_ui.forms.BatchUpdateScreen` — a body-only form —
          and the collected body is applied to every marked Thing;
        * a **bodyless** type (``done``-likes) skips the form entirely and is
          recorded on every marked Thing straight away.

        Either way the one Update lands on each marked Thing in turn (e.g. mark
        a handful of finished tasks and record one ``done`` across all of them).
        """
        ids = self._require_marked("run Update marked Things")
        if ids is None:
            return
        self._open_batch_update_nav()

    @work(exclusive=True, group="command-nav")
    async def _open_batch_update_nav(self) -> None:
        """Open the command navigator inside ``update`` to pick the batch type.

        The batch's type-select step: the same navigator the ``ctrl+u`` shortcut
        opens, but pre-navigated into the ``update`` group and wired to
        :meth:`_batch_update_type_chosen` so a picked type runs over the marked
        set instead of the in-view Thing. Discovers (and caches) the command
        tree first, exactly like :meth:`_open_command_nav`.
        """
        if self._help_tree is None:
            try:
                self._help_tree = await self._lot_cli.help_yaml()
            except LotError as error:
                self.notify(str(error), title="Commands", severity="error")
                return
        nav = CommandNav(self._help_tree)
        update_index = next(
            (
                index
                for index, child in enumerate(nav.children())
                if child.get("name") == "update"
            ),
            None,
        )
        if update_index is None:
            self.notify(
                "No update commands are available.",
                title="Update marked Things",
                severity="warning",
            )
            return
        nav.path.append(update_index)
        self.push_screen(CommandNavScreen(nav), self._batch_update_type_chosen)

    def _batch_update_type_chosen(self, command: LeafCommand | None) -> None:
        """Route the picked update type into the batch (``None`` = cancelled).

        Mirrors :meth:`run_lot_command`'s ``("update", <type>)`` dispatch, but
        for the marked set: a body-taking type opens the body-only
        :class:`~lot_textual_ui.forms.BatchUpdateScreen`, a bodyless type is
        applied straight away, and a leaf that is not a creatable update type
        (e.g. ``update path``) cannot be batched, so it just notifies.
        """
        if command is None:
            return
        update_type = None
        if command.path[:1] == ("update",) and len(command.path) == 2:
            update_type = next(
                (t for t in self.creatable_update_types() if t.name == command.path[1]),
                None,
            )
        if update_type is None:
            self.notify(
                f"'lot {command.label}' cannot be applied to marked Things.",
                title="Update marked Things",
                severity="warning",
            )
            return
        ids = self._require_marked("run Update marked Things")
        if ids is None:
            return
        if update_type.takes_body:
            self.push_screen(
                BatchUpdateScreen(len(ids), kind=update_type.name),
                self._batch_update_submitted,
            )
        else:
            self._batch_update_submitted((update_type.name, None, None))

    def _batch_update_submitted(
        self, result: tuple[str, str | None, str | None] | None
    ) -> None:
        """Apply the collected Update to every marked Thing (``None`` = cancel).

        Receives the ``(kind, body, preamble)`` triple to apply — from
        :class:`~lot_textual_ui.forms.BatchUpdateScreen`'s dismiss for a
        body-taking type, or synthesised as ``(kind, None, None)`` by
        :meth:`_batch_update_type_chosen` for a bodyless type (which opens no
        form, so there is no preamble to collect). Either way it maps straight
        onto :meth:`LotCli.add_update` for every kind, built-in or custom; the
        one preamble is stamped onto every marked Thing.
        """
        if result is None:
            return
        kind, body, preamble = result

        async def add_update(thing_id: str) -> str:
            return await self._lot_cli.add_update(
                kind, thing_id, body, preamble=preamble
            )

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
