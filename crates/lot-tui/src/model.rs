//! The read-only data the TUI displays: a flattened tree of Things.
//!
//! Each [`Row`] is one Thing, pre-computed at load time so rendering never
//! touches the filesystem. Rows are stored depth-first; `depth`, `parent`, and
//! `children` describe the tree they came from.

use anyhow::Result;
use lot_core::thing::Thing;
use lot_core::Vault;

/// One Thing, reduced to what the views need.
pub struct Row {
    pub title: String,
    pub status: String,
    pub depth: usize,
    pub children: Vec<usize>,
    /// Frontmatter of the computed state as ordered `key: value` pairs.
    pub meta: Vec<(String, String)>,
    /// The merged markdown body of every update.
    pub body: String,
}

/// Load the whole vault into a depth-first list of [`Row`]s.
pub fn load_rows(vault: &Vault) -> Result<Vec<Row>> {
    let mut rows = Vec::new();
    for thing in vault.things()? {
        push_thing(&thing, 0, None, &mut rows)?;
    }
    Ok(rows)
}

fn push_thing(
    thing: &Thing,
    depth: usize,
    parent: Option<usize>,
    rows: &mut Vec<Row>,
) -> Result<()> {
    // Compute the state once and derive everything from it.
    let state = thing.compute_state()?;
    let status = state
        .frontmatter
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("note")
        .to_string();
    let title = first_h1(&state.body).unwrap_or_else(|| thing.name());
    let meta = meta_pairs(&state.frontmatter);

    let index = rows.len();
    rows.push(Row {
        title,
        status,
        depth,
        children: Vec::new(),
        meta,
        body: state.body,
    });
    if let Some(p) = parent {
        rows[p].children.push(index);
    }

    for child in thing.children()? {
        push_thing(&child, depth + 1, Some(index), rows)?;
    }
    Ok(())
}

/// The first level-1 heading (`# ...`) in `body`, if any.
fn first_h1(body: &str) -> Option<String> {
    body.lines().find_map(|line| {
        let title = line.strip_prefix("# ")?.trim();
        (!title.is_empty()).then(|| title.to_string())
    })
}

/// Flatten frontmatter into displayable `(key, value)` pairs, preserving order.
fn meta_pairs(fm: &serde_yaml_ng::Mapping) -> Vec<(String, String)> {
    fm.iter()
        .filter_map(|(k, v)| {
            let key = k.as_str()?.to_string();
            Some((key, scalar(v)))
        })
        .collect()
}

/// Render a YAML scalar for the metadata panel.
fn scalar(v: &serde_yaml_ng::Value) -> String {
    use serde_yaml_ng::Value;
    match v {
        Value::Null => "null".to_string(),
        Value::Bool(b) => b.to_string(),
        Value::Number(n) => n.to_string(),
        Value::String(s) => s.clone(),
        other => serde_yaml_ng::to_string(other)
            .unwrap_or_default()
            .trim()
            .to_string(),
    }
}
