"""The right-column detail pane: the selected Thing's update thread.

The pane lives inside the app's ``#detail`` container and is entirely driven by
the app's :attr:`~lot_textual_ui.app.LotTextualApp.active_id` reactive — the
centre column's active item. On mount it subscribes to that reactive (see the
*detail-seam* note in :mod:`lot_textual_ui.app`); every change to the active item
kicks off an *exclusive* Textual worker that loads the Thing's update thread
through the shared :class:`~lot_textual_ui.lot_cli.LotCli` and re-renders.

The pane renders the update thread (from ``lot thing updates``) *oldest first* as
independent :class:`UpdateItem` widgets — each a header line
(``type · timestamp · id``) above the update ``body`` in its own
:class:`~textual.widgets.Markdown`. Bare canonical ``lot:`` ids in a body are
rewritten into markdown links first (:func:`linkify_lot_ids`), so a pasted id
navigates on click just like an authored ``[x](lot:…)`` link.

It deliberately does **not** also render ``lot thing get``'s computed ``body``:
that body *is* the concatenation of every update's body (readme §3.1.4), so
showing it above the thread duplicated the whole thread. The thread is the
richer representation of the same content — per-update headers, collapse, focus,
copy, and ``lot:`` link navigation — so it stands alone.

The pane itself is a :class:`~textual.containers.VerticalScroll`, so the whole
column scrolls by keyboard and mouse wheel and long threads stay fully
reachable (nothing is clipped). Only ``LotCli`` runs ``lot``; this module reuses
the app's instance rather than spawning its own.
"""

from __future__ import annotations

import re

from textual import events, work
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widgets import Label, Markdown, Static

from .lot_cli import LotCli, LotError
from .models import Update

# Shown in place of the thread when there is nothing to render, so the pane never
# looks broken for empty/na Things.
_NO_SELECTION = "Select a Thing."
_NO_UPDATES = "_No updates yet._"

# The scheme of an in-vault link. Bodies embed cross-references as markdown links
# whose target is a ``lot:`` URI (e.g. ``[Holiday](lot:6Ic9…)``); following one
# navigates the whole app to that Thing. Everything else (``https:``, …) is left
# to Textual's default link handling.
_LOT_SCHEME = "lot:"

# A *bare* canonical id in body text: ``lot:`` plus exactly 22 base62 digits
# (crates/lot-core/src/id.rs). Bodies routinely mention ids in prose without
# link syntax; :func:`linkify_lot_ids` wraps such mentions in markdown links so
# they get the same click-to-navigate treatment as authored ``[x](lot:…)``
# links. The guards keep already-linked ids untouched: not preceded by ``](``
# (an existing link's target), ``[`` (link text), ``<`` (autolink), or a base62
# digit (mid-word, e.g. ``pilot:…``), and not followed by a base62 digit (a
# 23+-digit token is not a canonical id).
_BARE_LOT_ID = re.compile(
    r"(?<![0-9A-Za-z<\[])(?<!\]\()lot:[0-9A-Za-z]{22}(?![0-9A-Za-z])"
)

# Inline code spans, captured (the group) so ``re.split`` keeps them as the
# odd-indexed segments and linkification can pass them through verbatim.
_CODE_SPAN = re.compile(r"(`+[^`]*`+)")

# A line opening or closing a fenced code block (CommonMark allows up to three
# leading spaces). Ids inside fences are code, not references.
_FENCE = re.compile(r"^ {0,3}(```|~~~)")


def linkify_lot_ids(markdown: str) -> str:
    """Wrap bare canonical ``lot:`` ids in markdown links to themselves.

    ``lot:6Ic9…`` in prose becomes ``[lot:6Ic9…](lot:6Ic9…)`` — same visible
    text, but rendered as a clickable link that the pane's ``lot:`` navigation
    (see :meth:`DetailPane.on_markdown_link_clicked`) already handles. Ids
    inside fenced code blocks, inline code spans, or existing link/autolink
    syntax are left alone (fence tracking is line-based and treats any
    `````/``~~~`` line as a toggle — deliberately simple, not full CommonMark).
    """
    out: list[str] = []
    in_fence = False
    for line in markdown.split("\n"):
        if _FENCE.match(line):
            in_fence = not in_fence
            out.append(line)
            continue
        if in_fence:
            out.append(line)
            continue
        segments = _CODE_SPAN.split(line)
        for i in range(0, len(segments), 2):  # even indexes: outside code spans
            segments[i] = _BARE_LOT_ID.sub(r"[\g<0>](\g<0>)", segments[i])
        out.append("".join(segments))
    return "\n".join(out)


