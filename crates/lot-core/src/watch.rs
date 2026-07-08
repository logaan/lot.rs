//! Watch a vault for changes and shape them into self-contained events.
//!
//! This module is the live-update mechanism the CLI's `lot watch` command drives
//! and front-ends (the Python Textual UI) consume. It is deliberately
//! interface-agnostic: it owns the filesystem watcher and produces
//! [`WatchEvent`]s, but knows nothing about how they are printed. The CLI simply
//! serialises each event to YAML and flushes it to stdout.
//!
//! Each event carries the *minimum* a consumer needs to patch its own in-memory
//! copy of the Things tree incrementally — not a fresh snapshot of the whole
//! vault on every tick:
//!
//! * `kind` — [`ChangeKind::Created`], [`ChangeKind::Modified`],
//!   [`ChangeKind::Deleted`] or [`ChangeKind::Reload`].
//! * `id` — the affected Thing's `task-id`. Present on created / modified /
//!   deleted; absent on a `reload`.
//! * `name` — the affected Thing's display name (the same value the tree in
//!   `lot thing list` uses). Present on created / modified only.
//! * `status` — the affected Thing's current status. Present on created /
//!   modified only.
//! * `parent` — the `task-id` of the affected Thing's parent, or absent for a
//!   top-level Thing. Present (possibly absent-for-top-level) on created /
//!   modified only. Together `id` + `name` + `status` + `parent` are exactly
//!   enough to patch one node into a consumer's tree index.
//! * `state` — the affected Thing's recomputed computed-state (the same value as
//!   `lot thing get`). Present on created / modified, so a detail view showing
//!   the changed Thing needs no follow-up `lot` call.
//! * `updates` — the affected Thing's whole update thread (the same value as
//!   `lot thing updates`). Present on created / modified, for the same reason.
//!
//! A `deleted` event carries only `kind` + `id`: the consumer drops that id and
//! any descendants of it from its index. A `reload` event carries only `kind`:
//! it is the rare fallback for a settled batch that maps to no single Thing
//! (e.g. a vault-level file edit that isn't a Thing), telling the consumer to
//! reload its baseline from scratch rather than embedding the whole tree.
//!
//! Rapid bursts (e.g. a git auto-commit that rewrites several files) are
//! debounced into a single settled batch, and churn inside the vault's `.git/`
//! directory is ignored so the watcher never loops on its own repository's
//! internals.

use crate::error::{Error, Result};
use crate::render;
use crate::thing::Thing;
use crate::vault::Vault;
use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use serde::Serialize;
use serde_yaml_ng::Value;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::mpsc::{self, RecvTimeoutError};
use std::time::Duration;

mod classify;

use classify::{classify, is_git_path, Change};

/// How long the watcher waits for the filesystem to fall quiet before emitting a
/// batch. A fresh change during the window restarts the timer, so a burst of
/// writes (like a git commit touching many files) coalesces into one settled
/// batch rather than a flurry of events.
const DEBOUNCE: Duration = Duration::from_millis(200);

/// The nature of a change to a Thing.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum ChangeKind {
    /// A Thing that did not exist before now does.
    Created,
    /// An existing Thing's updates (or nested structure) changed, or its
    /// folder moved (its `parent` may differ from what the consumer has).
    Modified,
    /// A Thing that existed before is now gone.
    Deleted,
    /// A settled batch that maps to no single Thing (e.g. a vault-level file
    /// edit): the consumer should reload its baseline from scratch.
    Reload,
}

/// A single minimal, incremental change event.
///
/// Field order is the serialisation order
/// (kind, id, name, status, parent, state, updates). Every field after `kind`
/// is omitted when not applicable, so each event carries only the minimum a
/// consumer needs to patch one node of its own tree index:
///
/// * created / modified: `id`, `name`, `status`, `parent` (absent for a
///   top-level Thing), `state`, `updates`.
/// * deleted: `id` only.
/// * reload: `kind` only.
#[derive(Debug, Serialize)]
pub struct WatchEvent {
    /// What happened.
    pub kind: ChangeKind,
    /// The affected Thing's `task-id`. Absent on a `reload`.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    /// The affected Thing's display name (created / modified only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    /// The affected Thing's current status (created / modified only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub status: Option<String>,
    /// The affected Thing's parent `task-id`; absent for a top-level Thing
    /// (created / modified only).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parent: Option<String>,
    /// The affected Thing's recomputed computed-state (as `lot thing get`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state: Option<Value>,
    /// The affected Thing's update thread (as `lot thing updates`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updates: Option<Value>,
}

