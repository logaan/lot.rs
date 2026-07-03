"""The right-column detail pane: computed state plus the update thread.

The pane lives inside the app's ``#detail`` container and is entirely driven by
the app's :attr:`~lot_textual_ui.app.LotTextualApp.selected_id` reactive. On
mount it subscribes to that reactive (see the *detail-seam* note in
:mod:`lot_textual_ui.app`); every selection change kicks off an *exclusive*
Textual worker that loads the Thing's computed state and update thread through
the shared :class:`~lot_textual_ui.lot_cli.LotCli` and re-renders.

Layout, top to bottom:

* the computed state ``body`` (from ``lot thing get``) in a
  :class:`~textual.widgets.Markdown` widget, then
* the update thread (from ``lot thing updates``) rendered *oldest first* as
  independent :class:`UpdateItem` widgets — each a header line
  (``type · timestamp · id``) above the update ``body`` in its own
  :class:`~textual.widgets.Markdown`.

The pane itself is a :class:`~textual.containers.VerticalScroll`, so the whole
column scrolls by keyboard and mouse wheel and long threads stay fully
reachable (nothing is clipped). Only ``LotCli`` runs ``lot``; this module reuses
the app's instance rather than spawning its own.
"""

from __future__ import annotations

from textual import events, work
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Markdown, Static

from .lot_cli import LotCli, LotError

# Shown in place of markdown when a section has nothing to render, so the pane
# never looks broken for empty/na Things.
_NO_SELECTION = "Select a Thing."
_NO_STATE = "_This Thing has no computed state._"
_NO_UPDATES = "_No updates yet._"

# The scheme of an in-vault link. Bodies embed cross-references as markdown links
# whose target is a ``lot:`` URI (e.g. ``[Holiday](lot:6Ic9…)``); following one
# navigates the whole app to that Thing. Everything else (``https:``, …) is left
# to Textual's default link handling.
_LOT_SCHEME = "lot:"


class UpdateItem(Vertical):
    """One update in the thread: a header line above its markdown body.

    Carries the update's ``update_id`` so the copy actions (copy Update
    URI/path) can target whichever item is focused. The widget is focusable
    (``can_focus``) — clicking it makes it the "current update"; when no item is
    focused the pane falls back to the Thing's latest update (see
    :attr:`DetailPane.current_update_id`).

    Expand/collapse is a later phase; in the MVP every item is fully expanded.
    Later phases (expand/collapse, text selection) extend this same widget, so
    ``update_id`` and its focusability are shared state — keep them.
    """

    # Focusable so a click (or a future keyboard motion) can single out one
    # update for the copy actions. It joins the app's Tab order but not the
    # explicit ``h``/``l`` column focus chain, which targets the DetailPane.
    can_focus = True

    def __init__(self, *, update_id: str, header: str, body: str) -> None:
        super().__init__()
        self.update_id = update_id
        self._header = header
        self._body = body

    def compose(self):
        yield Label(self._header, classes="update-header")
        yield Markdown(self._body)


