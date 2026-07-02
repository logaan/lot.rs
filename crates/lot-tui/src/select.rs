//! Mouse text selection over the detail pane.
//!
//! A selection lives in *screen* coordinates and is resolved against the
//! rendered frame buffer, so the copied text is exactly what is on screen —
//! after wrapping and scrolling. Because it is anchored to the screen and not
//! the text, it is cleared whenever the content beneath it could move
//! (scrolling, cursor moves, reloads, any keypress).

use ratatui::buffer::Buffer;
use ratatui::layout::{Position, Rect};
use ratatui::style::{Modifier, Style};

/// A mouse selection from `anchor` (where the button went down) to `head`
/// (where the pointer is, or was released), both inclusive.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Selection {
    pub anchor: Position,
    pub head: Position,
    /// Whether the button is still down (the selection is being made).
    pub dragging: bool,
}

impl Selection {
    pub fn begin(pos: Position) -> Self {
        Self {
            anchor: pos,
            head: pos,
            dragging: true,
        }
    }

    /// A click that never moved selects nothing.
    pub fn is_empty(&self) -> bool {
        self.anchor == self.head
    }

    /// The endpoints in text order (top-to-bottom, then left-to-right), so a
    /// drag upwards selects the same range as one downwards.
    fn ordered(&self) -> (Position, Position) {
        if (self.anchor.y, self.anchor.x) <= (self.head.y, self.head.x) {
            (self.anchor, self.head)
        } else {
            (self.head, self.anchor)
        }
    }

    /// The selected column range on row `y`, clamped to `area`, or `None` if
    /// the row carries no selection. Selection flows like text: interior rows
    /// span the whole pane; the first and last rows run from/to the endpoints.
    fn row_span(&self, area: Rect, y: u16) -> Option<(u16, u16)> {
        let (start, end) = self.ordered();
        if y < start.y || y > end.y {
            return None;
        }
        let last_col = (area.right()).checked_sub(1)?;
        let x0 = if y == start.y { start.x } else { area.left() }.max(area.left());
        let x1 = if y == end.y { end.x } else { last_col }.min(last_col);
        (x0 <= x1).then_some((x0, x1))
    }
}

/// Clamp a mouse position into `area`, so dragging past an edge keeps
/// selecting from the boundary instead of escaping the pane.
pub fn clamp_to(pos: Position, area: Rect) -> Position {
    Position {
        x: pos
            .x
            .clamp(area.left(), area.right().saturating_sub(1).max(area.left())),
        y: pos
            .y
            .clamp(area.top(), area.bottom().saturating_sub(1).max(area.top())),
    }
}

/// Paint the selection over the rendered frame in reversed video.
pub fn highlight(buf: &mut Buffer, area: Rect, sel: &Selection) {
    for y in area.top()..area.bottom() {
        if let Some((x0, x1)) = sel.row_span(area, y) {
            buf.set_style(
                Rect::new(x0, y, x1 - x0 + 1, 1),
                Style::default().add_modifier(Modifier::REVERSED),
            );
        }
    }
}

/// Read the selected text back out of the rendered frame. Each row is
/// right-trimmed (the pane pads lines with spaces) and rows join with
/// newlines.
pub fn extract(buf: &Buffer, area: Rect, sel: &Selection) -> String {
    let mut rows: Vec<String> = Vec::new();
    for y in area.top()..area.bottom() {
        if let Some((x0, x1)) = sel.row_span(area, y) {
            let mut row = String::new();
            for x in x0..=x1 {
                row.push_str(buf[(x, y)].symbol());
            }
            rows.push(row.trim_end().to_string());
        }
    }
    rows.join("\n")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_buffer() -> Buffer {
        Buffer::with_lines(vec!["hello world", "second line", "third row  "])
    }

    fn finished(anchor: (u16, u16), head: (u16, u16)) -> Selection {
        Selection {
            anchor: Position::new(anchor.0, anchor.1),
            head: Position::new(head.0, head.1),
            dragging: false,
        }
    }

    #[test]
    fn click_without_drag_is_empty() {
        assert!(Selection::begin(Position::new(3, 1)).is_empty());
        assert!(!finished((3, 1), (4, 1)).is_empty());
    }

    #[test]
    fn extracts_a_single_row_range() {
        let buf = sample_buffer();
        let sel = finished((0, 0), (4, 0));
        assert_eq!(extract(&buf, *buf.area(), &sel), "hello");
    }

    #[test]
    fn extracts_across_rows_and_trims_padding() {
        let buf = sample_buffer();
        // From "world" on row 0 through "second" on row 1: the first row runs
        // to the pane's right edge, the last stops at the head.
        let sel = finished((6, 0), (5, 1));
        assert_eq!(extract(&buf, *buf.area(), &sel), "world\nsecond");
        // A whole padded row loses its trailing spaces.
        let sel = finished((0, 2), (10, 2));
        assert_eq!(extract(&buf, *buf.area(), &sel), "third row");
    }

    #[test]
    fn upward_drag_selects_the_same_text() {
        let buf = sample_buffer();
        let down = finished((6, 0), (5, 1));
        let up = finished((5, 1), (6, 0));
        assert_eq!(
            extract(&buf, *buf.area(), &down),
            extract(&buf, *buf.area(), &up)
        );
    }

    #[test]
    fn selection_is_clamped_to_the_area() {
        let buf = sample_buffer();
        // An area covering only the middle row: rows outside it yield nothing.
        let area = Rect::new(0, 1, 11, 1);
        let sel = finished((0, 0), (10, 2));
        assert_eq!(extract(&buf, area, &sel), "second line");
    }

    #[test]
    fn highlight_reverses_only_the_selected_cells() {
        let mut buf = sample_buffer();
        let area = *buf.area();
        let sel = finished((1, 0), (3, 0));
        highlight(&mut buf, area, &sel);
        assert!(!buf[(0, 0)].modifier.contains(Modifier::REVERSED));
        assert!(buf[(1, 0)].modifier.contains(Modifier::REVERSED));
        assert!(buf[(3, 0)].modifier.contains(Modifier::REVERSED));
        assert!(!buf[(4, 0)].modifier.contains(Modifier::REVERSED));
    }

    #[test]
    fn clamp_keeps_positions_inside_the_area() {
        let area = Rect::new(10, 5, 20, 10);
        assert_eq!(clamp_to(Position::new(0, 0), area), Position::new(10, 5));
        assert_eq!(clamp_to(Position::new(99, 99), area), Position::new(29, 14));
        assert_eq!(clamp_to(Position::new(15, 7), area), Position::new(15, 7));
    }
}