impl WatchEvent {
    /// Serialise the event to a standalone YAML document (no leading `---`
    /// marker — the stream framing is the caller's concern).
    pub fn to_yaml(&self) -> Result<String> {
        Ok(serde_yaml_ng::to_string(self)?)
    }
}

/// Watch `vault` for changes, calling `on_event` for every event produced.
///
/// This blocks forever (until the watcher errors or the process is killed),
/// draining and debouncing raw filesystem notifications, classifying each
/// settled batch against the previous snapshot of the Things tree, and invoking
/// `on_event` once per affected Thing. No event is emitted for the vault's
/// initial state — a consumer should load the baseline itself (e.g. with
/// `lot thing list`) and then apply the stream on top.
///
/// `root`, when given, is a Thing id: events are limited to that Thing and its
/// descendants (the root Thing itself is included), letting a coordinator
/// watch just its own subtree. `None` watches the whole vault, matching the
/// behaviour before this parameter existed. The root's folder is resolved once
/// up front, so a later move of the root Thing is not tracked.
///
/// If `on_event` returns an error, watching stops and the error is propagated.
pub fn watch(
    vault: &Vault,
    root: Option<&str>,
    mut on_event: impl FnMut(&WatchEvent) -> Result<()>,
) -> Result<()> {
    let root_path = root
        .map(|id| vault.find_thing(id).map(|thing| thing.path().to_path_buf()))
        .transpose()?;

    let (tx, rx) = mpsc::channel::<PathBuf>();

    // The OS's native backend (FSEvents/inotify), not polling. Pure access
    // (read) events and anything inside `.git/` are dropped at the source so the
    // debounce loop only ever sees meaningful vault changes.
    let mut watcher: RecommendedWatcher =
        notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
            if let Ok(event) = res {
                if matches!(event.kind, notify::EventKind::Access(_)) {
                    return;
                }
                for path in event.paths {
                    if is_git_path(&path) {
                        continue;
                    }
                    let _ = tx.send(path);
                }
            }
        })
        .map_err(watch_err)?;
    watcher
        .watch(vault.path(), RecursiveMode::Recursive)
        .map_err(watch_err)?;

    // The snapshot of known Thing folders, kept between batches so a batch can be
    // classified as created / modified / deleted.
    let mut known = thing_folders(vault)?;

    loop {
        // Block until the first change of a batch, then keep draining until the
        // filesystem has been quiet for DEBOUNCE. A disconnected channel means
        // the watcher was dropped — stop watching.
        let first = match rx.recv() {
            Ok(path) => path,
            Err(_) => return Ok(()),
        };
        let mut changed: HashSet<PathBuf> = HashSet::new();
        changed.insert(first);
        loop {
            match rx.recv_timeout(DEBOUNCE) {
                Ok(path) => {
                    changed.insert(path);
                }
                Err(RecvTimeoutError::Timeout) => break,
                Err(RecvTimeoutError::Disconnected) => break,
            }
        }

        let current = thing_folders(vault)?;
        let changes = classify(&known, &current, &changed);
        let changes = scope_changes(root_path.as_deref(), &known, &current, changes);
        for event in build_events(vault, &current, &changes)? {
            on_event(&event)?;
        }
        known = current;
    }
}

