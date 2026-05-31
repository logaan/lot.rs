use crate::error::{io_err, Error, Result};
use crate::git;
use crate::id;
use crate::thing::Thing;
use crate::update::{build_update, UpdateKind};
use std::path::{Path, PathBuf};

/// The readme written into a freshly created vault.
pub const NEW_VAULT_README: &str = include_str!("../../../data/new-vault-readme.md");

/// A vault: a git-backed directory that stores Things.
#[derive(Debug, Clone)]
pub struct Vault {
    path: PathBuf,
}

impl Vault {
    /// Open the vault at `path`, initialising it (folder, readme, git repo) if
    /// it does not yet exist.
    pub fn open(path: impl Into<PathBuf>) -> Result<Vault> {
        let vault = Vault { path: path.into() };
        if !vault.path.exists() {
            vault.initialize()?;
        }
        Ok(vault)
    }

    /// The vault's root path.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Create the vault directory, seed its readme, init git, and commit.
    fn initialize(&self) -> Result<()> {
        std::fs::create_dir_all(&self.path).map_err(io_err(&self.path))?;
        let readme = self.path.join("readme.md");
        std::fs::write(&readme, NEW_VAULT_README).map_err(io_err(&readme))?;
        if !git::is_repo(&self.path) {
            git::init(&self.path)?;
        }
        git::commit(&self.path, &[Path::new("readme.md")], "Initialise vault")?;
        Ok(())
    }

    /// Create a new top-level thing with `name` and an initial `created` update
    /// holding `contents`. Commits the new thing to the vault repo and returns
    /// it.
    ///
    /// The folder is named after a slugified `name` (whitespace becomes
    /// underscores), while the original `name` is preserved as the `created`
    /// update's h1 heading.
    pub fn new_thing(&self, name: &str, contents: &str) -> Result<Thing> {
        self.create_thing_in(&self.path, name, contents)
    }

    /// Create a new thing nested inside the thing identified by `parent_id`.
    /// The child's folder lives inside its parent's folder.
    pub fn new_child_thing(&self, parent_id: &str, name: &str, contents: &str) -> Result<Thing> {
        let parent = self.find_thing(parent_id)?;
        self.create_thing_in(parent.path(), name, contents)
    }

    /// Create a thing whose folder lives directly inside `base` (the vault root
    /// for a top-level thing, or a parent thing's folder for a child).
    fn create_thing_in(&self, base: &Path, name: &str, contents: &str) -> Result<Thing> {
        let trimmed = name.trim();
        if trimmed.is_empty() || trimmed.contains('/') || trimmed.contains('\\') {
            return Err(Error::InvalidThingName(name.to_string()));
        }

        let folder = slugify(trimmed);
        let dir = base.join(&folder);
        if dir.exists() {
            return Err(Error::ThingExists(folder.clone()));
        }
        std::fs::create_dir(&dir).map_err(io_err(&dir))?;

        let id = id::new();
        let body = created_body(trimmed, contents);
        let doc = build_update(UpdateKind::Created, &body, Some(&id));
        let update_path = dir.join("001.md");
        std::fs::write(&update_path, doc.render()?).map_err(io_err(&update_path))?;

        let rel = self.relative(&update_path);
        git::commit(&self.path, &[&rel], &create_commit_message(trimmed, &id))?;

        Ok(Thing::new(dir))
    }

    /// Iterate over all top-level things (immediate sub-folders of the vault
    /// that contain a `001.md`). Use [`Thing::children`] to descend.
    pub fn things(&self) -> Result<Vec<Thing>> {
        let mut things = Vec::new();
        for entry in std::fs::read_dir(&self.path).map_err(io_err(&self.path))? {
            let entry = entry.map_err(io_err(&self.path))?;
            let path = entry.path();
            if path.is_dir() && path.join("001.md").exists() {
                things.push(Thing::new(path));
            }
        }
        things.sort_by_key(|t| t.name());
        Ok(things)
    }

    /// Find a thing by its `task-id`, searching the whole tree (top-level
    /// things and their descendants). The lookup accepts ids with or without
    /// the `lot:` scheme; base62 ids are matched case-sensitively.
    pub fn find_thing(&self, id: &str) -> Result<Thing> {
        let target = crate::id::normalize(id);
        find_in(self.things()?, &target).ok_or_else(|| Error::ThingNotFound(id.to_string()))
    }

