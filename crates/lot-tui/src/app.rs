//! Application state and input handling for the LoT TUI.

use crate::command::{CommandNode, Outcome, Palette};
use crate::links::Link;
use crate::model::Row;
use crate::select::{self, Selection};
use ratatui::crossterm::event::{
    KeyCode, KeyEvent, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::layout::{Position, Rect};
use std::time::Instant;

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
    /// The `lot` command tree, discovered once at startup, that the palette
    /// navigates.
    pub commands: CommandNode,
    /// Index into `rows` of the highlighted Thing.
    pub cursor: usize,
    /// Vertical scroll offset of the detail pane.
    pub detail_scroll: u16,
    /// Number of rendered detail lines (for clamping `detail_scroll`).
    pub detail_len: u16,
    /// In `Small` mode, whether the detail overlay is open.
    pub overlay: bool,
    /// The open command palette, if any (opened with <kbd>Space</kbd>).
    pub palette: Option<Palette>,
    /// Whether the `?` shortcut-tree overlay is open.
    pub help_overlay: bool,
    /// Set to the `lot` sub-command args when the user invokes a command;
    /// consumed by the event loop, which suspends the TUI to run it.
    pub invoke: Option<Vec<String>>,
    /// Set when the user presses <kbd>Ctrl-Z</kbd>; consumed by the event loop,
    /// which sends the process to the background like any CLI app.
    pub suspend: bool,
    pub mode: Mode,
    pub quit: bool,
    /// Inner rect of the tree list (set each draw, used for mouse hit-testing).
    pub tree_area: Rect,
    /// Inner rect of the detail pane (set each draw).
    pub detail_area: Rect,
    /// `lot:` ids visible in the detail pane, in screen coordinates (set each
    /// draw); a click on one selects that Thing.
    pub detail_links: Vec<Link>,
    /// Mouse selection over the detail pane, in screen coordinates. Cleared
    /// whenever the content beneath it could move.
    pub selection: Option<Selection>,
    /// Set on mouse-up over a non-empty selection; consumed by the next draw,
    /// which extracts the selected text from the rendered buffer.
    pub copy_request: bool,
    /// Extracted selection text awaiting the event loop's clipboard write.
    pub pending_copy: Option<String>,
    /// A transient footer message (e.g. "copied …") and when it was set.
    pub feedback: Option<(String, Instant)>,
}

impl App {
    pub fn new(rows: Vec<Row>, vault_path: String, commands: CommandNode) -> Self {
        Self {
            rows,
            vault_path,
            commands,
            cursor: 0,
            detail_scroll: 0,
            detail_len: 0,
            overlay: false,
            palette: None,
            help_overlay: false,
            invoke: None,
            suspend: false,
            mode: Mode::Normal,
            quit: false,
            tree_area: Rect::default(),
            detail_area: Rect::default(),
            detail_links: Vec::new(),
            selection: None,
            copy_request: false,
            pending_copy: None,
            feedback: None,
        }
    }

    /// The Thing currently under the cursor, if any.
    pub fn selected(&self) -> Option<&Row> {
        self.rows.get(self.cursor)
    }

    /// The id of the Thing under the cursor, if any (for `LOT_THING_ID`).
    pub fn selected_id(&self) -> Option<&str> {
        self.selected().map(|r| r.id.as_str())
    }

    /// Replace the rows (after a reload) and re-validate UI state, keeping the
    /// same Thing selected by id where possible. The on-disk state always wins.
    pub fn reload(&mut self, rows: Vec<Row>) {
        let prev_id = self.selected_id().map(str::to_string);
        self.rows = rows;
        self.reconcile(prev_id.as_deref());
    }

