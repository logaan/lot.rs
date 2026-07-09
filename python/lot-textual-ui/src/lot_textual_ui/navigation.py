"""Keyboard/mouse motion across the three columns (a mixin).

Extracted from :class:`~lot_textual_ui.app.LotTextualApp` verbatim; see the
class docstring for the seam rules.
"""

from __future__ import annotations

from textual.widget import Widget
from textual.widgets import Tree

from .detail import DetailPane


class NavigationMixin:
    """The pane-agnostic cursor/focus motions behind the central key table.

    These back the actions declared in :mod:`lot_textual_ui.keys`. Each motion
    is pane-agnostic: it looks up whichever column currently holds focus and
    does the right thing there — move a Tree's cursor or scroll the detail
    pane. The mouse needs no code here: Textual's Tree handles click-to-select
    and every pane (both trees and the DetailPane's VerticalScroll) handles
    the wheel.

    A mixin of :class:`~lot_textual_ui.app.LotTextualApp` (never instantiated
    alone): it queries the app's three columns and reads ``self.focused``.
    """

    def _focus_chain(self) -> list[Widget]:
        """The three columns, left to right, as the focus/drill order."""
        return [
            self.query_one("#left-tree", Tree),
            self.query_one("#centre-tree", Tree),
            self.query_one(DetailPane),
        ]

    def _column_of(self, widget: Widget | None) -> Widget | None:
        """The column of :meth:`_focus_chain` that ``widget`` belongs to, if any.

        Walks up from ``widget`` so a descendant of a column (an
        :class:`~lot_textual_ui.detail.UpdateItem` inside the detail pane)
        resolves to the column itself. ``None`` when it belongs to no column —
        ``None`` itself, or a widget on a modal screen pushed over the three
        columns. That distinction is one
        :meth:`~lot_textual_ui.app.LotTextualApp._sync_current_thing` needs, so it
        is drawn here rather than defaulted away.
        """
        chain = self._focus_chain()
        node: Widget | None = widget
        while node is not None:
            for column in chain:
                if node is column:
                    return column
            node = node.parent if isinstance(node.parent, Widget) else None
        return None

    def _focused_column(self) -> Widget | None:
        """The column holding focus, or ``None`` (see :meth:`_column_of`)."""
        return self._column_of(self.focused)

    def _focused_index(self) -> int:
        """Index into :meth:`_focus_chain` of the column that holds focus.

        Defaults to the left column when no column holds focus, so the vim
        motions always have somewhere to act.
        """
        column = self._focused_column()
        for index, candidate in enumerate(self._focus_chain()):
            if candidate is column:
                return index
        return 0

    def _detail_column_focused(self) -> bool:
        """Whether the detail/updates column (or a descendant) holds focus.

        Compares against the live :class:`~lot_textual_ui.detail.DetailPane`
        *instance* rather than a hardcoded :meth:`_focus_chain` index, so a
        future reorder of the columns can't silently mis-gate the
        detail-column-scoped actions (fold/copy — see
        :meth:`~lot_textual_ui.commands.CommandsMixin.check_action`).
        """
        return self._focused_column() is self.query_one(DetailPane)

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
