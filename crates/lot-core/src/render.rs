//! Presentation logic for listing Things in different formats.
//!
//! Lives in `lot-core` (rather than the CLI) so the markdown and YAML views can
//! be reused by future TUI / web front-ends, and so the YAML serialisation can
//! use this crate's `serde_yaml_ng` dependency.

use crate::error::Result;
use crate::frontmatter::Document;
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

/// Build the `lot thing list` YAML value: a mapping of the vault `path` and a
/// `things` tree of `{ name, id, status, children? }`. The `children` key is
/// present only when a thing has sub-things.
///
/// Exposed as a [`Value`] (rather than only its serialised string) so consumers
/// such as the `watch` event stream can embed the snapshot inside a larger
/// document without re-parsing it.
pub fn thing_list_value(vault: &Vault) -> Result<Value> {
    let things: Vec<Value> = nodes(vault)?.iter().map(node_to_yaml).collect();

    let mut root = Mapping::new();
    root.insert(
        Value::from("path"),
        Value::from(vault.path().display().to_string()),
    );
    root.insert(Value::from("things"), Value::Sequence(things));
    Ok(Value::Mapping(root))
}

/// Render `lot thing list` as a YAML document (see [`thing_list_value`]).
pub fn thing_list_yaml(vault: &Vault) -> Result<String> {
    Ok(serde_yaml_ng::to_string(&thing_list_value(vault)?)?)
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

/// Render a Thing's whole update thread as a YAML list, oldest first — the
/// surface a detail view renders as independent, expandable items. Each entry
/// carries everything needed to display an update without re-reading files off
/// disk: its `update-id`, its `type` (`note`/`work`/`info`/`done`), the `at`
/// timestamp, any other frontmatter it recorded (e.g. a `note`'s `task-id`),
/// and the raw markdown `body`.
///
/// This is deliberately separate from [`Thing::compute_state`], which reduces
/// the same updates into a single merged current state.
///
/// Exposed as a [`Value`] (rather than only its serialised string) so consumers
/// such as the `watch` event stream can embed the thread inside a larger
/// document without re-parsing it.
pub fn thing_updates_value(thing: &Thing) -> Result<Value> {
    let updates: Vec<Value> = thing.updates()?.iter().map(update_to_yaml).collect();
    Ok(Value::Sequence(updates))
}

/// Render a Thing's whole update thread as a YAML document (see
/// [`thing_updates_value`]).
pub fn thing_updates_yaml(thing: &Thing) -> Result<String> {
    Ok(serde_yaml_ng::to_string(&thing_updates_value(thing)?)?)
}

/// Convert one parsed update into the normalised mapping used by
/// [`thing_updates_yaml`]: `update-id`, `type`, `at`, then any remaining
/// frontmatter, and finally the raw `body`.
///
/// `type` is the update's `status`; `at` is pulled from the type-specific
/// timestamp field (`note-at`, `work-at`, …) and re-keyed to `at`. Those source
/// keys — plus `status`, which becomes `type` — are dropped from the pass-through
/// so they aren't duplicated. Any other frontmatter (such as a `note`'s
/// `task-id`) is preserved in its original order.
fn update_to_yaml(doc: &Document) -> Value {
    let fm = &doc.frontmatter;
    let status = fm.get("status").and_then(|v| v.as_str()).unwrap_or("");
    // The timestamp always lives at `<status>-at` — for custom types exactly
    // as for built-ins.
    let timestamp_field = crate::update::timestamp_field_for(status);

    let mut m = Mapping::new();
    if let Some(id) = fm.get("update-id") {
        m.insert(Value::from("update-id"), id.clone());
    }
    m.insert(Value::from("type"), Value::from(status));
    if let Some(at) = fm.get(&timestamp_field) {
        m.insert(Value::from("at"), at.clone());
    }
    // Pass through any remaining frontmatter, skipping the keys already
    // surfaced as `type`/`at`/`update-id` above so they aren't repeated.
    for (k, v) in fm {
        let key = k.as_str().unwrap_or_default();
        if key == "status" || key == "update-id" || key == timestamp_field {
            continue;
        }
        m.insert(k.clone(), v.clone());
    }
    m.insert(Value::from("body"), Value::from(doc.body.clone()));
    Value::Mapping(m)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::update::test_types::{done, note, work};

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
        vault.new_thing("Fresh", "", &note()).unwrap();
        let working = vault.new_thing("Working", "", &note()).unwrap();
        vault
            .add_update(&working.id().unwrap(), &work(), "on it")
            .unwrap();

        let md = thing_list_markdown(&vault).unwrap();
        assert!(md.starts_with(&format!("# {}\n", vault.path().display())));
        // No status grouping headers any more.
        assert!(!md.contains("## "));
        // Status appears to the left of the link.
        assert!(md.contains(&format!("- work [Working]({})", working.id().unwrap())));
        assert!(md.contains("- note [Fresh]("));
    }

    #[test]
    fn markdown_indents_children_two_spaces() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "", &note()).unwrap();
        let child = vault
            .new_child_thing(&parent.id().unwrap(), "Child", "", &note())
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
        let parent = vault.new_thing("Parent", "", &note()).unwrap();
        vault
            .new_child_thing(&parent.id().unwrap(), "Child", "", &note())
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
        let thing = vault.new_thing("Buy milk", "", &note()).unwrap();
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

    #[test]
    fn updates_yaml_lists_the_thread_oldest_first() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault
            .new_thing("Buy milk", "get the oat one", &note())
            .unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &work(), "on it").unwrap();
        vault.add_update(&id, &done(), "").unwrap();

        let yaml = thing_updates_yaml(&vault.find_thing(&id).unwrap()).unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        let updates = value.as_sequence().expect("top level should be a list");
        assert_eq!(updates.len(), 3);

        // Oldest first: the automatic `note`, then `work`, then `done`.
        let note = &updates[0];
        assert_eq!(note.get("type").and_then(|v| v.as_str()), Some("note"));
        // The note re-keys its timestamp to `at` (from `note-at`) and drops the
        // raw `status`/`note-at` keys.
        assert!(note.get("at").and_then(|v| v.as_str()).is_some());
        assert!(note.get("status").is_none());
        assert!(note.get("note-at").is_none());
        // `update-id` is carried, and any other frontmatter (the note's
        // `task-id`) is passed through untouched.
        assert!(note.get("update-id").and_then(|v| v.as_str()).is_some());
        assert_eq!(
            note.get("task-id").and_then(|v| v.as_str()),
            Some(id.as_str())
        );
        // The raw markdown body is carried verbatim (the created note prepends
        // an h1 with the thing's name).
        assert_eq!(
            note.get("body").and_then(|v| v.as_str()),
            Some("# Buy milk\n\nget the oat one\n")
        );

        let work = &updates[1];
        assert_eq!(work.get("type").and_then(|v| v.as_str()), Some("work"));
        assert!(work.get("at").and_then(|v| v.as_str()).is_some());
        assert_eq!(work.get("body").and_then(|v| v.as_str()), Some("on it"));
        // Ordinary updates don't carry a `task-id`.
        assert!(work.get("task-id").is_none());

        let done = &updates[2];
        assert_eq!(done.get("type").and_then(|v| v.as_str()), Some("done"));
        // `done` is a bare marker: its body is empty.
        assert_eq!(done.get("body").and_then(|v| v.as_str()), Some(""));
    }

    #[test]
    fn updates_yaml_rekeys_custom_type_timestamps_to_at() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Buy milk", "", &note()).unwrap();
        let id = thing.id().unwrap();
        let custom = crate::update::UpdateType {
            name: "blocked".into(),
            takes_body: true,
            terminal: false,
        };
        vault.add_update(&id, &custom, "waiting on parts").unwrap();

        let yaml = thing_updates_yaml(&vault.find_thing(&id).unwrap()).unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        let updates = value.as_sequence().unwrap();
        let blocked = &updates[1];

        // The custom type's timestamp follows the `<name>-at` convention and
        // is re-keyed to `at`, exactly like a built-in's.
        assert_eq!(
            blocked.get("type").and_then(|v| v.as_str()),
            Some("blocked")
        );
        assert!(blocked.get("at").and_then(|v| v.as_str()).is_some());
        assert!(blocked.get("blocked-at").is_none());
        assert!(blocked.get("status").is_none());
        assert_eq!(
            blocked.get("body").and_then(|v| v.as_str()),
            Some("waiting on parts")
        );
    }
}
