//! Watch a vault for changes and shape them into self-contained events.
//!
//! This module is the live-update mechanism the CLI's `lot watch` command drives
//! and future front-ends (the Python Textual UI) consume. It is deliberately
//! interface-agnostic: it owns the filesystem watcher and produces
//! [`WatchEvent`]s, but knows nothing about how they are printed. The CLI simply
//! serialises each event to YAML and flushes it to stdout.
//!
//! Each event carries *everything* a consumer needs to refresh without issuing
//! follow-up `lot` commands:
//!
//! * `kind` — [`ChangeKind::Created`], [`ChangeKind::Modified`] or
//!   [`ChangeKind::Deleted`].
//! * `id` — the affected Thing's `task-id`, when it could be determined.
//! * `state` — the affected Thing's recomputed computed-state (the same value as
//!   `lot thing get`), present when the Thing still exists.
//! * `updates` — the affected Thing's whole update thread (the same value as
//!   `lot thing updates`), present when the Thing still exists.
//! * `things` — a full current snapshot of the Things tree (the same value as
//!   `lot thing list`), so structural / name / status changes are always
//!   reflected even when a single affected Thing can't be pinned down.
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
    /// An existing Thing's updates (or nested structure) changed.
    Modified,
    /// A Thing that existed before is now gone.
    Deleted,
}

/// A single self-contained change event.
///
/// Field order is the serialisation order (kind, id, state, updates, things).
/// `id`, `state` and `updates` are omitted when not applicable — a deletion
/// carries no state or updates because the Thing is gone.
#[derive(Debug, Serialize)]
pub struct WatchEvent {
    /// What happened.
    pub kind: ChangeKind,
    /// The affected Thing's `task-id`, when determinable.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub id: Option<String>,
    /// The affected Thing's recomputed computed-state (as `lot thing get`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state: Option<Value>,
    /// The affected Thing's update thread (as `lot thing updates`).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub updates: Option<Value>,
    /// A full snapshot of the Things tree (as `lot thing list`).
    pub things: Value,
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
/// If `on_event` returns an error, watching stops and the error is propagated.
pub fn watch(vault: &Vault, mut on_event: impl FnMut(&WatchEvent) -> Result<()>) -> Result<()> {
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
        for event in build_events(vault, &changes)? {
            on_event(&event)?;
        }
        known = current;
    }
}

/// A classified change, before it is turned into a full [`WatchEvent`].
#[derive(Debug, Clone, PartialEq, Eq)]
struct Change {
    kind: ChangeKind,
    id: Option<String>,
}

/// Classify a settled batch of `changed` paths against the `known` and
/// `current` folder→id maps.
///
/// A folder present in `current` but not `known` is a creation; one present in
/// `known` but not `current` is a deletion; any other changed path is attributed
/// to its deepest enclosing current Thing folder as a modification. When no
/// Thing can be pinned down but the batch was non-empty (e.g. a vault-level file
/// like the readme changed) a single id-less modification is returned so the
/// consumer still refreshes.
fn classify(
    known: &HashMap<PathBuf, String>,
    current: &HashMap<PathBuf, String>,
    changed: &HashSet<PathBuf>,
) -> Vec<Change> {
    let mut out = Vec::new();
    let mut accounted: HashSet<PathBuf> = HashSet::new();

    // Creations: folders newly present.
    for (path, id) in current {
        if !known.contains_key(path) {
            out.push(Change {
                kind: ChangeKind::Created,
                id: Some(id.clone()),
            });
            accounted.insert(path.clone());
        }
    }
    // Deletions: folders that vanished.
    for (path, id) in known {
        if !current.contains_key(path) {
            out.push(Change {
                kind: ChangeKind::Deleted,
                id: Some(id.clone()),
            });
            accounted.insert(path.clone());
        }
    }
    // Modifications: attribute each changed path to its deepest enclosing
    // current Thing, skipping folders already reported as created/deleted.
    let mut modified: HashSet<PathBuf> = HashSet::new();
    for path in changed {
        if let Some(folder) = owning_folder(current, path) {
            if !accounted.contains(&folder) {
                modified.insert(folder);
            }
        }
    }
    // Deterministic order for stable output/tests.
    let mut modified: Vec<PathBuf> = modified.into_iter().collect();
    modified.sort();
    for folder in modified {
        out.push(Change {
            kind: ChangeKind::Modified,
            id: current.get(&folder).cloned(),
        });
    }

    // Nothing mapped to a Thing, but the batch wasn't empty: emit a bare
    // modification so consumers still pick up vault-level changes.
    if out.is_empty() && !changed.is_empty() {
        out.push(Change {
            kind: ChangeKind::Modified,
            id: None,
        });
    }
    out
}

