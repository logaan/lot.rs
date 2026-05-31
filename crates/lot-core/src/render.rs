//! Presentation logic for listing Things in different formats.
//!
//! Lives in `lot-core` (rather than the CLI) so the markdown and YAML views can
//! be reused by future TUI / web front-ends, and so the YAML serialisation can
//! use this crate's `serde_yaml_ng` dependency.

use crate::error::Result;
use crate::thing::Thing;
use crate::vault::Vault;
use serde_yaml_ng::{Mapping, Value};

/// A Thing reduced to the fields the list views care about, plus its children.
struct Node {
    name: String,
    id: String,
    status: String,
    children: Vec<Node>,
}

/// Build the forest of top-level things and their descendants. Siblings keep
/// the by-name order from [`Vault::things`] / [`Thing::children`].
fn nodes(vault: &Vault) -> Result<Vec<Node>> {
    things_to_nodes(vault.things()?)
}

fn things_to_nodes(things: Vec<Thing>) -> Result<Vec<Node>> {
    let mut nodes = Vec::new();
    for thing in things {
        let children = things_to_nodes(thing.children()?)?;
        nodes.push(Node {
            // The display name is the computed h1, not the on-disk folder slug.
            name: thing.title().unwrap_or_else(|_| thing.name()),
            id: thing.id().unwrap_or_default(),
            status: thing.status().unwrap_or_else(|_| "note".to_string()),
            children,
        });
    }
    Ok(nodes)
}

/// Render the markdown for `lot thing list`: the vault path as an `h1`, then a
/// nested bullet list. Each item is `<status> [name](id)`; children are
/// indented two spaces under their parent.
pub fn thing_list_markdown(vault: &Vault) -> Result<String> {
    let mut out = format!("# {}\n", vault.path().display());
    let nodes = nodes(vault)?;
    if !nodes.is_empty() {
        out.push('\n');
        render_nodes_markdown(&nodes, 0, &mut out);
    }
    Ok(out)
}

fn render_nodes_markdown(nodes: &[Node], depth: usize, out: &mut String) {
    for node in nodes {
        let indent = "  ".repeat(depth);
        out.push_str(&format!(
            "{indent}- {} [{}]({})\n",
            node.status, node.name, node.id
        ));
        render_nodes_markdown(&node.children, depth + 1, out);
    }
}

/// Render `lot thing list` as a YAML document: the vault `path` and a `things`
/// tree of `{ name, id, status, children? }`. The `children` key is present
/// only when a thing has sub-things.
pub fn thing_list_yaml(vault: &Vault) -> Result<String> {
    let things: Vec<Value> = nodes(vault)?.iter().map(node_to_yaml).collect();

    let mut root = Mapping::new();
    root.insert(
        Value::from("path"),
        Value::from(vault.path().display().to_string()),
    );
    root.insert(Value::from("things"), Value::Sequence(things));
    Ok(serde_yaml_ng::to_string(&Value::Mapping(root))?)
}

fn node_to_yaml(node: &Node) -> Value {
    let mut m = Mapping::new();
    m.insert(Value::from("name"), Value::from(node.name.clone()));
    m.insert(Value::from("id"), Value::from(node.id.clone()));
    m.insert(Value::from("status"), Value::from(node.status.clone()));
    if !node.children.is_empty() {
        let children: Vec<Value> = node.children.iter().map(node_to_yaml).collect();
        m.insert(Value::from("children"), Value::Sequence(children));
    }
    Value::Mapping(m)
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
    fn markdown_shows_status_inline_without_h2_headers() {
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
        // No status grouping headers any more.
        assert!(!md.contains("## "));
        // Status appears to the left of the link.
        assert!(md.contains(&format!("- doing [Working]({})", doing.id().unwrap())));
        assert!(md.contains("- note [Fresh]("));
    }

    #[test]
    fn markdown_indents_children_two_spaces() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "").unwrap();
        let child = vault
            .new_child_thing(&parent.id().unwrap(), "Child", "")
            .unwrap();

        let md = thing_list_markdown(&vault).unwrap();
        assert!(md.contains("- note [Parent]("));
        // Child is indented two spaces beneath its parent.
        assert!(md.contains(&format!("  - note [Child]({})", child.id().unwrap())));
    }

    #[test]
    fn yaml_nests_children() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "").unwrap();
        vault
            .new_child_thing(&parent.id().unwrap(), "Child", "")
            .unwrap();

        let yaml = thing_list_yaml(&vault).unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        let things = value.get("things").and_then(|v| v.as_sequence()).unwrap();
        assert_eq!(things.len(), 1);
        let parent_node = &things[0];
        assert_eq!(
            parent_node.get("name").and_then(|v| v.as_str()),
            Some("Parent")
        );
        let children = parent_node
            .get("children")
            .and_then(|v| v.as_sequence())
            .expect("parent should have a children sequence");
        assert_eq!(children.len(), 1);
        assert_eq!(
            children[0].get("name").and_then(|v| v.as_str()),
            Some("Child")
        );
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
        assert_eq!(entry.get("status").and_then(|v| v.as_str()), Some("note"));
    }
}