class DetailPane(VerticalScroll):
    """Scrollable computed-state + update-thread view for the selected Thing."""

    DEFAULT_CSS = """
    DetailPane {
        height: 1fr;
        width: 1fr;
    }

    DetailPane > #detail-updates {
        height: auto;
    }

    DetailPane .detail-muted {
        color: $text-muted;
    }

    UpdateItem {
        height: auto;
        margin-top: 1;
        padding-top: 1;
        border-top: solid $panel-lighten-2;
    }

    UpdateItem > .update-header {
        color: $text-muted;
        text-style: bold;
    }

    UpdateItem:focus {
        border-top: solid $accent;
    }
    """

    def __init__(self, lot_cli: LotCli, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lot_cli = lot_cli
        # The update ids currently rendered, oldest first, and the id of the
        # most recently focused update. Together they back `current_update_id`
        # so the copy-Update actions know which update to target.
        self._update_ids: list[str] = []
        self._last_focused_update_id: str | None = None

    def compose(self):
        # An empty-state notice, the computed-state markdown, and a container the
        # per-update items are mounted into. Sub-widgets are shown/hidden per
        # selection rather than recreated.
        yield Static(_NO_SELECTION, id="detail-empty", classes="detail-muted")
        yield Markdown(id="detail-state")
        yield Vertical(id="detail-updates")

    def on_mount(self) -> None:
        # Watch the app's shared selection reactive; init=True (the default)
        # fires the handler with the current value straight away.
        self.watch(self.app, "selected_id", self._on_selected_id_changed)

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        """Remember which update was last focused (for the copy actions).

        ``DescendantFocus`` bubbles up as any focusable descendant gains focus;
        we record the id when that descendant is an :class:`UpdateItem` so
        :attr:`current_update_id` can single it out even after focus later
        leaves it (e.g. the user tabs back to a tree).
        """
        if isinstance(event.widget, UpdateItem):
            self._last_focused_update_id = event.widget.update_id

    @property
    def current_update_id(self) -> str | None:
        """The update the copy-Update actions target, or ``None``.

        Preference order: the update whose :class:`UpdateItem` is focused right
        now; else the last update that was focused (while it is still on
        screen); else the Thing's current (latest) update; else ``None`` when the
        Thing has no updates.
        """
        focused = self.app.focused
        if isinstance(focused, UpdateItem):
            return focused.update_id
        if self._last_focused_update_id in self._update_ids:
            return self._last_focused_update_id
        return self._update_ids[-1] if self._update_ids else None

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        """Follow a ``lot:`` link in a rendered body by navigating to its Thing.

        ``Markdown.LinkClicked`` bubbles up from every :class:`Markdown` in the
        pane (the computed state and each update body), carrying the link
        ``href``. Only ``lot:`` URIs are ours: we stop those so no default
        handling fires, and navigate to the referenced Thing. Any other scheme
        (``https:``, …) is left to bubble to Textual's default handling.

        A ``lot:`` target may be a **Thing** id or an **Update** id. A Thing id
        already in the in-memory index is selected straight away (the common,
        synchronous case); anything else — an update id, or a Thing not currently
        indexed — is resolved through the CLI in a worker (see
        :meth:`_navigate_via_cli`), which maps it to the owning Thing.
        """
        href = event.href
        if not href.startswith(_LOT_SCHEME):
            return
        # This link is ours; don't let it fall through to any default handling.
        event.stop()
        target_id = href[len(_LOT_SCHEME) :].strip()
        if not target_id:
            return
        # Fast path: a Thing we already know navigates without a CLI round-trip.
        if self.app.thing_by_id(target_id) is not None:
            self.app.selected_id = target_id
            return
        # Otherwise resolve it (update id, or an unindexed Thing) via the CLI.
        self._navigate_via_cli(target_id)

    @work(exclusive=True, group="link-nav")
    async def _navigate_via_cli(self, target_id: str) -> None:
        """Resolve an arbitrary ``lot:`` id to its owning Thing and select it.

        ``lot thing get <id>`` resolves *both* a Thing id and an Update id to the
        owning Thing's computed state, whose ``task-id`` is the navigation
        target. An unknown id makes ``lot`` exit non-zero (:class:`LotError`), so
        a bad link surfaces as an error toast rather than crashing the app.
        """
        try:
            computed = await self._lot_cli.thing_get(target_id)
        except LotError as error:
            self.app.notify(str(error), title="Can't follow link", severity="error")
            return
        task_id = computed.task_id
        if not task_id:
            self.app.notify(
                f"No Thing found for '{target_id}'.",
                title="Can't follow link",
                severity="error",
            )
            return
        self.app.selected_id = task_id

    def _on_selected_id_changed(self, thing_id: str | None) -> None:
        self._load_detail(thing_id)

    def reload(self) -> None:
        """Re-load the currently selected Thing's detail from the CLI.

        The pane normally reloads only when ``selected_id`` *changes*; a live
        vault edit (see :meth:`~lot_textual_ui.app.LotTextualApp._apply_event`)
        can change the selected Thing's content without changing its id, so the
        app calls this to force a refresh. It reuses the same exclusive worker,
        so a reload supersedes any in-flight load.
        """
        self._load_detail(self.app.selected_id)

    @work(exclusive=True, group="detail-load")
    async def _load_detail(self, thing_id: str | None) -> None:
        """Load and render the selected Thing (or the empty state).

        Runs as an *exclusive* worker so a rapid succession of selections only
        renders the latest; earlier in-flight loads are cancelled.
        """
        empty = self.query_one("#detail-empty", Static)
        state = self.query_one("#detail-state", Markdown)
        updates_box = self.query_one("#detail-updates", Vertical)

        if thing_id is None:
            empty.display = True
            state.display = False
            updates_box.display = False
            await updates_box.remove_children()
            self._update_ids = []
            self._last_focused_update_id = None
            return

        empty.display = False
        state.display = True
        updates_box.display = True

        computed = await self._lot_cli.thing_get(thing_id)
        updates = await self._lot_cli.thing_updates(thing_id)

        body = (computed.body or "").strip()
        state.update(body if body else _NO_STATE)

        await updates_box.remove_children()
        # Track the rendered ids (oldest first) for `current_update_id`; a fresh
        # load clears any stale last-focused id that is no longer on screen.
        self._update_ids = [update.update_id for update in updates]
        self._last_focused_update_id = None
        items: list[Static] = []
        for update in updates:
            header = f"{update.type} · {update.at or '—'} · {update.update_id}"
            items.append(
                UpdateItem(
                    update_id=update.update_id,
                    header=header,
                    body=(update.body or "").strip(),
                )
            )
        if items:
            await updates_box.mount(*items)
        else:
            await updates_box.mount(Static(_NO_UPDATES, classes="detail-muted"))

        # New selection starts at the top; long content is reached by scrolling.
        self.scroll_home(animate=False)
