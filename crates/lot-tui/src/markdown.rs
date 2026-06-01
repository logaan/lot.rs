//! Render a Thing's markdown body into styled [`ratatui`] lines.
//!
//! This is deliberately a small, forgiving renderer rather than a full
//! CommonMark-to-terminal engine: it covers the constructs LoT actually uses
//! (headings, paragraphs, bullet/ordered lists, blockquotes, inline/fenced
//! code, emphasis, links, and thematic breaks) and ignores the rest.

use pulldown_cmark::{Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};

/// Render `input` (the merged body of a Thing) into wrapped-ready lines.
pub fn render(input: &str) -> Vec<Line<'static>> {
    let pre = preprocess(input);
    let mut opts = Options::empty();
    opts.insert(Options::ENABLE_STRIKETHROUGH);
    let parser = Parser::new_ext(&pre, opts);

    let mut r = Renderer::default();
    for event in parser {
        r.event(event);
    }
    r.finish()
}

/// A Thing's computed state glues each update under a header bracketed by two
/// 80-dash rules:
///
/// ```text
/// --------------------------------------------------------------------------------
/// 001 - note - 2026-... - lot:...
/// --------------------------------------------------------------------------------
/// ```
///
/// Fed to CommonMark as-is the dash rules turn the meta line into a setext
/// heading. Replacing each long dash run with a blank-padded `---` makes the
/// rules render as thematic breaks and the meta line as an ordinary paragraph.
fn preprocess(input: &str) -> String {
    let mut out = String::with_capacity(input.len() + 16);
    for line in input.lines() {
        let trimmed = line.trim_end();
        if trimmed.len() >= 10 && trimmed.chars().all(|c| c == '-') {
            out.push_str("\n---\n");
        } else {
            out.push_str(line);
            out.push('\n');
        }
    }
    out
}

#[derive(Default)]
struct Renderer {
    lines: Vec<Line<'static>>,
    spans: Vec<Span<'static>>,
    bold: u32,
    italic: u32,
    strike: u32,
    /// Stack of list contexts; `Some(n)` is an ordered list's next number,
    /// `None` an unordered list.
    lists: Vec<Option<u64>>,
    quote: u32,
    link: Option<String>,
    in_code_block: bool,
    heading: u32,
}

impl Renderer {
    fn inline_style(&self) -> Style {
        let mut s = Style::default();
        if self.bold > 0 || self.heading > 0 {
            s = s.add_modifier(Modifier::BOLD);
        }
        if self.italic > 0 {
            s = s.add_modifier(Modifier::ITALIC);
        }
        if self.strike > 0 {
            s = s.add_modifier(Modifier::CROSSED_OUT);
        }
        if self.heading == 1 {
            s = s.add_modifier(Modifier::UNDERLINED);
        }
        if self.link.is_some() {
            s = s.fg(Color::Cyan).add_modifier(Modifier::UNDERLINED);
        }
        s
    }

    fn push(&mut self, text: impl Into<String>, style: Style) {
        self.spans.push(Span::styled(text.into(), style));
    }

    /// Flush the in-progress spans as a line, prepending any blockquote marker.
    fn end_line(&mut self) {
        let mut spans = std::mem::take(&mut self.spans);
        for _ in 0..self.quote {
            spans.insert(0, Span::styled("▌ ", Style::default().fg(Color::DarkGray)));
        }
        self.lines.push(Line::from(spans));
    }

    /// Ensure a blank separator before a new block (but not at the very top).
    fn blank_before_block(&mut self) {
        if !self.spans.is_empty() {
            self.end_line();
        }
        match self.lines.last() {
            None => {}
            Some(last) if last.spans.is_empty() => {}
            Some(_) => self.lines.push(Line::from("")),
        }
    }

    fn event(&mut self, event: Event) {
        match event {
            Event::Start(tag) => self.start(tag),
            Event::End(tag) => self.end(tag),
            Event::Text(text) => {
                if self.in_code_block {
                    let style = Style::default().fg(Color::Rgb(180, 180, 180));
                    for (i, part) in text.split('\n').enumerate() {
                        if i > 0 {
                            self.end_line();
                        }
                        self.push(format!("  {part}"), style);
                    }
                } else {
                    let style = self.inline_style();
                    self.push(text.into_string(), style);
                }
            }
            Event::Code(code) => {
                let style = self.inline_style().fg(Color::Yellow);
                self.push(code.into_string(), style);
            }
            Event::SoftBreak => self.spans.push(Span::raw(" ")),
            Event::HardBreak => self.end_line(),
            Event::Rule => {
                self.blank_before_block();
                self.lines.push(Line::styled(
                    "─".repeat(40),
                    Style::default().fg(Color::DarkGray),
                ));
                self.lines.push(Line::from(""));
            }
            _ => {}
        }
    }

