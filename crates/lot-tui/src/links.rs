//! Detect `lot:` ids in the rendered detail pane so clicks can follow them.
//!
//! Like the mouse selection, links live in *screen* coordinates and are found
//! by scanning the rendered frame buffer, so they line up with exactly what is
//! on screen — after wrapping and scrolling. The scan runs every draw and the
//! results are cached on the [`App`](crate::app::App) for mouse hit-testing.

use ratatui::buffer::Buffer;
use ratatui::layout::{Position, Rect};
use ratatui::style::{Color, Modifier, Style};

/// A canonical `lot:` id found on screen, spanning columns `x0..=x1` of row
/// `y`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Link {
    pub y: u16,
    pub x0: u16,
    pub x1: u16,
    pub id: String,
}

impl Link {
    pub fn contains(&self, pos: Position) -> bool {
        pos.y == self.y && (self.x0..=self.x1).contains(&pos.x)
    }
}

/// Scan `area` of the rendered frame for canonical `lot:` ids. An id must
/// stand alone (no alphanumeric character butting against either end) and
/// validate with [`lot_core::id::is_id`], so update-numbering like `lot:` in
/// prose or a truncated id never registers. An id wrapped across two rows is
/// not detected.
pub fn find(buf: &Buffer, area: Rect) -> Vec<Link> {
    let mut links = Vec::new();
    for y in area.top()..area.bottom() {
        // One (column, char) per cell; wide graphemes can't be part of an id,
        // so only each symbol's first char matters.
        let row: Vec<(u16, char)> = (area.left()..area.right())
            .map(|x| (x, buf[(x, y)].symbol().chars().next().unwrap_or(' ')))
            .collect();
        let mut i = 0;
        while i < row.len() {
            let bounded = i == 0 || !row[i - 1].1.is_ascii_alphanumeric();
            if bounded && starts_with(&row[i..], "lot:") {
                let mut j = i + 4;
                while j < row.len() && row[j].1.is_ascii_alphanumeric() {
                    j += 1;
                }
                let candidate: String = row[i..j].iter().map(|&(_, c)| c).collect();
                if lot_core::id::is_id(&candidate) {
                    links.push(Link {
                        y,
                        x0: row[i].0,
                        x1: row[j - 1].0,
                        id: candidate,
                    });
                    i = j;
                    continue;
                }
            }
            i += 1;
        }
    }
    links
}

fn starts_with(row: &[(u16, char)], prefix: &str) -> bool {
    row.len() >= prefix.len() && prefix.chars().zip(row).all(|(p, &(_, c))| p == c)
}

/// Paint every found link in the terminal's usual hyperlink dress so it reads
/// as clickable.
pub fn highlight(buf: &mut Buffer, links: &[Link]) {
    for link in links {
        buf.set_style(
            Rect::new(link.x0, link.y, link.x1 - link.x0 + 1, 1),
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::UNDERLINED),
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A valid canonical id (22 base62 chars after `lot:`).
    const ID: &str = "lot:033iZf0ZVsZAxCSMCnee6U";

    #[test]
    fn finds_an_id_and_its_screen_span() {
        let buf = Buffer::with_lines(vec![format!("see {ID} for more").as_str()]);
        let links = find(&buf, *buf.area());
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].id, ID);
        assert_eq!(links[0].y, 0);
        assert_eq!(links[0].x0, 4);
        assert_eq!(links[0].x1, 4 + ID.len() as u16 - 1);
    }

    #[test]
    fn finds_ids_in_punctuation_and_at_line_edges() {
        let buf = Buffer::with_lines(vec![format!("({ID})").as_str(), ID]);
        let links = find(&buf, *buf.area());
        assert_eq!(links.len(), 2);
        assert!(links.iter().all(|l| l.id == ID));
    }

    #[test]
    fn rejects_non_canonical_and_embedded_ids() {
        let pilot = format!("pi{ID}"); // "pilot:..." — embedded, not an id
        let buf = Buffer::with_lines(vec!["lot: is a prefix, lot:tooshort too", pilot.as_str()]);
        assert!(find(&buf, *buf.area()).is_empty());
    }

    #[test]
    fn respects_the_scanned_area() {
        let buf = Buffer::with_lines(vec![ID, ID]);
        // Only the second row is inside the area.
        let area = Rect::new(0, 1, buf.area().width, 1);
        let links = find(&buf, area);
        assert_eq!(links.len(), 1);
        assert_eq!(links[0].y, 1);
    }

    #[test]
    fn contains_hit_tests_the_span_inclusively() {
        let link = Link {
            y: 2,
            x0: 5,
            x1: 9,
            id: ID.into(),
        };
        assert!(link.contains(Position::new(5, 2)));
        assert!(link.contains(Position::new(9, 2)));
        assert!(!link.contains(Position::new(4, 2)));
        assert!(!link.contains(Position::new(10, 2)));
        assert!(!link.contains(Position::new(7, 1)));
    }

    #[test]
    fn highlight_styles_only_the_link_cells() {
        let mut buf = Buffer::with_lines(vec![format!("x {ID} y").as_str()]);
        let links = find(&buf, *buf.area());
        highlight(&mut buf, &links);
        assert!(!buf[(0, 0)].modifier.contains(Modifier::UNDERLINED));
        assert!(buf[(2, 0)].modifier.contains(Modifier::UNDERLINED));
        assert_eq!(buf[(2, 0)].fg, Color::Cyan);
    }
}
