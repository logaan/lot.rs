use crate::error::Result;
use serde_yaml_ng::{Mapping, Value};

/// A parsed update/markdown document: YAML frontmatter plus a markdown body.
///
/// The frontmatter is stored as an ordered [`Mapping`] so that round-tripping a
/// document preserves the order in which keys were written.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Document {
    pub frontmatter: Mapping,
    pub body: String,
}

impl Document {
    /// Build a document from a mapping and a body.
    pub fn new(frontmatter: Mapping, body: impl Into<String>) -> Self {
        Self {
            frontmatter,
            body: body.into(),
        }
    }

    /// Parse a document from raw file text.
    ///
    /// Frontmatter is delimited by a leading `---` line and a closing `---`
    /// line. When no frontmatter block is present the whole input is treated as
    /// the body.
    pub fn parse(raw: &str) -> Result<Self> {
        // Normalise to make the leading-delimiter check simple.
        let trimmed_start = raw.strip_prefix('\u{feff}').unwrap_or(raw);

        let Some(rest) = strip_opening_delimiter(trimmed_start) else {
            return Ok(Document {
                frontmatter: Mapping::new(),
                body: raw.to_string(),
            });
        };

        // Find the closing `---` (or `...`) line.
        if let Some((yaml, body)) = split_closing_delimiter(rest) {
            let frontmatter = if yaml.trim().is_empty() {
                Mapping::new()
            } else {
                match serde_yaml_ng::from_str::<Value>(yaml)? {
                    Value::Mapping(m) => m,
                    Value::Null => Mapping::new(),
                    other => {
                        // Frontmatter that isn't a mapping is unusual; keep it
                        // under a synthetic key rather than failing.
                        let mut m = Mapping::new();
                        m.insert(Value::from("value"), other);
                        m
                    }
                }
            };
            Ok(Document {
                frontmatter,
                body: body.to_string(),
            })
        } else {
            // Opening delimiter but no closing one: treat as plain body.
            Ok(Document {
                frontmatter: Mapping::new(),
                body: raw.to_string(),
            })
        }
    }

    /// Render the document back to text with a `---` delimited frontmatter
    /// block followed by the body.
    pub fn render(&self) -> Result<String> {
        let mut out = String::new();
        if !self.frontmatter.is_empty() {
            out.push_str("---\n");
            let yaml = serde_yaml_ng::to_string(&Value::Mapping(self.frontmatter.clone()))?;
            out.push_str(&yaml);
            if !yaml.ends_with('\n') {
                out.push('\n');
            }
            out.push_str("---\n");
        }
        out.push_str(&self.body);
        Ok(out)
    }

    /// Render the document as a single YAML document: every frontmatter key
    /// followed by a `body` key holding the markdown body. This is the
    /// structured counterpart to [`Document::render`]'s markdown output.
    pub fn to_yaml(&self) -> Result<String> {
        let mut map = self.frontmatter.clone();
        map.insert(Value::from("body"), Value::from(self.body.clone()));
        Ok(serde_yaml_ng::to_string(&Value::Mapping(map))?)
    }
}

/// Strip a leading `---` delimiter line, returning the remainder of the input.
fn strip_opening_delimiter(input: &str) -> Option<&str> {
    if let Some(rest) = input.strip_prefix("---\n") {
        Some(rest)
    } else {
        input.strip_prefix("---\r\n")
    }
}

/// Split the YAML region from the body at the first closing delimiter line
/// (`---` or `...`). Returns `(yaml, body)`.
fn split_closing_delimiter(input: &str) -> Option<(&str, &str)> {
    let mut search_from = 0;
    while let Some(rel) = input[search_from..].find('\n') {
        let line_start = search_from;
        let line_end = search_from + rel; // index of the '\n'
        let line = input[line_start..line_end].trim_end_matches('\r');
        if line == "---" || line == "..." {
            let yaml = &input[..line_start];
            // Body begins after this delimiter line's newline.
            let body = &input[line_end + 1..];
            return Some((yaml, body));
        }
        search_from = line_end + 1;
    }
    // Handle a closing delimiter on the final line without a trailing newline.
    let last_line_start = input.rfind('\n').map(|i| i + 1).unwrap_or(0);
    let last_line = input[last_line_start..].trim_end_matches('\r');
    if last_line == "---" || last_line == "..." {
        return Some((&input[..last_line_start], ""));
    }
    None
}

/// Shallow-merge `newer` on top of `base`, with values from `newer` overriding
/// those already present in `base`. Returns the merged mapping.
pub fn shallow_merge(base: &mut Mapping, newer: &Mapping) {
    for (k, v) in newer {
        base.insert(k.clone(), v.clone());
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_frontmatter_and_body() {
        let doc = Document::parse("---\nstatus: task\nid: 7\n---\nhello world\n").unwrap();
        assert_eq!(doc.frontmatter.len(), 2);
        assert_eq!(doc.body, "hello world\n");
    }

    #[test]
    fn parses_body_without_frontmatter() {
        let doc = Document::parse("just text\n").unwrap();
        assert!(doc.frontmatter.is_empty());
        assert_eq!(doc.body, "just text\n");
    }

    #[test]
    fn empty_frontmatter_block() {
        let doc = Document::parse("---\n---\nbody\n").unwrap();
        assert!(doc.frontmatter.is_empty());
        assert_eq!(doc.body, "body\n");
    }

    #[test]
    fn round_trips() {
        let raw = "---\nstatus: task\n---\ncontent here\n";
        let doc = Document::parse(raw).unwrap();
        let rendered = doc.render().unwrap();
        let reparsed = Document::parse(&rendered).unwrap();
        assert_eq!(doc, reparsed);
    }

    #[test]
    fn to_yaml_includes_body_key() {
        let doc = Document::parse("---\nstatus: done\n---\nall finished\n").unwrap();
        let yaml = doc.to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(value.get("status").and_then(|v| v.as_str()), Some("done"));
        assert_eq!(
            value.get("body").and_then(|v| v.as_str()),
            Some("all finished\n")
        );
    }

    #[test]
    fn merge_overrides_newer_wins() {
        let mut base = Mapping::new();
        base.insert("status".into(), "task".into());
        base.insert("id".into(), "abc".into());
        let mut newer = Mapping::new();
        newer.insert("status".into(), "done".into());
        shallow_merge(&mut base, &newer);
        assert_eq!(base.get("status").unwrap().as_str(), Some("done"));
        assert_eq!(base.get("id").unwrap().as_str(), Some("abc"));
    }
}