    fn start(&mut self, tag: Tag) {
        match tag {
            Tag::Paragraph => self.blank_before_block(),
            Tag::Heading { level, .. } => {
                self.blank_before_block();
                self.heading = level as u32;
            }
            Tag::BlockQuote(_) => {
                self.blank_before_block();
                self.quote += 1;
            }
            Tag::CodeBlock(_) => {
                self.blank_before_block();
                self.in_code_block = true;
            }
            Tag::List(start) => self.lists.push(start),
            Tag::Item => {
                if !self.spans.is_empty() {
                    self.end_line();
                }
                let depth = self.lists.len().saturating_sub(1);
                let indent = "  ".repeat(depth);
                let marker = match self.lists.last_mut() {
                    Some(Some(n)) => {
                        let m = format!("{n}. ");
                        *n += 1;
                        m
                    }
                    _ => "• ".to_string(),
                };
                self.push(
                    format!("{indent}{marker}"),
                    Style::default().fg(Color::DarkGray),
                );
            }
            Tag::Emphasis => self.italic += 1,
            Tag::Strong => self.bold += 1,
            Tag::Strikethrough => self.strike += 1,
            Tag::Link { dest_url, .. } => self.link = Some(dest_url.into_string()),
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph | TagEnd::Item | TagEnd::Heading(_) => self.end_line(),
            TagEnd::BlockQuote(_) => {
                if !self.spans.is_empty() {
                    self.end_line();
                }
                self.quote = self.quote.saturating_sub(1);
            }
            TagEnd::CodeBlock => {
                if !self.spans.is_empty() {
                    self.end_line();
                }
                self.in_code_block = false;
            }
            TagEnd::List(_) => {
                self.lists.pop();
            }
            TagEnd::Emphasis => self.italic = self.italic.saturating_sub(1),
            TagEnd::Strong => self.bold = self.bold.saturating_sub(1),
            TagEnd::Strikethrough => self.strike = self.strike.saturating_sub(1),
            TagEnd::Link => {
                // Show the URL plainly so the terminal can make it clickable.
                if let Some(url) = self.link.take() {
                    self.push(format!(" ({url})"), Style::default().fg(Color::DarkGray));
                }
            }
            _ => {}
        }
        if matches!(tag, TagEnd::Heading(_)) {
            self.heading = 0;
        }
    }

    fn finish(mut self) -> Vec<Line<'static>> {
        if !self.spans.is_empty() {
            self.end_line();
        }
        self.lines
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Collapse a rendered line back to its plain text for assertions.
    fn plain(line: &Line) -> String {
        line.spans.iter().map(|s| s.content.as_ref()).collect()
    }

    #[test]
    fn renders_heading_and_paragraph() {
        let lines = render("# Title\n\nHello world");
        let text: Vec<String> = lines.iter().map(plain).collect();
        assert!(text.iter().any(|l| l == "Title"));
        assert!(text.iter().any(|l| l == "Hello world"));
    }

    #[test]
    fn renders_bullets_with_markers() {
        let lines = render("- one\n- two");
        let text: Vec<String> = lines.iter().map(plain).collect();
        assert!(text.iter().any(|l| l == "• one"));
        assert!(text.iter().any(|l| l == "• two"));
    }

    #[test]
    fn link_shows_url_for_terminal_click_through() {
        let lines = render("[Canva](https://canva.com)");
        let joined: String = lines.iter().map(plain).collect();
        assert!(joined.contains("Canva"));
        assert!(joined.contains("(https://canva.com)"));
    }

    #[test]
    fn dash_rules_become_thematic_breaks_not_headings() {
        // The computed-state separators must not swallow the meta line as a
        // setext heading; the meta text should survive as its own line.
        let input = "----------------------------------------\n\
                     001 - note - ts - lot:abc\n\
                     ----------------------------------------\n\n\
                     Body text";
        let lines = render(input);
        let text: Vec<String> = lines.iter().map(plain).collect();
        assert!(text.iter().any(|l| l.contains("001 - note - ts - lot:abc")));
        assert!(text.iter().any(|l| l == "Body text"));
    }
}