    /// Add an update to the thing identified by `id`, commit it, and return the
    /// new update's `update-id`.
    pub fn add_update(&self, id: &str, kind: UpdateKind, body: &str) -> Result<String> {
        let thing = self.find_thing(id)?;
        let (path, update_id) = thing.add_update(kind, body, None)?;
        let rel = self.relative(&path);
        git::commit(
            &self.path,
            &[&rel],
            &format!("Add {} update to {:?}", kind.status(), thing.name()),
        )?;
        Ok(update_id)
    }

    /// Make a path relative to the vault root, for passing to git.
    fn relative(&self, path: &Path) -> PathBuf {
        path.strip_prefix(&self.path)
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|_| path.to_path_buf())
    }
}

/// Depth-first search for a thing whose id equals `target`, descending into
/// each thing's children.
fn find_in(things: Vec<Thing>, target: &str) -> Option<Thing> {
    for thing in things {
        if thing.id().ok().as_deref() == Some(target) {
            return Some(thing);
        }
        if let Ok(children) = thing.children() {
            if let Some(found) = find_in(children, target) {
                return Some(found);
            }
        }
    }
    None
}

/// Turn a thing's name into a folder-safe slug: runs of whitespace collapse to
/// single underscores. e.g. `"Buy some milk"` -> `"Buy_some_milk"`.
fn slugify(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join("_")
}

/// Build the commit message for a newly created thing. The subject line is
/// `Create thing <name>`, with the name truncated (an ellipsis marking the cut)
/// so the whole subject is at most 50 characters. The thing's id goes on the
/// third line, after a blank line, keeping the subject short and scannable:
///
/// ```text
/// Create thing Buy some milk
///
/// lot:6Ic9Cg6kx0Xk2hQhVz3aBd
/// ```
fn create_commit_message(name: &str, id: &str) -> String {
    const MAX_SUBJECT: usize = 50;
    const PREFIX: &str = "Create thing ";
    let budget = MAX_SUBJECT - PREFIX.len();
    format!("{PREFIX}{}\n\n{id}", truncate_chars(name, budget))
}

/// Truncate `s` to at most `max` characters (counting Unicode scalar values).
/// When truncation happens the last kept character is replaced with `…` so the
/// result is never longer than `max` and the cut is visible.
fn truncate_chars(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max {
        return s.to_string();
    }
    if max == 0 {
        return String::new();
    }
    let mut out: String = chars[..max - 1].iter().collect();
    out.push('…');
    out
}