/// Flesh out classified [`Change`]s into full [`WatchEvent`]s, attaching the
/// shared Things snapshot and (for surviving Things) the recomputed state and
/// update thread.
fn build_events(vault: &Vault, changes: &[Change]) -> Result<Vec<WatchEvent>> {
    if changes.is_empty() {
        return Ok(Vec::new());
    }
    let things = render::thing_list_value(vault)?;
    let mut events = Vec::with_capacity(changes.len());
    for change in changes {
        let (state, updates) = match change.kind {
            // A deleted Thing is gone: there is nothing to recompute.
            ChangeKind::Deleted => (None, None),
            _ => match change
                .id
                .as_deref()
                .and_then(|id| vault.find_thing(id).ok())
            {
                Some(thing) => {
                    let (state, updates) = thing_detail(&thing)?;
                    (Some(state), Some(updates))
                }
                None => (None, None),
            },
        };
        events.push(WatchEvent {
            kind: change.kind,
            id: change.id.clone(),
            state,
            updates,
            things: things.clone(),
        });
    }
    Ok(events)
}

/// Recompute a Thing's computed-state and update-thread values.
fn thing_detail(thing: &Thing) -> Result<(Value, Value)> {
    let state = thing.compute_state()?.to_value();
    let updates = render::thing_updates_value(thing)?;
    Ok((state, updates))
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

/// The deepest Thing folder in `folders` that contains (or equals) `path`.
///
/// Paths are canonicalised before comparison so a match survives platform path
/// aliasing — notably macOS FSEvents reporting `/private/var/…` for a vault
/// rooted at `/var/…`. The original (un-canonicalised) key is returned so the
/// caller can still look the folder up in the folder→id map.
fn owning_folder(folders: &HashMap<PathBuf, String>, path: &Path) -> Option<PathBuf> {
    let target = canonical(path);
    folders
        .keys()
        .filter(|folder| target.starts_with(canonical(folder)))
        .max_by_key(|folder| canonical(folder).components().count())
        .cloned()
}

/// Canonicalise `path`, falling back to the path unchanged when it can't be
/// resolved (e.g. it has been deleted).
fn canonical(path: &Path) -> PathBuf {
    std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf())
}

/// Whether `path` lies inside a `.git` directory (or is one).
fn is_git_path(path: &Path) -> bool {
    path.components().any(|c| c.as_os_str() == ".git")
}

