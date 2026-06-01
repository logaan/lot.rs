//! Drawing the responsive, read-only LoT views.

use crate::app::{App, Mode};
use crate::markdown;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Paragraph, Wrap};
use ratatui::Frame;

/// Render one frame. Updates `app`'s cached layout rects for mouse hit-testing.
pub fn draw(f: &mut Frame, app: &mut App) {
    let area = f.area();
    app.mode = Mode::for_size(area.width, area.height);

    let [body, footer] = Layout::vertical([Constraint::Min(0), Constraint::Length(1)]).areas(area);
    render_footer(f, footer, app);

    // Small mode shows the detail as a full-screen overlay on demand.
    if app.mode == Mode::Small && app.overlay {
        render_detail(f, body, app);
        return;
    }
    // When not overlaid, the detail pane occupies no area in Small mode.
    app.detail_area = Rect::default();

    match app.mode {
        Mode::Wide => {
            let [tree, children, detail] = Layout::horizontal([
                Constraint::Percentage(34),
                Constraint::Percentage(30),
                Constraint::Percentage(36),
            ])
            .areas(body);
            render_tree(f, tree, app);
            render_children(f, children, app);
            render_detail(f, detail, app);
        }
        Mode::Normal => {
            let [tree, detail] =
                Layout::horizontal([Constraint::Percentage(45), Constraint::Percentage(55)])
                    .areas(body);
            render_tree(f, tree, app);
            render_detail(f, detail, app);
        }
        Mode::Tall => {
            let [tree, detail] =
                Layout::vertical([Constraint::Percentage(55), Constraint::Percentage(45)])
                    .areas(body);
            render_tree(f, tree, app);
            render_detail(f, detail, app);
        }
        Mode::Small => render_tree(f, body, app),
    }
}

/// The tree of every Thing, indented by depth, with the cursor highlighted.
fn render_tree(f: &mut Frame, area: Rect, app: &mut App) {
    let block = Block::bordered().title(" Things ");
    let inner = block.inner(area);
    app.tree_area = inner;

    let mut lines: Vec<Line> = Vec::new();
    if app.rows.is_empty() {
        lines.push(Line::styled(
            "  (this vault has no things yet)",
            Style::default().fg(Color::DarkGray),
        ));
    } else {
        let first = app.tree_first();
        let height = inner.height.max(1) as usize;
        for (i, row) in app.rows.iter().enumerate().skip(first).take(height) {
            let indent = "  ".repeat(row.depth);
            let mut line = Line::from(vec![
                Span::raw(indent),
                Span::styled(format!("{:>4} ", row.status), status_style(&row.status)),
                Span::raw(row.title.clone()),
            ]);
            if i == app.cursor {
                line = line.style(Style::default().add_modifier(Modifier::REVERSED));
            }
            lines.push(line);
        }
    }

    f.render_widget(Paragraph::new(lines).block(block), area);
}

/// The selected Thing's immediate sub-things (Wide mode only).
fn render_children(f: &mut Frame, area: Rect, app: &App) {
    let block = Block::bordered().title(" Sub-things ");
    let lines: Vec<Line> = match app.selected() {
        Some(row) if !row.children.is_empty() => row
            .children
            .iter()
            .filter_map(|&ci| app.rows.get(ci))
            .map(|child| {
                Line::from(vec![
                    Span::styled(format!("{:>4} ", child.status), status_style(&child.status)),
                    Span::raw(child.title.clone()),
                ])
            })
            .collect(),
        Some(_) => vec![Line::styled(
            "  (no sub-things)",
            Style::default().fg(Color::DarkGray),
        )],
        None => Vec::new(),
    };
    f.render_widget(
        Paragraph::new(lines)
            .block(block)
            .wrap(Wrap { trim: false }),
        area,
    );
}

/// The selected Thing's metadata and rendered markdown body.
fn render_detail(f: &mut Frame, area: Rect, app: &mut App) {
    let title = app
        .selected()
        .map(|r| format!(" {} ", r.title))
        .unwrap_or_else(|| " Detail ".to_string());
    let block = Block::bordered().title(title);
    let inner = block.inner(area);
    app.detail_area = inner;

    let mut lines: Vec<Line> = Vec::new();
    if let Some(row) = app.selected() {
        for (k, v) in &row.meta {
            lines.push(Line::from(vec![
                Span::styled(format!("{k}: "), Style::default().fg(Color::DarkGray)),
                Span::raw(v.clone()),
            ]));
        }
        if !row.meta.is_empty() {
            lines.push(Line::from(""));
        }
        lines.extend(markdown::render(&row.body));
    }
    app.detail_len = lines.len() as u16;

    f.render_widget(
        Paragraph::new(lines)
            .block(block)
            .wrap(Wrap { trim: false })
            .scroll((app.detail_scroll, 0)),
        area,
    );
}