    /// Validate UI state against the current rows: re-resolve the selection by
    /// id (clamping when it vanished), and reset scrolling/overlay when empty.
    /// Called after every reload so a changed vault can't leave a dangling
    /// selection.
    fn reconcile(&mut self, prev_id: Option<&str>) {
        self.selection = None;
        if self.rows.is_empty() {
            self.cursor = 0;
            self.detail_scroll = 0;
            self.overlay = false;
            return;
        }
        let last = self.rows.len() - 1;
        self.cursor = prev_id
            .and_then(|id| self.rows.iter().position(|r| r.id == id))
            .unwrap_or_else(|| self.cursor.min(last));
        self.detail_scroll = 0;
    }

    /// Move the cursor to the Thing with `id`, if one is present. Used after a
    /// command reports a Thing by printing its id (e.g. `lot thing new`) so the
    /// TUI jumps to it. Ids that match no row (e.g. an update id) are ignored,
    /// leaving the current selection untouched.
    pub fn focus_id(&mut self, id: &str) {
        if let Some(i) = self.rows.iter().position(|r| r.id == id) {
            if i != self.cursor {
                self.cursor = i;
                self.detail_scroll = 0;
            }
        }
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
            self.selection = None;
        }
    }

    fn scroll_detail(&mut self, delta: isize) {
        let max = self.detail_len.saturating_sub(1);
        let next = ((self.detail_scroll as isize + delta).clamp(0, max as isize)) as u16;
        if next != self.detail_scroll {
            self.detail_scroll = next;
            // The text has moved out from under a screen-anchored selection.
            self.selection = None;
        }
    }

    pub fn on_key(&mut self, key: KeyEvent) {
        // Like a terminal, any keypress drops the mouse selection.
        self.selection = None;
        // Ctrl-C always quits, even from the palette or an overlay.
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('c') {
            self.quit = true;
            return;
        }
        // Ctrl-Z suspends to the background, like any CLI app. Raw mode means the
        // terminal hands us the keypress instead of generating SIGTSTP, so we
        // flag it here and the event loop raises the signal itself.
        if key.modifiers.contains(KeyModifiers::CONTROL) && key.code == KeyCode::Char('z') {
            self.suspend = true;
            return;
        }
        // The `?` shortcut-tree overlay swallows keys until dismissed.
        if self.help_overlay {
            if matches!(
                key.code,
                KeyCode::Esc | KeyCode::Char('?') | KeyCode::Char('q')
            ) {
                self.help_overlay = false;
            }
            return;
        }
        // While the palette is open it owns the keyboard.
        if self.palette.is_some() {
            self.on_palette_key(key);
            return;
        }
        match key.code {
            KeyCode::Char('q') => self.quit = true,
            // Space opens the command palette; `?` shows the shortcut tree.
            KeyCode::Char(' ') => self.palette = Some(Palette::new()),
            KeyCode::Char('?') => self.help_overlay = true,
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

    /// Route a key to the open palette and apply its outcome.
    fn on_palette_key(&mut self, key: KeyEvent) {
        // Borrows `self.palette` (mut) and `self.commands` (shared) disjointly.
        let outcome = self.palette.as_mut().expect("palette is open").on_key(
            key,
            &self.commands,
            Instant::now(),
        );
        match outcome {
            Outcome::None => {}
            Outcome::Close => self.palette = None,
            Outcome::Invoke(args) => {
                self.invoke = Some(args);
                self.palette = None;
            }
            // Keep the palette open beneath the `?` overlay so navigation
            // resumes once it's dismissed.
            Outcome::OpenHelp => self.help_overlay = true,
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
            MouseEventKind::Down(MouseButton::Left) => self.on_left_down(pos),
            MouseEventKind::Drag(MouseButton::Left) => {
                if let Some(sel) = self.selection.as_mut().filter(|s| s.dragging) {
                    sel.head = select::clamp_to(pos, self.detail_area);
                }
            }
            MouseEventKind::Up(MouseButton::Left) => {
                if let Some(sel) = self.selection.as_mut().filter(|s| s.dragging) {
                    sel.dragging = false;
                    if sel.is_empty() {
                        // A click that never moved: not a selection, but it may
                        // be on a `lot:` id — follow it to its Thing.
                        let clicked = sel.anchor;
                        self.selection = None;
                        self.follow_link(clicked);
                    } else {
                        self.copy_request = true;
                    }
                }
            }
            _ => {}
        }
    }

    /// A left-button press either starts a text selection (over the detail
    /// pane) or selects the clicked Thing (over the tree). Either way any
    /// finished selection is dropped, as in a terminal.
    fn on_left_down(&mut self, pos: Position) {
        self.selection = None;
        if self.detail_area.contains(pos) && self.palette.is_none() && !self.help_overlay {
            self.selection = Some(Selection::begin(pos));
        } else if self.tree_area.contains(pos) {
            let offset = (pos.y - self.tree_area.y) as usize;
            let target = self.tree_first() + offset;
            if target < self.rows.len() {
                self.cursor = target;
                self.detail_scroll = 0;
            }
        }
    }

    /// Follow a `lot:` id clicked in the detail pane: select its Thing if the
    /// vault has one, otherwise say so in the footer (the id may name an
    /// update, or a Thing from another vault). A click anywhere else is a
    /// no-op.
    fn follow_link(&mut self, pos: Position) {
        let Some(link) = self.detail_links.iter().find(|l| l.contains(pos)) else {
            return;
        };
        let id = link.id.clone();
        if self.rows.iter().any(|r| r.id == id) {
            self.focus_id(&id);
        } else {
            self.feedback = Some((format!("no thing {id} in this vault"), Instant::now()));
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
                id: format!("lot:{i}"),
                title: format!("Thing {i}"),
                status: "note".into(),
                depth: 0,
                children: Vec::new(),
                meta: Vec::new(),
                body: String::new(),
            })
            .collect();
        App::new(rows, "/tmp/vault".into(), CommandNode::default())
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
    fn space_opens_palette_and_escape_closes_it() {
        let mut app = app_with(1);
        assert!(app.palette.is_none());
        app.on_key(KeyEvent::from(KeyCode::Char(' ')));
        assert!(app.palette.is_some());
        // With an empty nav path, Esc closes the palette.
        app.on_key(KeyEvent::from(KeyCode::Esc));
        assert!(app.palette.is_none());
    }

    #[test]
    fn ctrl_z_requests_suspend_without_quitting() {
        let mut app = app_with(1);
        app.on_key(KeyEvent::new(KeyCode::Char('z'), KeyModifiers::CONTROL));
        assert!(app.suspend);
        assert!(!app.quit);
    }

    #[test]
    fn question_mark_toggles_help_overlay() {
        let mut app = app_with(1);
        app.on_key(KeyEvent::from(KeyCode::Char('?')));
        assert!(app.help_overlay);
        // Any dismiss key closes it and is swallowed (no quit).
        app.on_key(KeyEvent::from(KeyCode::Char('q')));
        assert!(!app.help_overlay);
        assert!(!app.quit);
    }

    #[test]
    fn reload_keeps_selection_by_id() {
        let mut app = app_with(3);
        app.cursor = 2; // "lot:2"
                        // Reload with the same Thing now at a different index.
        let rows = vec![
            Row {
                id: "lot:9".into(),
                title: "New".into(),
                status: "note".into(),
                depth: 0,
                children: Vec::new(),
                meta: Vec::new(),
                body: String::new(),
            },
            Row {
                id: "lot:2".into(),
                title: "Thing 2".into(),
                status: "note".into(),
                depth: 0,
                children: Vec::new(),
                meta: Vec::new(),
                body: String::new(),
            },
        ];
        app.reload(rows);
        assert_eq!(app.cursor, 1, "selection follows the id, not the index");
    }

    #[test]
    fn reload_clamps_when_selection_vanishes() {
        let mut app = app_with(3);
        app.cursor = 2;
        // The selected Thing (lot:2) is gone; only one row remains.
        let rows = vec![Row {
            id: "lot:0".into(),
            title: "Thing 0".into(),
            status: "note".into(),
            depth: 0,
            children: Vec::new(),
            meta: Vec::new(),
            body: String::new(),
        }];
        app.reload(rows);
        assert_eq!(app.cursor, 0);
    }

    #[test]
    fn focus_id_jumps_to_match_and_ignores_misses() {
        let mut app = app_with(3);
        app.detail_scroll = 4;
        // A matching id moves the cursor and resets the detail scroll.
        app.focus_id("lot:2");
        assert_eq!(app.cursor, 2);
        assert_eq!(app.detail_scroll, 0);
        // An id no row carries (e.g. an update id) leaves the selection put.
        app.focus_id("lot:does-not-exist");
        assert_eq!(app.cursor, 2);
    }

    fn mouse(kind: MouseEventKind, x: u16, y: u16) -> MouseEvent {
        MouseEvent {
            kind,
            column: x,
            row: y,
            modifiers: KeyModifiers::NONE,
        }
    }

    #[test]
    fn dragging_over_the_detail_pane_selects_then_requests_a_copy() {
        let mut app = app_with(1);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 12, 1));
        assert!(app.selection.is_some());
        // Dragging past the pane's edge clamps to it.
        app.on_mouse(mouse(MouseEventKind::Drag(MouseButton::Left), 99, 2));
        assert_eq!(app.selection.expect("dragging").head, Position::new(29, 2));
        app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 99, 2));
        assert!(app.copy_request, "release requests a copy");
        assert!(!app.selection.expect("kept for the draw").dragging);
    }

    #[test]
    fn a_click_without_a_drag_selects_nothing() {
        let mut app = app_with(1);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 12, 1));
        app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 12, 1));
        assert!(app.selection.is_none());
        assert!(!app.copy_request);
    }

    #[test]
    fn clicking_a_lot_id_in_the_detail_pane_selects_that_thing() {
        let mut app = app_with(3);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.detail_links = vec![Link {
            y: 1,
            x0: 12,
            x1: 16,
            id: "lot:2".into(),
        }];
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 13, 1));
        app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 13, 1));
        assert_eq!(app.cursor, 2, "the click followed the id to its Thing");
        assert!(app.selection.is_none());
        assert!(!app.copy_request);
    }

    #[test]
    fn clicking_an_unknown_lot_id_reports_in_the_footer() {
        let mut app = app_with(3);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.detail_links = vec![Link {
            y: 1,
            x0: 12,
            x1: 16,
            id: "lot:unknown".into(),
        }];
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 13, 1));
        app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 13, 1));
        assert_eq!(app.cursor, 0, "an unknown id moves nothing");
        let (message, _) = app.feedback.expect("the footer explains the miss");
        assert!(message.contains("lot:unknown"), "message: {message}");
    }

    #[test]
    fn clicking_beside_a_link_is_not_a_jump() {
        let mut app = app_with(3);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.detail_links = vec![Link {
            y: 1,
            x0: 12,
            x1: 16,
            id: "lot:2".into(),
        }];
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 18, 1));
        app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 18, 1));
        assert_eq!(app.cursor, 0);
        assert!(app.feedback.is_none());
    }

    #[test]
    fn keys_scrolling_and_tree_clicks_clear_a_selection() {
        let mut app = app_with(3);
        app.detail_area = Rect::new(10, 0, 20, 10);
        app.tree_area = Rect::new(0, 0, 10, 10);
        let select = |app: &mut App| {
            app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 12, 1));
            app.on_mouse(mouse(MouseEventKind::Drag(MouseButton::Left), 15, 1));
            app.on_mouse(mouse(MouseEventKind::Up(MouseButton::Left), 15, 1));
            assert!(app.selection.is_some());
        };
        select(&mut app);
        app.on_key(KeyEvent::from(KeyCode::Char('j')));
        assert!(app.selection.is_none(), "a keypress clears the selection");
        select(&mut app);
        app.on_mouse(mouse(MouseEventKind::Down(MouseButton::Left), 2, 1));
        assert!(app.selection.is_none(), "a tree click clears the selection");
        select(&mut app);
        app.reload(Vec::new());
        assert!(app.selection.is_none(), "a reload clears the selection");
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
