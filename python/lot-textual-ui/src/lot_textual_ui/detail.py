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

from textual import work
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Label, Markdown, Static

from .lot_cli import LotCli

# Shown in place of markdown when a section has nothing to render, so the pane
# never looks broken for empty/na Things.
_NO_SELECTION = "Select a Thing."
_NO_STATE = "_This Thing has no computed state._"
_NO_UPDATES = "_No updates yet._"


class UpdateItem(Vertical):
    """One update in the thread: a header line above its markdown body.

    Expand/collapse is a later phase; in the MVP every item is fully expanded.
    """

    def __init__(self, *, header: str, body: str) -> None:
        super().__init__()
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
    """

    def __init__(self, lot_cli: LotCli, **kwargs) -> None:
        super().__init__(**kwargs)
        self._lot_cli = lot_cli

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

    def _on_selected_id_changed(self, thing_id: str | None) -> None:
        self._load_detail(thing_id)

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
            return

        empty.display = False
        state.display = True
        updates_box.display = True

        computed = await self._lot_cli.thing_get(thing_id)
        updates = await self._lot_cli.thing_updates(thing_id)

        body = (computed.body or "").strip()
        state.update(body if body else _NO_STATE)

        await updates_box.remove_children()
        items: list[Static] = []
        for update in updates:
            header = f"{update.type} · {update.at or '—'} · {update.update_id}"
            items.append(UpdateItem(header=header, body=(update.body or "").strip()))
        if items:
            await updates_box.mount(*items)
        else:
            await updates_box.mount(Static(_NO_UPDATES, classes="detail-muted"))

        # New selection starts at the top; long content is reached by scrolling.
        self.scroll_home(animate=False)