/// The single-line help/status footer.
fn render_footer(f: &mut Frame, area: Rect, app: &App) {
    let mode = match app.mode {
        Mode::Wide => "wide",
        Mode::Normal => "normal",
        Mode::Tall => "tall",
        Mode::Small => "small",
    };
    let position = if app.rows.is_empty() {
        "0/0".to_string()
    } else {
        format!("{}/{}", app.cursor + 1, app.rows.len())
    };
    let help = if app.mode == Mode::Small {
        "j/k move · enter detail · esc back · q quit"
    } else {
        "j/k move · J/K scroll · mouse click+wheel · q quit"
    };
    let text = format!(" {}  ·  {help}    [{mode} · {position}] ", app.vault_path);
    f.render_widget(
        Paragraph::new(Line::styled(text, Style::default().fg(Color::DarkGray))),
        area,
    );
}

/// A distinct colour per update status.
fn status_style(status: &str) -> Style {
    let color = match status {
        "note" => Color::Blue,
        "work" => Color::Yellow,
        "info" => Color::Green,
        "done" => Color::DarkGray,
        _ => Color::Magenta,
    };
    Style::default().fg(color)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::app::App;
    use crate::model::Row;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    fn sample_app() -> App {
        let parent = Row {
            title: "Meetings".into(),
            status: "work".into(),
            depth: 0,
            children: vec![1],
            meta: vec![("status".into(), "work".into())],
            body: "# Meetings\n\nSpeak to Zoe about [design](https://canva.com).".into(),
        };
        let child = Row {
            title: "Zoe kickoff".into(),
            status: "note".into(),
            depth: 1,
            children: vec![],
            meta: vec![("status".into(), "note".into())],
            body: "# Zoe kickoff\n\n- one\n- two".into(),
        };
        App::new(vec![parent, child], "/tmp/vault".into())
    }

    /// Flatten a rendered frame to plain text for substring assertions.
    fn buffer_text(terminal: &Terminal<TestBackend>) -> String {
        let buf = terminal.backend().buffer();
        let area = *buf.area();
        let mut out = String::new();
        for y in 0..area.height {
            for x in 0..area.width {
                out.push_str(buf[(x, y)].symbol());
            }
            out.push('\n');
        }
        out
    }

    /// Render at a given size and return both the chosen mode and the text.
    fn render_at(width: u16, height: u16, overlay: bool) -> (Mode, String) {
        let backend = TestBackend::new(width, height);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = sample_app();
        app.overlay = overlay;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        (app.mode, buffer_text(&terminal))
    }

    #[test]
    fn wide_mode_shows_all_three_panes() {
        let (mode, text) = render_at(200, 50, false);
        assert_eq!(mode, Mode::Wide);
        assert!(text.contains("Things"));
        assert!(text.contains("Sub-things"));
        assert!(text.contains("Meetings"));
        // The detail pane renders the selected Thing's body and link URL.
        assert!(text.contains("Speak to Zoe"));
    }

    #[test]
    fn normal_mode_shows_tree_and_detail() {
        let (mode, text) = render_at(120, 50, false);
        assert_eq!(mode, Mode::Normal);
        assert!(text.contains("Things"));
        assert!(text.contains("Meetings"));
    }

    #[test]
    fn tall_mode_stacks_tree_over_detail() {
        let (mode, text) = render_at(80, 40, false);
        assert_eq!(mode, Mode::Tall);
        assert!(text.contains("Things"));
        assert!(text.contains("Meetings"));
    }

    #[test]
    fn small_mode_shows_tree_then_overlay_detail() {
        let (mode, tree_text) = render_at(80, 20, false);
        assert_eq!(mode, Mode::Small);
        assert!(tree_text.contains("Things"));
        // With the overlay open, the detail body is shown instead.
        let (_, overlay_text) = render_at(80, 20, true);
        assert!(overlay_text.contains("Speak to Zoe"));
    }
}