/// Flesh out classified [`Change`]s into minimal [`WatchEvent`]s.
///
/// A deletion becomes a `kind` + `id` event. A [`ChangeKind::Reload`] becomes a
/// bare `kind`-only event. A creation or modification is fleshed out with the
/// Thing's `name`, `status` and `parent` (the fields a consumer patches into its
/// index) plus its recomputed `state` and `updates`. If a created/modified Thing
/// can no longer be read (it vanished in the race between classifying and
/// building) the event degrades to a `reload` so the consumer still resyncs.
fn build_events(
    vault: &Vault,
    current: &HashMap<PathBuf, String>,
    changes: &[Change],
) -> Result<Vec<WatchEvent>> {
    let mut events = Vec::with_capacity(changes.len());
    for change in changes {
        let event = match change.kind {
            ChangeKind::Reload => reload_event(),
            // A deleted Thing is gone: carry only its id so the consumer can
            // drop it and any descendants from its index.
            ChangeKind::Deleted => WatchEvent {
                kind: ChangeKind::Deleted,
                id: change.id.clone(),
                name: None,
                status: None,
                parent: None,
                state: None,
                updates: None,
            },
            ChangeKind::Created | ChangeKind::Modified => {
                match change
                    .id
                    .as_deref()
                    .and_then(|id| vault.find_thing(id).ok())
                {
                    Some(thing) => WatchEvent {
                        kind: change.kind,
                        id: change.id.clone(),
                        name: Some(thing.title().unwrap_or_else(|_| thing.name())),
                        status: Some(thing.status().unwrap_or_else(|_| "unknown".to_string())),
                        parent: parent_id(current, thing.path()),
                        state: Some(thing.compute_state()?.to_value()),
                        updates: Some(render::thing_updates_value(&thing)?),
                    },
                    // Can't read the Thing any more: fall back to a reload.
                    None => reload_event(),
                }
            }
        };
        events.push(event);
    }
    Ok(events)
}

/// The bare `kind`-only fallback event telling a consumer to reload its baseline.
fn reload_event() -> WatchEvent {
    WatchEvent {
        kind: ChangeKind::Reload,
        id: None,
        name: None,
        status: None,
        parent: None,
        state: None,
        updates: None,
    }
}

/// Restrict `changes` to those under `root` (a Thing folder path), when
/// scoping is requested. `None` passes every change through unchanged.
///
/// A `reload` always passes through: it carries no id, and the consumer
/// rebaselines its own scope regardless. A created/modified change is checked
/// against its Thing's path in `current` (where it now lives); a deleted
/// change is checked against `known` (its last path, since it is already gone
/// from `current`). When the id can't be found in the relevant snapshot —
/// which should not happen, but a race is cheaper to tolerate than a dropped
/// event — the change is let through rather than silently swallowed.
fn scope_changes(
    root: Option<&Path>,
    known: &HashMap<PathBuf, String>,
    current: &HashMap<PathBuf, String>,
    changes: Vec<Change>,
) -> Vec<Change> {
    let Some(root) = root else {
        return changes;
    };
    changes
        .into_iter()
        .filter(|change| match change.kind {
            ChangeKind::Reload => true,
            ChangeKind::Deleted => in_scope(root, known, change.id.as_deref()),
            ChangeKind::Created | ChangeKind::Modified => {
                in_scope(root, current, change.id.as_deref())
            }
        })
        .collect()
}

/// Whether the Thing `id` (looked up in `folders`, a path→id snapshot) lies at
/// or under `root`. An id that can't be resolved in `folders` is treated as
/// in scope, so an unexpected gap never silently drops an event.
fn in_scope(root: &Path, folders: &HashMap<PathBuf, String>, id: Option<&str>) -> bool {
    let Some(id) = id else {
        return true;
    };
    match folders.iter().find(|(_, v)| v.as_str() == id) {
        Some((path, _)) => path.starts_with(root),
        None => true,
    }
}

/// The `task-id` of the Thing whose folder directly contains `thing_path`, or
/// `None` when `thing_path` is a top-level Thing (its parent is the vault root).
/// A Thing's folder always sits directly inside its parent Thing's folder, so a
/// plain parent-directory lookup in the folder→id map is enough.
fn parent_id(current: &HashMap<PathBuf, String>, thing_path: &Path) -> Option<String> {
    current.get(thing_path.parent()?).cloned()
}

/// Map every Thing folder in the vault (top-level and nested) to its `task-id`.
/// Things whose id can't be read are skipped.
fn thing_folders(vault: &Vault) -> Result<HashMap<PathBuf, String>> {
    let mut map = HashMap::new();
    collect_folders(vault.things()?, &mut map)?;
    Ok(map)
}

fn collect_folders(things: Vec<Thing>, map: &mut HashMap<PathBuf, String>) -> Result<()> {
    for thing in things {
        if let Ok(id) = thing.id() {
            map.insert(thing.path().to_path_buf(), id);
        }
        collect_folders(thing.children()?, map)?;
    }
    Ok(())
}