/// Wrap a `notify` error as a core [`Error`].
fn watch_err(err: notify::Error) -> Error {
    Error::Watch(err.to_string())
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
    fn ignores_git_internal_paths() {
        assert!(is_git_path(Path::new("/vault/.git/index")));
        assert!(is_git_path(Path::new("/vault/.git")));
        assert!(!is_git_path(Path::new("/vault/Thing/001.md")));
        assert!(!is_git_path(Path::new("/vault/agitprop/001.md")));
    }

    #[test]
    fn owning_folder_picks_deepest_match() {
        let mut folders = HashMap::new();
        folders.insert(PathBuf::from("/v/Parent"), "lot:parent".to_string());
        folders.insert(PathBuf::from("/v/Parent/Child"), "lot:child".to_string());

        // A file inside the nested child is attributed to the child, not the
        // parent, even though both enclose it.
        assert_eq!(
            owning_folder(&folders, Path::new("/v/Parent/Child/002.md")),
            Some(PathBuf::from("/v/Parent/Child"))
        );
        // A file directly in the parent is attributed to the parent.
        assert_eq!(
            owning_folder(&folders, Path::new("/v/Parent/003.md")),
            Some(PathBuf::from("/v/Parent"))
        );
        // A path outside any known Thing maps to nothing.
        assert_eq!(owning_folder(&folders, Path::new("/v/readme.md")), None);
    }

    #[test]
    fn classify_detects_created_modified_deleted() {
        let parent = PathBuf::from("/v/Parent");
        let gone = PathBuf::from("/v/Gone");
        let fresh = PathBuf::from("/v/Fresh");

        let mut known = HashMap::new();
        known.insert(parent.clone(), "lot:parent".to_string());
        known.insert(gone.clone(), "lot:gone".to_string());

        let mut current = HashMap::new();
        current.insert(parent.clone(), "lot:parent".to_string());
        current.insert(fresh.clone(), "lot:fresh".to_string());

        let mut changed = HashSet::new();
        changed.insert(fresh.join("001.md")); // the creation's own write
        changed.insert(parent.join("002.md")); // a modification
        changed.insert(gone.join("001.md")); // the deletion's own write

        let changes = classify(&known, &current, &changed);

        assert!(changes.contains(&Change {
            kind: ChangeKind::Created,
            id: Some("lot:fresh".to_string()),
        }));
        assert!(changes.contains(&Change {
            kind: ChangeKind::Deleted,
            id: Some("lot:gone".to_string()),
        }));
        assert!(changes.contains(&Change {
            kind: ChangeKind::Modified,
            id: Some("lot:parent".to_string()),
        }));
        // Exactly those three: the created/deleted folders are not double-counted
        // as modifications.
        assert_eq!(changes.len(), 3);
    }

    #[test]
    fn classify_falls_back_to_bare_modification() {
        // A non-empty batch that touches no Thing (e.g. the vault readme) still
        // yields one id-less modification so consumers refresh.
        let known = HashMap::new();
        let current = HashMap::new();
        let mut changed = HashSet::new();
        changed.insert(PathBuf::from("/v/readme.md"));

        let changes = classify(&known, &current, &changed);
        assert_eq!(
            changes,
            vec![Change {
                kind: ChangeKind::Modified,
                id: None,
            }]
        );
    }

    #[test]
    fn event_yaml_carries_state_updates_and_snapshot() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Buy milk", "get the oat one").unwrap();
        let id = thing.id().unwrap();

        let changes = vec![Change {
            kind: ChangeKind::Created,
            id: Some(id.clone()),
        }];
        let events = build_events(&vault, &changes).unwrap();
        assert_eq!(events.len(), 1);
        let yaml = events[0].to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();

        assert_eq!(value.get("kind").and_then(|v| v.as_str()), Some("created"));
        assert_eq!(value.get("id").and_then(|v| v.as_str()), Some(id.as_str()));
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
        // `things` mirrors `lot thing list` (path + tree).
        let things = value.get("things").expect("things snapshot present");
        assert_eq!(
            things.get("path").and_then(|v| v.as_str()),
            Some(vault.path().display().to_string().as_str())
        );
        assert_eq!(
            things
                .get("things")
                .and_then(|v| v.as_sequence())
                .map(|s| s.len()),
            Some(1)
        );
    }

    #[test]
    fn deleted_event_omits_state_and_updates() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        // A deletion references an id that is no longer in the vault; the event
        // carries only the snapshot and the id.
        let changes = vec![Change {
            kind: ChangeKind::Deleted,
            id: Some("lot:033GoneGoneGoneGoneGone".to_string()),
        }];
        let events = build_events(&vault, &changes).unwrap();
        assert_eq!(events.len(), 1);
        assert!(events[0].state.is_none());
        assert!(events[0].updates.is_none());

        let yaml = events[0].to_yaml().unwrap();
        let value: Value = serde_yaml_ng::from_str(&yaml).unwrap();
        assert_eq!(value.get("kind").and_then(|v| v.as_str()), Some("deleted"));
        assert!(value.get("state").is_none());
        assert!(value.get("updates").is_none());
        assert!(value.get("things").is_some());
    }

    #[test]
    fn added_update_is_a_modification_with_fresh_state() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "").unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, UpdateKind::Work, "on it").unwrap();

        let changes = vec![Change {
            kind: ChangeKind::Modified,
            id: Some(id.clone()),
        }];
        let events = build_events(&vault, &changes).unwrap();
        let value: Value = serde_yaml_ng::from_str(&events[0].to_yaml().unwrap()).unwrap();

        // The recomputed state reflects the newly added `work` update.
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
}
