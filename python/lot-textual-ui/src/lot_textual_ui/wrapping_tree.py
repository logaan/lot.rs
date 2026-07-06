"""A :class:`~textual.widgets.Tree` whose node labels wrap instead of truncate.

Textual's stock :class:`~textual.widgets.Tree` renders exactly one terminal
line per node: a label wider than the column is cropped at the visible width
and the overflow is only reachable by scrolling the tree sideways. For the LoT
browser's two tree columns (left and centre), where a Thing's *name* is the
whole point, that hides information. :class:`WrappingTree` word-wraps each
node's label onto as many rows as it needs, so long names stay fully visible.

How it works
------------

The stock Tree's model — one :class:`~textual.widgets.tree.TreeNode` per
"tree line", with the cursor, click-to-select, and navigation all keyed on that
one-line-per-node index — is left completely intact. Only the *vertical layout*
changes: each tree line may now occupy several **visual rows**.

After the base :meth:`~textual.widgets.Tree._build` lays the nodes out, we
word-wrap every node's label to the available width and record, per tree line,
how many rows it needs and where its first row lands (see
:meth:`_compute_wrapping`). :attr:`virtual_size` height becomes the total row
count. Rendering (:meth:`render_line`) maps a visual row back to a
``(tree-line, sub-row)`` pair and paints that slice — the tree guides and the
expand/collapse icon on the first row, blank indentation lining the wrapped
continuation up under the label on the rest. Scrolling a node into view
(:meth:`_get_label_region`) and repaints (:meth:`_refresh_line` /
:meth:`_refresh_node`) are re-expressed in visual rows so the cursor, mouse
wheel, and live relabelling all keep working.

Because wrapping is to the *visible* width there is never any horizontal
overflow, so the virtual width is pinned to the content width and the tree
never grows a horizontal scrollbar.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import cast

from rich.style import Style
from rich.text import Text
from textual._segment_tools import line_pad
from textual.geometry import Region, Size
from textual.strip import Strip
from textual.widgets import Tree
from textual.widgets._tree import _TreeLine
from textual.widgets.tree import TreeDataType, TreeNode

TOGGLE_STYLE = Style.from_meta({"toggle": True})


class WrappingTree(Tree[TreeDataType]):
    """A :class:`~textual.widgets.Tree` that wraps long node labels over rows."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        # Visual-row bookkeeping, rebuilt by _compute_wrapping on every _build:
        # the row->(tree-line, sub-row) map, and per tree-line its first visual
        # row, its row count, and its label pre-wrapped into per-row Text.
        self._row_index: list[tuple[int, int]] = []
        self._line_first_row: list[int] = []
        self._line_row_count: list[int] = []
        self._wrapped_labels: list[list[Text]] = []

    # --- layout ------------------------------------------------------------

    def _wrap_width(self) -> int:
        """The content width labels wrap to (and the pinned virtual width)."""
        width = self.scrollable_content_region.width
        if width <= 0:
            width = self.size.width
        return max(1, width)

    def _label_indent(self, line: _TreeLine[TreeDataType]) -> int:
        """Cells before a node's label: its tree guides plus the icon slot.

        The expand/collapse icon (two cells) is reserved on *every* row of an
        expandable node — shown on the first, blank on the wrapped
        continuations — so a wrapped label lines up under itself rather than
        under the icon.
        """
        guide_width = line._get_guide_width(self.guide_depth, self.show_root)
        icon_slot = 2 if line.node._allow_expand else 0
        return guide_width + icon_slot

    def _build(self) -> None:
        """Lay the nodes out (base), then compute the wrapped-row layout."""
        super()._build()
        self._compute_wrapping()

    def _compute_wrapping(self) -> None:
        """Word-wrap every node's label and index the resulting visual rows.

        Fills the four visual-row tables and repins :attr:`virtual_size`: the
        width to the wrap width (no horizontal overflow, so no horizontal
        scrollbar) and the height to the total number of wrapped rows.
        """
        lines = self._tree_lines
        content_width = self._wrap_width()
        console = self.app.console

        row_index: list[tuple[int, int]] = []
        first_row: list[int] = []
        row_count: list[int] = []
        wrapped: list[list[Text]] = []

        for line_no, line in enumerate(lines):
            wrap_width = max(1, content_width - self._label_indent(line))
            label_lines = list(
                line.node._label.wrap(console, wrap_width, overflow="fold")
            )
            if not label_lines:
                label_lines = [Text("")]
            first_row.append(len(row_index))
            row_count.append(len(label_lines))
            for sub_row in range(len(label_lines)):
                row_index.append((line_no, sub_row))
            wrapped.append(label_lines)

        self._row_index = row_index
        self._line_first_row = first_row
        self._line_row_count = row_count
        self._wrapped_labels = wrapped
        self.virtual_size = Size(content_width, len(row_index))

    # --- scrolling / refresh, re-expressed in visual rows ------------------

    def _get_label_region(self, line: int) -> Region | None:
        """The visual region a node's (possibly multi-row) label occupies.

        Used by :meth:`~textual.widgets.Tree.scroll_to_line` to bring the
        cursor into view; returning the node's *full* wrapped span keeps every
        row of a tall node reachable by scrolling.
        """
        if not 0 <= line < len(self._line_first_row):
            return None
        return Region(
            0,
            self._line_first_row[line],
            self.virtual_size.width,
            self._line_row_count[line],
        )

    def _refresh_line(self, line: int) -> None:
        """Repaint one tree line — i.e. all of its wrapped visual rows."""
        if not 0 <= line < len(self._line_first_row):
            return
        y = self._line_first_row[line] - self.scroll_offset.y
        self.refresh(Region(0, y, self.size.width, self._line_row_count[line]))

    def _refresh_node(self, node: TreeNode[TreeDataType]) -> None:
        """Repaint the given node's line (cursor/hover/mark relabels).

        The stock version walks the visible slice of ``_tree_lines`` treating
        the scroll offset as a line index; under wrapping that offset is a
        visual row, so refresh the node by its own line instead.
        """
        if node._line != -1:
            self._refresh_line(node._line)

    # --- rendering ---------------------------------------------------------

    def render_line(self, y: int) -> Strip:
        """Render visual row ``y`` (viewport-relative) of the wrapped tree."""
        width = self.size.width
        scroll_x, scroll_y = self.scroll_offset
        base_style = self.rich_style
        row = y + scroll_y
        if not 0 <= row < len(self._row_index):
            return Strip.blank(width, base_style)
        line_no, sub_row = self._row_index[row]
        strip = self._render_wrapped_row(line_no, sub_row, base_style)
        return strip.crop(scroll_x, scroll_x + width)

    def _render_wrapped_row(
        self, line_no: int, sub_row: int, base_style: Style
    ) -> Strip:
        """Build the full-width strip for one sub-row of tree line ``line_no``.

        The guide-drawing logic mirrors the stock
        :meth:`~textual.widgets.Tree._render_line`; the divergence is the label:
        on the first sub-row we emit the expand/collapse icon (when the node has
        one) followed by the first wrapped line, and on later sub-rows blank
        indentation followed by the next wrapped line, so the label wraps under
        itself.
        """
        line = self._tree_lines[line_no]
        width = self.size.width

        cache_key = (
            line_no,
            sub_row,
            self.hover_line == line_no,
            width,
            self._updates,
            self._pseudo_class_state,
            tuple(node._updates for node in line.path),
        )
        cached = self._line_cache.get(cache_key)
        if cached is not None:
            return cached

        base_hidden = self.get_component_styles("tree--guides").color.a == 0
        hover_hidden = self.get_component_styles("tree--guides-hover").color.a == 0
        selected_hidden = (
            self.get_component_styles("tree--guides-selected").color.a == 0
        )

        base_guide_style = self.get_component_rich_style("tree--guides", partial=True)
        guide_hover_style = base_guide_style + self.get_component_rich_style(
            "tree--guides-hover", partial=True
        )
        guide_selected_style = base_guide_style + self.get_component_rich_style(
            "tree--guides-selected", partial=True
        )

        hover = line.path[0]._hover
        selected = line.path[0]._selected and self.has_focus

        def get_guides(style: Style, hidden: bool) -> tuple[str, str, str, str]:
            lines: tuple[Iterable[str], Iterable[str], Iterable[str], Iterable[str]]
            if self.show_guides and not hidden:
                lines = self.LINES["default"]
                if style.bold:
                    lines = self.LINES["bold"]
                elif style.underline2:
                    lines = self.LINES["double"]
            else:
                lines = ("  ", "  ", "  ", "  ")

            guide_depth = max(0, self.guide_depth - 2)
            guide_lines = tuple(
                f"{characters[0]}{characters[1] * guide_depth} " for characters in lines
            )
            return cast("tuple[str, str, str, str]", guide_lines)

        if self.hover_line == line_no:
            line_style = self.get_component_rich_style("tree--highlight-line")
        else:
            line_style = base_style

        line_style += Style(meta={"line": line_no})

        guides = Text(style=line_style)
        guides_append = guides.append

        guide_style = base_guide_style

        hidden = True
        for node in line.path[1:]:
            hidden = base_hidden
            if hover:
                guide_style = guide_hover_style
                hidden = hover_hidden
            if selected:
                guide_style = guide_selected_style
                hidden = selected_hidden

            space, vertical, _, _ = get_guides(guide_style, hidden)
            guide = space if node.is_last else vertical
            if node != line.path[-1]:
                guides_append(guide, style=guide_style)
            hover = hover or node._hover
            selected = (selected or node._selected) and self.has_focus

        if len(line.path) > 1:
            space, vertical, terminator, cross = get_guides(guide_style, hidden)
            if sub_row > 0:
                # A wrapped continuation drops the node's ├──/└── connector for
                # the same column an *ancestor* would use — a vertical guide when
                # a sibling follows below, blank when this is the last child — so
                # the label lines up under itself while the tree structure still
                # reads down the wrapped rows.
                guides.append(space if line.last else vertical, style=guide_style)
            elif line.last:
                guides.append(terminator, style=guide_style)
            else:
                guides.append(cross, style=guide_style)

        label_style = self.get_component_rich_style("tree--label", partial=True)
        if self.hover_line == line_no:
            label_style += self.get_component_rich_style(
                "tree--highlight", partial=True
            )
        if self.cursor_line == line_no:
            label_style += self.get_component_rich_style("tree--cursor", partial=False)

        node = line.node
        if node._allow_expand:
            if sub_row == 0:
                icon = self.ICON_NODE_EXPANDED if node.is_expanded else self.ICON_NODE
                guides.append(icon, style=base_style + TOGGLE_STYLE)
            else:
                guides.append("  ", style=base_style)

        wrapped = self._wrapped_labels[line_no]
        label = (wrapped[sub_row] if sub_row < len(wrapped) else Text("")).copy()
        label.stylize(label_style)
        label.stylize(Style(meta={"node": node._id, "line": line_no}))
        guides.append(label)

        segments = list(guides.render(self.app.console))
        pad_width = max(self.virtual_size.width, width)
        segments = line_pad(segments, 0, pad_width - guides.cell_len, line_style)
        strip = Strip(segments)
        self._line_cache[cache_key] = strip
        return strip
