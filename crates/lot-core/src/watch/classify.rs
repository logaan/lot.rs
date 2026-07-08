//! The classification phase of the watcher: turning a settled batch of
//! changed paths into [`Change`]s by diffing the folder→id snapshots, plus
//! the path-matching helpers it relies on.

use super::ChangeKind;
use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};

/// A classified change, before it is turned into a full [`super::WatchEvent`].
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct Change {
    pub(super) kind: ChangeKind,
    pub(super) id: Option<String>,
}

/// Classify a settled batch of `changed` paths against the `known` and
/// `current` folder→id maps.
///
/// A folder present in `current` but not `known` is a creation; one present in
/// `known` but not `current` is a deletion; any other changed path is attributed
/// to its deepest enclosing current Thing folder as a modification. A Thing's
/// id lives in its files, not its path, so a *moved* folder shows up on both
/// sides of that diff under the same id — that is a modification (its parent
/// may have changed), never a created/deleted pair: consumers key on id, and
/// the trailing `deleted` would make them drop the still-live node. When no
/// Thing can be pinned down but the batch was non-empty (e.g. a vault-level
/// file like the readme changed) a single [`ChangeKind::Reload`] is returned
/// so the consumer reloads its baseline.
pub(super) fn classify(
    known: &HashMap<PathBuf, String>,
    current: &HashMap<PathBuf, String>,
    changed: &HashSet<PathBuf>,
) -> Vec<Change> {
    let mut out = Vec::new();
    let mut accounted: HashSet<PathBuf> = HashSet::new();

    let known_ids: HashSet<&String> = known.values().collect();
    let current_ids: HashSet<&String> = current.values().collect();

    // Folders newly present: a brand-new id is a creation; a known id at a new
    // path is a move, reported as a modification.
    for (path, id) in current {
        if !known.contains_key(path) {
            out.push(Change {
                kind: if known_ids.contains(id) {
                    ChangeKind::Modified
                } else {
                    ChangeKind::Created
                },
                id: Some(id.clone()),
            });
            accounted.insert(path.clone());
        }
    }
    // Folders that vanished: a deletion only when the id is gone from the
    // vault entirely — an id still present elsewhere moved, reported above.
    for (path, id) in known {
        if !current.contains_key(path) {
            if !current_ids.contains(id) {
                out.push(Change {
                    kind: ChangeKind::Deleted,
                    id: Some(id.clone()),
                });
            }
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

    // Nothing mapped to a Thing, but the batch wasn't empty: ask the consumer
    // to reload its baseline so vault-level changes are still picked up.
    if out.is_empty() && !changed.is_empty() {
        out.push(Change {
            kind: ChangeKind::Reload,
            id: None,
        });
    }
    out
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
pub(super) fn is_git_path(path: &Path) -> bool {
    path.components().any(|c| c.as_os_str() == ".git")
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn classify_treats_move_as_modification() {
        // A moved folder appears on both sides of the path diff under the same
        // id. That must surface as one `modified` (new parent), never a
        // created/deleted pair — the trailing `deleted` would make an id-keyed
        // consumer drop the still-live node (the "items don't show up after
        // being moved" bug).
        let parent = PathBuf::from("/v/Parent");
        let old_item = PathBuf::from("/v/Item");
        let new_item = PathBuf::from("/v/Parent/Item");

        let mut known = HashMap::new();
        known.insert(parent.clone(), "lot:parent".to_string());
        known.insert(old_item.clone(), "lot:item".to_string());

        let mut current = HashMap::new();
        current.insert(parent.clone(), "lot:parent".to_string());
        current.insert(new_item.clone(), "lot:item".to_string());

        let mut changed = HashSet::new();
        changed.insert(old_item);
        changed.insert(new_item);

        let changes = classify(&known, &current, &changed);
        assert_eq!(
            changes,
            vec![Change {
                kind: ChangeKind::Modified,
                id: Some("lot:item".to_string()),
            }]
        );
    }

    #[test]
    fn classify_move_with_children_keeps_whole_subtree() {
        // Moving a folder moves its descendants too: every id survives at a
        // new path, so each yields a `modified` and none a `deleted`.
        let mut known = HashMap::new();
        known.insert(PathBuf::from("/v/Item"), "lot:item".to_string());
        known.insert(PathBuf::from("/v/Item/Child"), "lot:child".to_string());
        known.insert(PathBuf::from("/v/Parent"), "lot:parent".to_string());

        let mut current = HashMap::new();
        current.insert(PathBuf::from("/v/Parent/Item"), "lot:item".to_string());
        current.insert(
            PathBuf::from("/v/Parent/Item/Child"),
            "lot:child".to_string(),
        );
        current.insert(PathBuf::from("/v/Parent"), "lot:parent".to_string());

        let mut changed = HashSet::new();
        changed.insert(PathBuf::from("/v/Item"));
        changed.insert(PathBuf::from("/v/Parent/Item"));

        let changes = classify(&known, &current, &changed);
        assert_eq!(changes.len(), 2);
        for id in ["lot:item", "lot:child"] {
            assert!(changes.contains(&Change {
                kind: ChangeKind::Modified,
                id: Some(id.to_string()),
            }));
        }
        assert!(!changes.iter().any(|c| c.kind == ChangeKind::Deleted));
    }

    #[test]
    fn classify_falls_back_to_reload() {
        // A non-empty batch that touches no Thing (e.g. the vault readme) yields
        // a single id-less reload so consumers resync their baseline.
        let known = HashMap::new();
        let current = HashMap::new();
        let mut changed = HashSet::new();
        changed.insert(PathBuf::from("/v/readme.md"));

        let changes = classify(&known, &current, &changed);
        assert_eq!(
            changes,
            vec![Change {
                kind: ChangeKind::Reload,
                id: None,
            }]
        );
    }
}