class UpdateItem(Vertical):
    """One update in the thread: a header line above its markdown body.

    Carries the update's ``update_id`` so the copy actions (copy Update
    URI/path) can target whichever item is focused. The widget is focusable
    (``can_focus``) — clicking it makes it the "current update"; when no item is
    focused the pane falls back to the Thing's latest update (see
    :attr:`DetailPane.current_update_id`).

    Each item can be **collapsed** (only its header line shows) or **expanded**
    (header plus the full markdown body). Items start expanded. The header
    carries a chevron marker (``▼`` expanded, ``▶`` collapsed) so the state is
    visible; clicking the item or pressing the toggle key (see
    :meth:`~lot_textual_ui.app.LotTextualApp.action_toggle_update`) flips it.
    Collapsing only hides the body — ``update_id`` and focusability are
    unchanged, so the copy actions and ``current_update_id`` keep working.
    Later phases (text selection) extend this same widget, so ``update_id`` and
    its focusability are shared state — keep them.
    """

    # Focusable so a click (or a future keyboard motion) can single out one
    # update for the copy actions. It joins the app's Tab order but not the
    # explicit ``h``/``l`` column focus chain, which targets the DetailPane.
    can_focus = True

    # Expand/collapse state. Default ``False`` (expanded) matches the previous
    # always-expanded behaviour; the watcher hides/shows the body and repaints
    # the header chevron. Set via :meth:`toggle` (click or key) after mount.
    collapsed: reactive[bool] = reactive(False, init=False)

    def __init__(self, *, update_id: str, header: str, body: str) -> None:
        super().__init__()
        self.update_id = update_id
        self._header = header
        self._body = body
        # The header Label, captured on compose so :meth:`on_click` can tell a
        # header click (toggles collapse) from a body click (must not, so mouse
        # text-selection in the body is not undone by a fold).
        self._header_label: Label | None = None

    def compose(self):
        self._header_label = Label(self._header_text(), classes="update-header")
        yield self._header_label
        yield Markdown(self._body)

    def _header_text(self) -> str:
        """The header line prefixed with a chevron reflecting collapse state."""
        marker = "▶" if self.collapsed else "▼"
        return f"{marker} {self._header}"

    def toggle(self) -> None:
        """Flip between collapsed (header only) and expanded (header + body)."""
        self.collapsed = not self.collapsed

    def watch_collapsed(self, collapsed: bool) -> None:
        """Hide/show the body and repaint the header chevron on state change.

        Fires only after mount (the initial expanded state needs no repaint), so
        the ``query_one`` calls always find the composed children.
        """
        self.query_one(Markdown).display = not collapsed
        self.query_one(".update-header", Label).update(self._header_text())

    def on_click(self, event: events.Click) -> None:
        """Toggle collapse only when the **header** is clicked.

        Scoped to the header so dragging to select text in the body does not
        collapse the update out from under the selection (Textual tracks the
        mouse-drag selection separately; only the synthesized click is ours to
        interpret). ``event.widget`` is the widget under the pointer: the header
        :class:`~textual.widgets.Label` for a header click, the body
        :class:`~textual.widgets.Markdown` otherwise. A body click is left to
        bubble (it still focuses the item via ``can_focus``, keeping it the
        current update); a header click is stopped and folds the item. A click
        that follows a ``lot:`` body link navigates away and reloads the whole
        pane, so no toggle there either.
        """
        if event.widget is not self._header_label:
            return
        event.stop()
        self.toggle()


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
        # An empty-state notice and a container the per-update items are mounted
        # into. Sub-widgets are shown/hidden per selection rather than recreated.
        yield Static(_NO_SELECTION, id="detail-empty", classes="detail-muted")
        yield Vertical(id="detail-updates")

    def on_mount(self) -> None:
        # Watch the app's centre-column active item; init=True (the default)
        # fires the handler with the current value straight away.
        self.watch(self.app, "active_id", self._on_active_id_changed)

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

    def set_all_collapsed(self, collapsed: bool) -> None:
        """Collapse or expand every rendered update at once.

        Backs the palette's "Collapse/Expand all updates" commands. Iterates the
        mounted :class:`UpdateItem`\\s and sets their state uniformly; the scroll
        offset is left untouched so the view does not jump.
        """
        for item in self.query(UpdateItem):
            item.collapsed = collapsed

    def on_markdown_link_clicked(self, event: Markdown.LinkClicked) -> None:
        """Follow a ``lot:`` link in a rendered body by navigating to its Thing.

        ``Markdown.LinkClicked`` bubbles up from every :class:`Markdown` in the
        pane (each update body), carrying the link ``href``. Only ``lot:`` URIs
        are ours: we stop those so no default
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

    def _on_active_id_changed(self, thing_id: str | None) -> None:
        self._load_detail(thing_id)

    def reload(self) -> None:
        """Re-load the in-view (centre-active) Thing's detail from the CLI.

        The pane normally reloads only when ``active_id`` *changes*; a live vault
        edit (see :meth:`~lot_textual_ui.app.LotTextualApp._apply_event`) can
        change the in-view Thing's content without changing its id, so the app
        calls this to force a refresh. It reuses the same exclusive worker, so a
        reload supersedes any in-flight load.
        """
        self._load_detail(self.app.active_id)

    def render_updates(self, updates: list[Update]) -> None:
        """Render an already-parsed update thread, skipping the CLI round-trip.

        The seam for ``lot watch``'s created/modified events, which carry the
        changed Thing's full ``updates`` thread precisely so a detail view
        needs no follow-up ``lot thing updates`` call (see
        :meth:`~lot_textual_ui.app.LotTextualApp._refresh_after`). Runs in the
        same exclusive worker group as :meth:`reload`, so it supersedes any
        in-flight load (and vice versa).
        """
        self._render_prepared(list(updates))

    @work(exclusive=True, group="detail-load")
    async def _render_prepared(self, updates: list[Update]) -> None:
        """Worker wrapper: render a pre-parsed thread (see :meth:`render_updates`)."""
        await self._render_thread(updates)

    @work(exclusive=True, group="detail-load")
    async def _load_detail(self, thing_id: str | None) -> None:
        """Load and render the selected Thing (or the empty state).

        Runs as an *exclusive* worker so a rapid succession of selections only
        renders the latest; earlier in-flight loads are cancelled.
        """
        empty = self.query_one("#detail-empty", Static)
        updates_box = self.query_one("#detail-updates", Vertical)

        if thing_id is None:
            empty.display = True
            updates_box.display = False
            await updates_box.remove_children()
            self._update_ids = []
            self._last_focused_update_id = None
            return

        empty.display = False
        updates_box.display = True

        await self._render_thread(await self._lot_cli.thing_updates(thing_id))

    async def _render_thread(self, updates: list[Update]) -> None:
        """Replace the rendered thread with ``updates`` (oldest first).

        The shared rendering tail of :meth:`_load_detail` (CLI-fetched) and
        :meth:`render_updates` (watch-event payload).
        """
        empty = self.query_one("#detail-empty", Static)
        updates_box = self.query_one("#detail-updates", Vertical)
        empty.display = False
        updates_box.display = True

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
                    body=linkify_lot_ids((update.body or "").strip()),
                )
            )
        if items:
            await updates_box.mount(*items)
        else:
            await updates_box.mount(Static(_NO_UPDATES, classes="detail-muted"))

        # New selection starts at the top; long content is reached by scrolling.
        self.scroll_home(animate=False)
