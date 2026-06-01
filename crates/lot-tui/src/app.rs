//! Application state and input handling for the read-only LoT TUI.

use crate::model::Row;
use ratatui::crossterm::event::{
    KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::layout::{Position, Rect};

/// The responsive layout in effect, chosen from the terminal's size.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Three columns: tree | children | detail.
    Wide,
    /// Two columns: tree | detail.
    Normal,
    /// Two rows: tree above, detail below.
    Tall,
    /// One column: tree only; detail opens as an overlay.
    Small,
}

impl Mode {
    /// Pick a layout from the available width and height (in cells).
    pub fn for_size(width: u16, height: u16) -> Mode {
        if width >= 160 {
            Mode::Wide
        } else if width >= 100 {
            Mode::Normal
        } else if height >= 30 {
            Mode::Tall
        } else {
            Mode::Small
        }
    }
}

/// The whole UI state.
pub struct App {
    pub rows: Vec<Row>,
    pub vault_path: String,
    /// Index into `rows` of the highlighted Thing.
    pub cursor: usize,
    /// Vertical scroll offset of the detail pane.
    pub detail_scroll: u16,
    /// Number of rendered detail lines (for clamping `detail_scroll`).
    pub detail_len: u16,
    /// In `Small` mode, whether the detail overlay is open.
    pub overlay: bool,
    pub mode: Mode,
    pub quit: bool,
    /// Inner rect of the tree list (set each draw, used for mouse hit-testing).
    pub tree_area: Rect,
    /// Inner rect of the detail pane (set each draw).
    pub detail_area: Rect,
}

impl App {
    pub fn new(rows: Vec<Row>, vault_path: String) -> Self {
        Self {
            rows,
            vault_path,
            cursor: 0,
            detail_scroll: 0,
            detail_len: 0,
            overlay: false,
            mode: Mode::Normal,
            quit: false,
            tree_area: Rect::default(),
            detail_area: Rect::default(),
        }
    }

    /// The Thing currently under the cursor, if any.
    pub fn selected(&self) -> Option<&Row> {
        self.rows.get(self.cursor)
    }

    fn move_cursor(&mut self, delta: isize) {
        if self.rows.is_empty() {
            return;
        }
        let last = self.rows.len() - 1;
        let next = (self.cursor as isize + delta).clamp(0, last as isize) as usize;
        if next != self.cursor {
            self.cursor = next;
            // A new selection resets the detail scroll to the top.
            self.detail_scroll = 0;
        }
    }

    fn scroll_detail(&mut self, delta: isize) {
        let max = self.detail_len.saturating_sub(1);
        let next = (self.detail_scroll as isize + delta).clamp(0, max as isize);
        self.detail_scroll = next as u16;
    }

    pub fn on_key(&mut self, key: KeyEvent) {
        // Ctrl-C always quits.
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.quit = true;
            return;
        }
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            KeyCode::Esc => {
                if self.overlay {
                    self.overlay = false;
                } else {
                    self.quit = true;
                }
            }
            KeyCode::Char('j') | KeyCode::Down => self.move_cursor(1),
            KeyCode::Char('k') | KeyCode::Up => self.move_cursor(-1),
            KeyCode::Char('g') | KeyCode::Home => self.cursor = 0,
            KeyCode::Char('G') | KeyCode::End => {
                self.cursor = self.rows.len().saturating_sub(1);
                self.detail_scroll = 0;
            }
            KeyCode::PageDown => self.move_cursor(10),
            KeyCode::PageUp => self.move_cursor(-10),
            // Scroll the detail pane with shift+arrows.
            KeyCode::Char('J') => self.scroll_detail(1),
            KeyCode::Char('K') => self.scroll_detail(-1),
            KeyCode::Enter if self.mode == Mode::Small => {
                self.overlay = true;
            }
            _ => {}
        }
    }

    pub fn on_mouse(&mut self, ev: MouseEvent) {
        let pos = Position {
            x: ev.column,
            y: ev.row,
        };
        match ev.kind {
            MouseEventKind::ScrollDown => {
                if self.detail_area.contains(pos) {
                    self.scroll_detail(1);
                } else {
                    self.move_cursor(1);
                }
            }
            MouseEventKind::ScrollUp => {
                if self.detail_area.contains(pos) {
                    self.scroll_detail(-1);
                } else {
                    self.move_cursor(-1);
                }
            }
            MouseEventKind::Down(MouseButton::Left) if self.tree_area.contains(pos) => {
                let offset = (pos.y - self.tree_area.y) as usize;
                let target = self.tree_first() + offset;
                if target < self.rows.len() {
                    self.cursor = target;
                    self.detail_scroll = 0;
                }
            }
            _ => {}
        }
    }

    /// The index of the first tree row that should be visible so the cursor
    /// stays on screen given the current tree viewport height.
    pub fn tree_first(&self) -> usize {
        let height = self.tree_area.height.max(1) as usize;
        if self.cursor < height {
            0
        } else {
            self.cursor - height + 1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn mode_thresholds() {
        assert_eq!(Mode::for_size(200, 50), Mode::Wide);
        assert_eq!(Mode::for_size(120, 50), Mode::Normal);
        assert_eq!(Mode::for_size(80, 40), Mode::Tall);
        assert_eq!(Mode::for_size(80, 20), Mode::Small);
    }

    fn app_with(n: usize) -> App {
        let rows = (0..n)
            .map(|i| Row {
                title: format!("Thing {i}"),
                status: "note".into(),
                depth: 0,
                children: Vec::new(),
                meta: Vec::new(),
                body: String::new(),
            })
            .collect();
        App::new(rows, "/tmp/vault".into())
    }

    #[test]
    fn cursor_clamps_at_both_ends() {
        let mut app = app_with(3);
        app.move_cursor(-1);
        assert_eq!(app.cursor, 0);
        app.move_cursor(99);
        assert_eq!(app.cursor, 2);
    }

    #[test]
    fn moving_cursor_resets_detail_scroll() {
        let mut app = app_with(3);
        app.detail_scroll = 5;
        app.detail_len = 10;
        app.move_cursor(1);
        assert_eq!(app.detail_scroll, 0);
    }

    #[test]
    fn tree_first_keeps_cursor_visible() {
        let mut app = app_with(100);
        app.tree_area = Rect::new(0, 0, 20, 10);
        app.cursor = 0;
        assert_eq!(app.tree_first(), 0);
        app.cursor = 50;
        // Cursor must be within the last visible window.
        assert_eq!(app.tree_first(), 41);
    }
}
