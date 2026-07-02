//! Drawing the responsive LoT views and the command palette.

use crate::app::{App, Mode};
use crate::markdown;
use crate::select;
use ratatui::layout::{Constraint, Flex, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Clear, Paragraph, Wrap};
use ratatui::Frame;
use std::time::Duration;

/// How long a transient footer message (e.g. "copied …") stays visible.
const FEEDBACK_TTL: Duration = Duration::from_millis(2500);

/// Render one frame. Updates `app`'s cached layout rects for mouse hit-testing.
pub fn draw(f: &mut Frame, app: &mut App) {
    let area = f.area();
    app.mode = Mode::for_size(area.width, area.height);

    let [body, footer] = Layout::vertical([Constraint::Min(0), Constraint::Length(1)]).areas(area);
    render_footer(f, footer, app);
    render_body(f, body, app);

    // Paint the mouse selection over the freshly drawn detail pane.
    if let Some(sel) = &app.selection {
        select::highlight(f.buffer_mut(), app.detail_area, sel);
    }

    // Overlays paint on top of whatever layout is beneath them.
    if app.palette.is_some() {
        render_palette(f, area, app);
    }
    if app.help_overlay {
        render_help(f, area, app);
    }

    // Satisfy a pending copy from this very buffer, so what is copied is
    // exactly what is on screen (post-wrapping, post-scroll). The event loop
    // moves `pending_copy` to the system clipboard.
    if app.copy_request {
        app.copy_request = false;
        if let Some(sel) = &app.selection {
            app.pending_copy = Some(select::extract(f.buffer_mut(), app.detail_area, sel));
        }
    }
}

/// Render the main body (tree + detail) for the current responsive layout.
fn render_body(f: &mut Frame, body: Rect, app: &mut App) {
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

/// The single-line help/status footer, or a transient feedback message.
fn render_footer(f: &mut Frame, area: Rect, app: &App) {
    if let Some((msg, at)) = &app.feedback {
        if at.elapsed() < FEEDBACK_TTL {
            f.render_widget(
                Paragraph::new(Line::styled(
                    format!(" {msg}"),
                    Style::default().fg(Color::Yellow),
                )),
                area,
            );
            return;
        }
    }
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
        "j/k move · enter detail · space cmds · ? help · q quit"
    } else {
        "j/k move · J/K scroll · space cmds · ? help · q quit"
    };
    let text = format!(" {}  ·  {help}    [{mode} · {position}] ", app.vault_path);
    f.render_widget(
        Paragraph::new(Line::styled(text, Style::default().fg(Color::DarkGray))),
        area,
    );
}

/// The command palette popup: the current level's commands (or a chooser),
/// with a breadcrumb title and a key-hint footer.
fn render_palette(f: &mut Frame, area: Rect, app: &App) {
    let palette = app.palette.as_ref().expect("palette is open");
    let root = &app.commands;
    let children = palette.current_children(root);

    let mut lines: Vec<Line> = Vec::new();
    if let Some(chooser) = &palette.chooser {
        lines.push(Line::styled(
            "  several commands match — ↑/↓ then Enter:",
            Style::default().fg(Color::DarkGray),
        ));
        for (row, &ci) in chooser.candidates.iter().enumerate() {
            let name = children.get(ci).map(|c| c.name.as_str()).unwrap_or("?");
            let mut line = Line::from(format!("    {name}"));
            if row == chooser.selected {
                line = line.style(Style::default().add_modifier(Modifier::REVERSED));
            }
            lines.push(line);
        }
    } else if children.is_empty() {
        lines.push(Line::styled(
            "  (no sub-commands — Enter runs it, Backspace goes back)",
            Style::default().fg(Color::DarkGray),
        ));
    } else {
        for child in children {
            lines.push(command_line(child));
        }
    }
    lines.push(Line::from(""));
    lines.push(Line::styled(
        "  letter navigate · Enter run · Backspace up · Esc clear/close · ? tree",
        Style::default().fg(Color::DarkGray),
    ));

    let title = format!(" {} ", palette.breadcrumb(root));
    let height = (lines.len() as u16 + 2).min(area.height);
    let popup = centered(area, 72, height);
    f.render_widget(Clear, popup);
    f.render_widget(
        Paragraph::new(lines)
            .block(Block::bordered().title(title))
            .wrap(Wrap { trim: false }),
        popup,
    );
}

/// One `[key] name — about` line for a command in the palette.
fn command_line(child: &crate::command::CommandNode) -> Line<'static> {
    let key = child.name.chars().next().unwrap_or(' ');
    let mut spans = vec![
        Span::styled(
            format!("  {key}  "),
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(child.name.clone()),
    ];
    if let Some(about) = &child.about {
        spans.push(Span::styled(
            format!("  — {about}"),
            Style::default().fg(Color::DarkGray),
        ));
    }
    Line::from(spans)
}

/// The `?` overlay: the whole command tree with each node's first-letter key.
fn render_help(f: &mut Frame, area: Rect, app: &App) {
    let mut lines = vec![
        Line::styled(
            "  Command shortcuts — type a command's first letter to navigate",
            Style::default()
                .fg(Color::Yellow)
                .add_modifier(Modifier::BOLD),
        ),
        Line::from(""),
    ];
    push_tree(&app.commands, 0, &mut lines);
    lines.push(Line::from(""));
    lines.push(Line::styled(
        "  Space opens the palette · Esc / q / ? closes this",
        Style::default().fg(Color::DarkGray),
    ));

    let height = (lines.len() as u16 + 2).min(area.height);
    let popup = centered(area, 80, height);
    f.render_widget(Clear, popup);
    f.render_widget(
        Paragraph::new(lines)
            .block(Block::bordered().title(" Keyboard shortcuts "))
            .wrap(Wrap { trim: false }),
        popup,
    );
}

