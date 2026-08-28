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
``(tree-line, sub-row)`` pair and paints that slice — the depth indentation
and any fixed leading columns (a status, say) on the first row, blank
indentation lining the wrapped continuation up under the name on the rest (see
:meth:`set_name_offset`). Scrolling a node into view
(:meth:`_get_label_region`) and repaints (:meth:`_refresh_line` /
:meth:`_refresh_node`) are re-expressed in visual rows so the cursor, mouse
wheel, and live relabelling all keep working.

Unlike the stock Tree, no guide lines (``│ ├── └──``) and no expand/collapse
arrows are drawn: to keep the columns compact, a node's depth is shown by
nothing but plain indentation, :attr:`~textual.widgets.Tree.guide_depth` (two)
cells per level.

The root row is rendered as a centred, bold **heading** rather than an indented
line: it stands for the whole tree (the vault, or the branch the centre column
is rooted at), so its immediate children sit flush left (indent 0) and each
deeper level indents one :attr:`~textual.widgets.Tree.guide_depth` further —
i.e. a node at path depth ``d`` (the root being depth 1) is ``d - 2`` levels in.

Because wrapping is to the *visible* width there is never any horizontal
overflow, so the virtual width is pinned to the content width and the tree
never grows a horizontal scrollbar.
"""

from __future__ import annotations

from rich.style import Style
from rich.text import Text
from textual._segment_tools import line_pad
from textual.geometry import Region, Size
from textual.strip import Strip
from textual.widgets import Tree
from textual.widgets._tree import _TreeLine
from textual.widgets.tree import TreeDataType, TreeNode


class WrappingTree(Tree[TreeDataType]):
    """A :class:`~textual.widgets.Tree` that wraps long node labels over rows."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        # Depth is shown by indentation alone (no guides, no arrows), so use
        # the narrowest indent Textual allows: two cells per level.
        self.guide_depth = 2
        # Visual-row bookkeeping, rebuilt by _compute_wrapping on every _build:
        # the row->(tree-line, sub-row) map, and per tree-line its first visual
        # row, its row count, and its label pre-wrapped into per-row Text.
        self._row_index: list[tuple[int, int]] = []
        self._line_first_row: list[int] = []
        self._line_row_count: list[int] = []
        self._wrapped_labels: list[list[Text]] = []

    # --- name column -------------------------------------------------------
    #
    # A node's label may open with fixed "table columns" — in the LoT browser a
    # multi-select mark plus the status word — that should stay on the node's
    # first row while only the *name* after them wraps, lined up under itself in
    # its own column. The caller declares how many leading cells those columns
    # occupy per node via :meth:`set_name_offset`; the offset rides on the node
    # (so it is discarded with the node on a rebuild) and defaults to 0, which
    # wraps the whole label like a plain tree.

    def set_name_offset(self, node: TreeNode[TreeDataType], offset: int) -> None:
        """Record how many leading label cells are fixed columns for ``node``."""
        node._wrap_name_offset = offset  # type: ignore[attr-defined]

    def _name_offset(self, node: TreeNode[TreeDataType]) -> int:
        """The leading fixed-column cells for ``node`` (0 if none declared)."""
        return getattr(node, "_wrap_name_offset", 0)

    # --- layout ------------------------------------------------------------

    def _wrap_width(self) -> int:
        """The content width labels wrap to (and the pinned virtual width)."""
        width = self.scrollable_content_region.width
        if width <= 0:
            width = self.size.width
        return max(1, width)

    def _is_root(self, line: _TreeLine[TreeDataType]) -> bool:
        """Whether ``line`` is the tree's root — the centred heading row."""
        return len(line.path) <= 1

    def _indent_width(self, line: _TreeLine[TreeDataType]) -> int:
        """Depth indentation for ``line``, in cells.

        The root is a centred heading, so its immediate children sit flush left
        (indent 0) and each deeper level indents one :attr:`guide_depth`
        further: a node at path depth ``d`` (the root being depth 1) is
        ``d - 2`` levels in. (The stock Tree would put the root's children one
        level in; dropping that level is what pulls the whole outline left.)
        """
        return max(0, len(line.path) - 2) * self.guide_depth

    def _name_column(self, line: _TreeLine[TreeDataType]) -> int:
        """Cells before a node's *name*: depth indentation plus fixed columns.

        The fixed leading columns (mark/status) are reserved on *every* row of
        a node — printed on the first, blank on the wrapped continuations — so
        a wrapped name lines up under itself rather than under the status.
        """
        return self._indent_width(line) + self._name_offset(line.node)

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
            if self._is_root(line):
                # The root is a centred heading spanning the full width: the
                # whole label wraps (no fixed columns held back), and each row
                # is centred at render time (see _render_wrapped_row).
                name = line.node._label
                wrap_width = content_width
            else:
                # Only the name (the label past its fixed leading columns)
                # wraps; the mark/status columns stay on the node's first row.
                name = line.node._label[self._name_offset(line.node) :]
                wrap_width = max(1, content_width - self._name_column(line))
            name_lines = list(name.wrap(console, wrap_width, overflow="fold"))
            if not name_lines:
                name_lines = [Text("")]
            first_row.append(len(row_index))
            row_count.append(len(name_lines))
            for sub_row in range(len(name_lines)):
                row_index.append((line_no, sub_row))
            wrapped.append(name_lines)

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

        Unlike the stock :meth:`~textual.widgets.Tree._render_line`, no guide
        lines and no expand/collapse arrow are drawn: depth reads from plain
        indentation alone. The first sub-row emits the indentation, the fixed
        leading columns (mark/status), then the first wrapped line of the name;
        later sub-rows emit blank indentation of the same width, then the next
        wrapped line — so the name wraps under itself in its own column.

        The root row is the exception: it is drawn as a centred, bold heading —
        no indentation, no fixed columns, each wrapped row centred in the
        column (see :meth:`_render_heading_row`).
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

        if self.hover_line == line_no:
            line_style = self.get_component_rich_style("tree--highlight-line")
        else:
            line_style = base_style

        line_style += Style(meta={"line": line_no})

        # The name takes the full label style — including the cursor/hover
        # highlight — so a selected row's name is clearly highlighted.
        label_style = self.get_component_rich_style("tree--label", partial=True)
        # The fixed leading columns (mark/status) are semantic colour chips, so
        # they keep their *own* foreground on the plain line background even when
        # selected: the focused `tree--cursor` style sets a foreground that would
        # otherwise clobber the status colour (leaving it uncoloured on the
        # selected row — and blue-on-blue for the default `note` status against
        # the block cursor). Only the name carries the highlight.
        prefix_style = label_style
        if self.hover_line == line_no:
            label_style += self.get_component_rich_style(
                "tree--highlight", partial=True
            )
        if self.cursor_line == line_no:
            label_style += self.get_component_rich_style("tree--cursor", partial=False)

        node = line.node
        meta_style = Style(meta={"node": node._id, "line": line_no})

        if self._is_root(line):
            strip = self._render_heading_row(
                line_no, sub_row, line_style, label_style, meta_style
            )
            self._line_cache[cache_key] = strip
            return strip

        # The blank depth indentation. It carries the line meta (via the Text's
        # base style), so a click on it still selects the node.
        guides = Text(style=line_style)
        guides.append(" " * self._indent_width(line))

        # The fixed leading columns (mark/status) print on the first row; a
        # wrapped continuation reserves the same width as blank indentation so
        # the name column lines up under itself. The blanks still carry the line
        # meta, so a click anywhere on a wrapped row selects the node.
        name_offset = self._name_offset(node)
        if sub_row == 0:
            prefix = node._label[:name_offset].copy()
            prefix.stylize(prefix_style)
            prefix.stylize(meta_style)
            guides.append(prefix)
        elif name_offset:
            guides.append(" " * name_offset, style=line_style + meta_style)

        wrapped = self._wrapped_labels[line_no]
        label = (wrapped[sub_row] if sub_row < len(wrapped) else Text("")).copy()
        label.stylize(label_style)
        label.stylize(meta_style)
        guides.append(label)

        segments = list(guides.render(self.app.console))
        pad_width = max(self.virtual_size.width, width)
        segments = line_pad(segments, 0, pad_width - guides.cell_len, line_style)
        strip = Strip(segments)
        self._line_cache[cache_key] = strip
        return strip

    def _render_heading_row(
        self,
        line_no: int,
        sub_row: int,
        line_style: Style,
        label_style: Style,
        meta_style: Style,
    ) -> Strip:
        """Build the strip for one sub-row of the root heading.

        The root stands for the whole tree, so it is drawn centred and bold
        rather than indented: no depth indentation and no fixed mark/status
        column — the whole label wraps and each wrapped row is centred in the
        column. The centring blanks carry the line meta, so a click anywhere on
        the heading still selects the root.
        """
        width = self.size.width
        content_width = self._wrap_width()

        wrapped = self._wrapped_labels[line_no]
        label = (wrapped[sub_row] if sub_row < len(wrapped) else Text("")).copy()
        label.stylize(label_style + Style(bold=True))
        label.stylize(meta_style)

        # Tree labels reserve a blank mark column at the front (``"  "`` for
        # an unmarked Thing). Account for those cells when centring the visible
        # heading text; otherwise widths that leave an even remainder make the
        # root look two cells too far right.
        leading_blanks = len(label.plain) - len(label.plain.lstrip(" "))
        pad = max(0, (content_width - label.cell_len - leading_blanks) // 2)
        heading = Text(style=line_style)
        heading.append(" " * pad, style=line_style + meta_style)
        heading.append(label)

        segments = list(heading.render(self.app.console))
        pad_width = max(self.virtual_size.width, width)
        segments = line_pad(segments, 0, pad_width - heading.cell_len, line_style)
        return Strip(segments)
