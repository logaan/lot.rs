//! Presentation logic for listing Things in different formats.
//!
//! Lives in `lot-core` (rather than the CLI) so the markdown and YAML views can
//! be reused by future TUI / web front-ends, and so the YAML serialisation can
//! use this crate's `serde_yaml_ng` dependency.

use crate::error::Result;
use crate::vault::Vault;
use serde_yaml_ng::{Mapping, Value};

/// A Thing reduced to the fields the list views care about.
struct Entry {
    name: String,
    id: String,
    status: String,
}

/// Lifecycle ordering for status groups; unknown statuses sort last.
fn status_rank(status: &str) -> usize {
    match status {
        "created" => 0,
        "task" => 1,
        "doing" => 2,
        "done" => 3,
        "archive" => 4,
        _ => 5,
    }
}

/// Collect every Thing in the vault, ordered by lifecycle status. Within a
/// status the by-name order from [`Vault::things`] is preserved (the sort is
/// stable).
fn entries(vault: &Vault) -> Result<Vec<Entry>> {
    let mut entries: Vec<Entry> = Vec::new();
    for thing in vault.things()? {
        let status = thing.status().unwrap_or_else(|_| "created".to_string());
        let id = thing.id().unwrap_or_default();
        // The display name is the computed h1, not the on-disk folder slug.
        let name = thing.title().unwrap_or_else(|_| thing.name());
        entries.push(Entry { name, id, status });
    }
    entries.sort_by_key(|e| (status_rank(&e.status), e.status.clone()));
    Ok(entries)
}

/// Render the markdown for `lot thing list`: the vault path as an `h1`, then the
/// Things grouped under an `h2` per status, in lifecycle order.
pub fn thing_list_markdown(vault: &Vault) -> Result<String> {
    let mut out = format!("# {}\n", vault.path().display());
    let mut current: Option<String> = None;
    for entry in entries(vault)? {
        if current.as_deref() != Some(entry.status.as_str()) {
            out.push_str(&format!("\n## {}\n\n", entry.status));
            current = Some(entry.status.clone());
        }
        out.push_str(&format!("- [{}]({})\n", entry.name, entry.id));
    }
    Ok(out)
}

/// Render `lot thing list` as a YAML document: the vault `path` and a flat
/// `things` sequence of `{ name, id, status }`, ordered by lifecycle status.
pub fn thing_list_yaml(vault: &Vault) -> Result<String> {
    let mut things = Vec::new();
    for entry in entries(vault)? {
        let mut m = Mapping::new();
        m.insert(Value::from("name"), Value::from(entry.name));
        m.insert(Value::from("id"), Value::from(entry.id));
        m.insert(Value::from("status"), Value::from(entry.status));
        things.push(Value::Mapping(m));
    }

    let mut root = Mapping::new();
    root.insert(
        Value::from("path"),
        Value::from(vault.path().display().to_string()),
    );
    root.insert(Value::from("things"), Value::Sequence(things));
    Ok(serde_yaml_ng::to_string(&Value::Mapping(root))?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::update::UpdateKind;

    fn git_available() -> bool {
        std::process::Command::new("git")
            .arg("--version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    fn configured_temp_vault() -> (tempfile::TempDir, Vault) {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("vault");
        for (k, v) in [
            ("GIT_AUTHOR_NAME", "Test"),
            ("GIT_AUTHOR_EMAIL", "test@example.com"),
            ("GIT_COMMITTER_NAME", "Test"),
            ("GIT_COMMITTER_EMAIL", "test@example.com"),
        ] {
            std::env::set_var(k, v);
        }
        let vault = Vault::open(&path).unwrap();
        (dir, vault)
    }

    #[test]
    fn markdown_groups_by_status_in_lifecycle_order() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        vault.new_thing("Fresh", "").unwrap();
        let doing = vault.new_thing("Working", "").unwrap();
        vault
            .add_update(&doing.id().unwrap(), UpdateKind::Doing, "on it")
            .unwrap();

        let md = thing_list_markdown(&vault).unwrap();
        assert!(md.starts_with(&format!("# {}\n", vault.path().display())));
        assert!(md.contains("## created"));
        assert!(md.contains("## doing"));
        assert!(md.contains(&format!("- [Working]({})", doing.id().unwrap())));
        // `doing` ranks after `created` in lifecycle order.
        assert!(md.find("## created").unwrap() < md.find("## doing").unwrap());
    }

    #[test]
    fn yaml_lists_things_with_status() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Buy milk", "").unwrap();
        let id = thing.id().unwrap();

        let yaml = thing_list_yaml(&vault).unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(
            value.get("path").and_then(|v| v.as_str()),
            Some(vault.path().display().to_string().as_str())
        );
        let things = value.get("things").and_then(|v| v.as_sequence()).unwrap();
        assert_eq!(things.len(), 1);
        let entry = &things[0];
        // `name` is the computed h1 (the human-readable name with spaces), not
        // the on-disk folder slug (`Buy_milk`).
        assert_eq!(entry.get("name").and_then(|v| v.as_str()), Some("Buy milk"));
        assert_eq!(entry.get("id").and_then(|v| v.as_str()), Some(id.as_str()));
        assert_eq!(
            entry.get("status").and_then(|v| v.as_str()),
            Some("created")
        );
    }
}