/// Append `node`'s sub-commands (recursively) as indented shortcut lines.
fn push_tree(node: &crate::command::CommandNode, depth: usize, lines: &mut Vec<Line<'static>>) {
    for child in &node.subcommands {
        let key = child.name.chars().next().unwrap_or(' ');
        let mut spans = vec![
            Span::raw("  ".repeat(depth + 1)),
            Span::styled(format!("{key}  "), Style::default().fg(Color::Yellow)),
            Span::raw(child.name.clone()),
        ];
        if let Some(about) = &child.about {
            spans.push(Span::styled(
                format!("  — {about}"),
                Style::default().fg(Color::DarkGray),
            ));
        }
        lines.push(Line::from(spans));
        push_tree(child, depth + 1, lines);
    }
}

/// A centered popup rect: `percent_x` wide and `height` rows tall.
fn centered(area: Rect, percent_x: u16, height: u16) -> Rect {
    let [row] = Layout::vertical([Constraint::Length(height)])
        .flex(Flex::Center)
        .areas(area);
    let [col] = Layout::horizontal([Constraint::Percentage(percent_x)])
        .flex(Flex::Center)
        .areas(row);
    col
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
    use crate::command::{CommandNode, Palette};
    use crate::model::Row;
    use ratatui::backend::TestBackend;
    use ratatui::Terminal;

    /// A small command tree mirroring `lot`'s real shape for palette rendering.
    fn sample_commands() -> CommandNode {
        CommandNode {
            name: "lot".into(),
            about: None,
            subcommands: vec![
                CommandNode {
                    name: "thing".into(),
                    about: Some("Work with Things".into()),
                    subcommands: vec![CommandNode {
                        name: "new".into(),
                        about: Some("Create a new Thing".into()),
                        subcommands: vec![],
                    }],
                },
                CommandNode {
                    name: "ui".into(),
                    about: Some("Launch the terminal UI".into()),
                    subcommands: vec![],
                },
            ],
        }
    }

    fn sample_app() -> App {
        let parent = Row {
            id: "lot:parent".into(),
            title: "Meetings".into(),
            status: "work".into(),
            depth: 0,
            children: vec![1],
            meta: vec![("status".into(), "work".into())],
            body: "# Meetings\n\nSpeak to Zoe about [design](https://canva.com).".into(),
        };
        let child = Row {
            id: "lot:child".into(),
            title: "Zoe kickoff".into(),
            status: "note".into(),
            depth: 1,
            children: vec![],
            meta: vec![("status".into(), "note".into())],
            body: "# Zoe kickoff\n\n- one\n- two".into(),
        };
        App::new(vec![parent, child], "/tmp/vault".into(), sample_commands())
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
    fn palette_overlay_lists_current_commands() {
        let backend = TestBackend::new(120, 40);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = sample_app();
        app.palette = Some(Palette::new());
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let text = buffer_text(&terminal);
        // The top-level commands and the breadcrumb title are shown.
        assert!(text.contains("thing"));
        assert!(text.contains("ui"));
        assert!(text.contains("Enter run"));
    }

    #[test]
    fn help_overlay_shows_the_shortcut_tree() {
        let backend = TestBackend::new(120, 40);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = sample_app();
        app.help_overlay = true;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let text = buffer_text(&terminal);
        assert!(text.contains("Keyboard shortcuts"));
        // A nested command (thing -> new) appears in the tree.
        assert!(text.contains("new"));
    }

    #[test]
    fn copy_request_extracts_the_selected_screen_text() {
        let backend = TestBackend::new(120, 40);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = sample_app();
        // The first draw computes the detail pane's rect for hit-testing.
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let area = app.detail_area;
        assert!(area.width > 0, "normal mode has a detail pane");
        // Select the whole pane and request a copy: the next draw extracts
        // the rendered text (and paints the selection reversed).
        app.selection = Some(crate::select::Selection {
            anchor: ratatui::layout::Position::new(area.left(), area.top()),
            head: ratatui::layout::Position::new(area.right() - 1, area.bottom() - 1),
            dragging: false,
        });
        app.copy_request = true;
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        let copied = app.pending_copy.take().expect("draw extracted the copy");
        assert!(copied.contains("Speak to Zoe"), "copied: {copied:?}");
        assert!(!app.copy_request, "the request is consumed");
        let buf = terminal.backend().buffer();
        assert!(buf[(area.left(), area.top())]
            .modifier
            .contains(Modifier::REVERSED));
    }

    #[test]
    fn footer_shows_transient_feedback() {
        let backend = TestBackend::new(120, 40);
        let mut terminal = Terminal::new(backend).unwrap();
        let mut app = sample_app();
        app.feedback = Some(("copied 12 chars".into(), std::time::Instant::now()));
        terminal.draw(|f| draw(f, &mut app)).unwrap();
        assert!(buffer_text(&terminal).contains("copied 12 chars"));
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