/// Build the body of the `created` update: the name as an h1 heading, followed
/// by the piped contents (if any).
fn created_body(name: &str, contents: &str) -> String {
    let contents = contents.trim();
    if contents.is_empty() {
        format!("# {name}\n")
    } else {
        format!("# {name}\n\n{contents}\n")
    }
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
        // `Vault::open` creates the repo and makes the initial commit, so a
        // committer identity must exist before we call it. Set it via env vars
        // so the test works on machines/CI with no global git identity, without
        // clobbering the developer's real git config.
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
    fn create_and_find_thing() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Buy milk", "remember the milk").unwrap();
        let id = thing.id().unwrap();
        let found = vault.find_thing(&id).unwrap();
        // The folder name is the slug; whitespace becomes underscores.
        assert_eq!(found.name(), "Buy_milk");
    }

    #[test]
    fn slugifies_folder_and_keeps_name_as_h1() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Buy some milk", "the contents").unwrap();
        // Folder: whitespace collapsed to underscores.
        assert_eq!(thing.name(), "Buy_some_milk");
        assert!(thing.path().ends_with("Buy_some_milk"));
        // Created update: name preserved (with spaces) as an h1, contents below.
        let body = thing.created_update().unwrap().body;
        assert_eq!(body, "# Buy some milk\n\nthe contents\n");
    }

    #[test]
    fn created_h1_without_contents() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Lonely task", "").unwrap();
        let body = thing.created_update().unwrap().body;
        assert_eq!(body, "# Lonely task\n");
    }

    #[test]
    fn slugify_collapses_whitespace() {
        assert_eq!(slugify("foo bar baz"), "foo_bar_baz");
        assert_eq!(slugify("  spaced   out  "), "spaced_out");
    }

    #[test]
    fn commit_message_short_name_fits_on_one_subject() {
        let msg = create_commit_message("Buy milk", "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        assert_eq!(msg, "Create thing Buy milk\n\nlot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        // Subject (first line) within the 50-char budget.
        assert!(msg.lines().next().unwrap().chars().count() <= 50);
        // Id is on the third line, after a blank second line.
        let lines: Vec<&str> = msg.lines().collect();
        assert_eq!(lines[1], "");
        assert_eq!(lines[2], "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
    }

    #[test]
    fn commit_message_truncates_long_name_to_50_char_subject() {
        let long = "Refactor the entire vault storage layer to support nested things";
        let msg = create_commit_message(long, "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        let subject = msg.lines().next().unwrap();
        assert_eq!(subject.chars().count(), 50);
        assert!(subject.starts_with("Create thing Refactor the entire vault"));
        assert!(subject.ends_with('…'));
    }

    #[test]
    fn duplicate_thing_errors() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        vault.new_thing("Dup", "").unwrap();
        assert!(matches!(
            vault.new_thing("Dup", ""),
            Err(Error::ThingExists(_))
        ));
    }

    #[test]
    fn add_update_returns_its_update_id() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing").unwrap();
        let id = thing.id().unwrap();
        let update_id = vault.add_update(&id, UpdateKind::Task, "step one").unwrap();
        // It returns the new update's id (not the file path)...
        assert!(update_id.starts_with("lot:"));
        // ...and that id is the one recorded in the freshly written update.
        let latest = thing.update_path(thing.next_update_number().unwrap() - 1);
        let doc =
            crate::frontmatter::Document::parse(&std::fs::read_to_string(latest).unwrap()).unwrap();
        assert_eq!(
            doc.frontmatter.get("update-id").and_then(|v| v.as_str()),
            Some(update_id.as_str())
        );
    }

    #[test]
    fn updates_merge_into_state() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing").unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, UpdateKind::Task, "step one").unwrap();
        vault.add_update(&id, UpdateKind::Done, "finished").unwrap();
        let state = thing.compute_state().unwrap();
        assert_eq!(
            state.frontmatter.get("status").unwrap().as_str(),
            Some("done")
        );
        assert!(state.body.contains("do the thing"));
        assert!(state.body.contains("finished"));
    }

    #[test]
    fn child_thing_nests_in_parent_folder_and_is_findable() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "").unwrap();
        let parent_id = parent.id().unwrap();
        let child = vault.new_child_thing(&parent_id, "Child", "kid").unwrap();
        let child_id = child.id().unwrap();

        // The child's folder lives inside the parent's folder.
        assert!(child.path().starts_with(parent.path()));
        assert!(child.path().ends_with("Child"));

        // The parent reports the child among its children.
        let children = parent.children().unwrap();
        assert_eq!(children.len(), 1);
        assert_eq!(children[0].id().unwrap(), child_id);

        // The parent's own update files are unaffected by the child folder.
        assert_eq!(parent.update_paths().unwrap().len(), 1);

        // find_thing locates the nested child by id.
        assert_eq!(vault.find_thing(&child_id).unwrap().id().unwrap(), child_id);
    }

    #[test]
    fn computed_body_separates_updates_with_headers() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing").unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, UpdateKind::Task, "step one").unwrap();
        let body = thing.compute_state().unwrap().body;

        // An 80-dash rule brackets each header.
        assert!(body.contains(&"-".repeat(80)));
        // Headers carry the number, type and a `lot:` update-id.
        assert!(body.contains("001 - created - "));
        assert!(body.contains("002 - task - "));
        assert!(body.contains(" - lot:"));
        // The created header precedes the task header.
        assert!(body.find("001 - created").unwrap() < body.find("002 - task").unwrap());
        // Bodies still appear, after their headers.
        assert!(body.find("001 - created").unwrap() < body.find("do the thing").unwrap());
        assert!(body.contains("step one"));
    }
}