/// Wrap a `notify` error as a core [`Error`].
fn watch_err(err: notify::Error) -> Error {
    Error::Watch(err.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::update::test_types::{note, work};

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
    fn event_yaml_carries_minimal_patch_fields() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault
            .new_thing("Buy milk", "get the oat one", &note())
            .unwrap();
        let id = thing.id().unwrap();

        let current = thing_folders(&vault).unwrap();
        let changes = vec![Change {
            kind: ChangeKind::Created,
            id: Some(id.clone()),
        }];
        let events = build_events(&vault, &current, &changes).unwrap();
        assert_eq!(events.len(), 1);
        let yaml = events[0].to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();

        assert_eq!(value.get("kind").and_then(|v| v.as_str()), Some("created"));
        assert_eq!(value.get("id").and_then(|v| v.as_str()), Some(id.as_str()));
        // The patch fields: display name, status, and (absent) top-level parent.
        assert_eq!(value.get("name").and_then(|v| v.as_str()), Some("Buy milk"));
        assert_eq!(value.get("status").and_then(|v| v.as_str()), Some("note"));
        // A top-level Thing carries no `parent` key.
        assert!(value.get("parent").is_none());
        // `state` mirrors `lot thing get` (frontmatter keys plus `body`).
        assert!(value.get("state").and_then(|v| v.get("body")).is_some());
        assert_eq!(
            value
                .get("state")
                .and_then(|v| v.get("status"))
                .and_then(|v| v.as_str()),
            Some("note")
        );
        // `updates` mirrors `lot thing updates` (a list of the thread).
        let updates = value
            .get("updates")
            .and_then(|v| v.as_sequence())
            .expect("updates should be a sequence");
        assert_eq!(updates.len(), 1);
        // The whole-tree snapshot is gone: an event patches one node only.
        assert!(value.get("things").is_none());
    }

    #[test]
    fn child_event_carries_parent_id() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "", &note()).unwrap();
        let parent_id = parent.id().unwrap();
        let child = vault
            .new_child_thing(&parent_id, "Child", "", &note())
            .unwrap();
        let child_id = child.id().unwrap();

        let current = thing_folders(&vault).unwrap();
        let changes = vec![Change {
            kind: ChangeKind::Created,
            id: Some(child_id.clone()),
        }];
        let events = build_events(&vault, &current, &changes).unwrap();
        let value: Value = serde_yaml_ng::from_str(&events[0].to_yaml().unwrap()).unwrap();

        assert_eq!(value.get("name").and_then(|v| v.as_str()), Some("Child"));
        // A nested Thing carries its parent's task-id.
        assert_eq!(
            value.get("parent").and_then(|v| v.as_str()),
            Some(parent_id.as_str())
        );
    }

    #[test]
    fn deleted_event_carries_only_id() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        // A deletion references an id that is no longer in the vault; the event
        // carries only its kind and id.
        let current = thing_folders(&vault).unwrap();
        let changes = vec![Change {
            kind: ChangeKind::Deleted,
            id: Some("lot:033GoneGoneGoneGoneGone".to_string()),
        }];
        let events = build_events(&vault, &current, &changes).unwrap();
        assert_eq!(events.len(), 1);
        assert!(events[0].name.is_none());
        assert!(events[0].status.is_none());
        assert!(events[0].parent.is_none());
        assert!(events[0].state.is_none());
        assert!(events[0].updates.is_none());

        let yaml = events[0].to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(value.get("kind").and_then(|v| v.as_str()), Some("deleted"));
        assert_eq!(
            value.get("id").and_then(|v| v.as_str()),
            Some("lot:033GoneGoneGoneGoneGone")
        );
        assert!(value.get("state").is_none());
        assert!(value.get("updates").is_none());
        assert!(value.get("things").is_none());
    }

    #[test]
    fn reload_event_carries_only_kind() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let current = HashMap::new();
        let changes = vec![Change {
            kind: ChangeKind::Reload,
            id: None,
        }];
        let events = build_events(&vault, &current, &changes).unwrap();
        assert_eq!(events.len(), 1);

        let yaml = events[0].to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(value.get("kind").and_then(|v| v.as_str()), Some("reload"));
        // Nothing else: the consumer reloads its own baseline.
        assert!(value.get("id").is_none());
        assert!(value.get("name").is_none());
        assert!(value.get("state").is_none());
        assert!(value.get("things").is_none());
    }

    #[test]
    fn added_update_is_a_modification_with_fresh_state() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &work(), "on it").unwrap();

        let current = thing_folders(&vault).unwrap();
        let changes = vec![Change {
            kind: ChangeKind::Modified,
            id: Some(id.clone()),
        }];
        let events = build_events(&vault, &current, &changes).unwrap();
        let value: Value = serde_yaml_ng::from_str(&events[0].to_yaml().unwrap()).unwrap();

        // The patched status and recomputed state reflect the new `work` update.
        assert_eq!(value.get("status").and_then(|v| v.as_str()), Some("work"));
        assert_eq!(
            value
                .get("state")
                .and_then(|v| v.get("status"))
                .and_then(|v| v.as_str()),
            Some("work")
        );
        let updates = value.get("updates").and_then(|v| v.as_sequence()).unwrap();
        assert_eq!(updates.len(), 2);
    }

    #[test]
    fn scope_changes_keeps_root_and_descendants_drops_others() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let root = vault.new_thing("Root", "", &note()).unwrap();
        let root_id = root.id().unwrap();
        let child = vault
            .new_child_thing(&root_id, "Child", "", &note())
            .unwrap();
        let child_id = child.id().unwrap();
        let other = vault.new_thing("Other", "", &note()).unwrap();
        let other_id = other.id().unwrap();

        let known = HashMap::new();
        let current = thing_folders(&vault).unwrap();
        let changes = vec![
            Change {
                kind: ChangeKind::Created,
                id: Some(root_id.clone()),
            },
            Change {
                kind: ChangeKind::Created,
                id: Some(child_id.clone()),
            },
            Change {
                kind: ChangeKind::Created,
                id: Some(other_id.clone()),
            },
        ];

        let root_path = vault.find_thing(&root_id).unwrap().path().to_path_buf();
        let scoped = scope_changes(Some(&root_path), &known, &current, changes);
        let ids: HashSet<String> = scoped.into_iter().filter_map(|c| c.id).collect();
        // The root itself and its descendant are in scope; the unrelated
        // top-level Thing is filtered out.
        assert!(ids.contains(&root_id));
        assert!(ids.contains(&child_id));
        assert!(!ids.contains(&other_id));
    }

    #[test]
    fn scope_changes_lets_deletions_and_reloads_through_correctly() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let root = vault.new_thing("Root", "", &note()).unwrap();
        let root_id = root.id().unwrap();
        let root_path = root.path().to_path_buf();
        let inside = root_path.join("Child");
        let outside = PathBuf::from("/v/Elsewhere");

        // `known` reflects the state before the deletions: one Thing inside the
        // root's subtree, one outside it. Both are already gone from `current`.
        let mut known = HashMap::new();
        known.insert(root_path.clone(), root_id.clone());
        known.insert(inside, "lot:inside".to_string());
        known.insert(outside, "lot:outside".to_string());
        let current: HashMap<PathBuf, String> = HashMap::new();

        let changes = vec![
            Change {
                kind: ChangeKind::Deleted,
                id: Some("lot:inside".to_string()),
            },
            Change {
                kind: ChangeKind::Deleted,
                id: Some("lot:outside".to_string()),
            },
            Change {
                kind: ChangeKind::Reload,
                id: None,
            },
        ];

        let scoped = scope_changes(Some(&root_path), &known, &current, changes);
        let ids: HashSet<Option<String>> = scoped.iter().map(|c| c.id.clone()).collect();
        assert!(ids.contains(&Some("lot:inside".to_string())));
        assert!(!ids.contains(&Some("lot:outside".to_string())));
        // A reload always passes through, scoped or not.
        assert!(scoped.iter().any(|c| c.kind == ChangeKind::Reload));
    }

    #[test]
    fn scope_changes_is_a_no_op_when_unscoped() {
        let changes = vec![
            Change {
                kind: ChangeKind::Created,
                id: Some("lot:anything".to_string()),
            },
            Change {
                kind: ChangeKind::Reload,
                id: None,
            },
        ];
        let scoped = scope_changes(None, &HashMap::new(), &HashMap::new(), changes.clone());
        assert_eq!(scoped, changes);
    }
}
