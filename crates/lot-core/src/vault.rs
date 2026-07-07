use crate::config::VAULT_CONFIG_RELATIVE_PATH;
use crate::error::{io_err, Error, Result};
use crate::git;
use crate::id;
use crate::thing::Thing;
use crate::update::{
    build_update, default_update_types, UpdateType, UpdateTypes, DEFAULT_INITIAL_TYPE_NAME,
};
use std::path::{Path, PathBuf};

/// The readme written into a freshly created vault.
pub const NEW_VAULT_README: &str = include_str!("../../../data/new-vault-readme.md");

/// A vault: a git-backed directory that stores Things.
#[derive(Debug, Clone)]
pub struct Vault {
    path: PathBuf,
    /// Whether changes are committed to the vault's git repo. When `false`
    /// the vault never runs git — no `git init`, no commits — leaving the
    /// files to be versioned by an enclosing repo (or not at all).
    auto_commit: bool,
}

impl Vault {
    /// Open the vault at `path`, initialising it (folder, readme, git repo) if
    /// it does not yet exist, with changes committed automatically. Use
    /// [`Vault::open_with`] to control committing.
    pub fn open(path: impl Into<PathBuf>) -> Result<Vault> {
        Self::open_with(path, true)
    }

    /// Open the vault at `path`, initialising it if it does not yet exist.
    /// With `auto_commit` false the vault never touches git: initialising
    /// creates only the folder and its readme, and changes are written to disk
    /// without being committed.
    pub fn open_with(path: impl Into<PathBuf>, auto_commit: bool) -> Result<Vault> {
        let vault = Vault {
            path: path.into(),
            auto_commit,
        };
        if !vault.path.exists() {
            vault.initialize()?;
        }
        Ok(vault)
    }

    /// Create a brand-new vault at `path`, initialising it (folder, readme,
    /// git repo, initial commit). Unlike [`Vault::open`], this errors if
    /// anything already exists at `path`: a freshly created vault must be empty.
    ///
    /// A leading `~` in `path` is expanded against the user's home directory,
    /// matching how configured vault paths are resolved.
    pub fn create(path: &str) -> Result<Vault> {
        let vault = Vault {
            path: expand_path(path),
            auto_commit: true,
        };
        if vault.path.exists() {
            return Err(Error::VaultExists(vault.path.clone()));
        }
        vault.initialize()?;
        Ok(vault)
    }

    /// The vault's root path.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// Create the vault directory, seed its readme and its config (the stock
    /// update types plus the default update type — see
    /// [`default_vault_config`]); with auto-commit on, also init git and
    /// commit. With it off no repo is created — the vault may live inside
    /// (and be versioned by) an enclosing project repo.
    fn initialize(&self) -> Result<()> {
        std::fs::create_dir_all(&self.path).map_err(io_err(&self.path))?;
        let readme = self.path.join("readme.md");
        std::fs::write(&readme, NEW_VAULT_README).map_err(io_err(&readme))?;
        let config = self.path.join(VAULT_CONFIG_RELATIVE_PATH);
        if let Some(parent) = config.parent() {
            std::fs::create_dir_all(parent).map_err(io_err(parent))?;
        }
        std::fs::write(&config, default_vault_config()).map_err(io_err(&config))?;
        if !self.auto_commit {
            return Ok(());
        }
        if !git::is_repo(&self.path) {
            git::init(&self.path)?;
        }
        git::commit(
            &self.path,
            &[
                Path::new("readme.md"),
                Path::new(VAULT_CONFIG_RELATIVE_PATH),
            ],
            "Initialise vault",
        )?;
        Ok(())
    }

    /// Create a new top-level thing with `name` and an initial update of type
    /// `kind` (the vault's default update type, resolved by the caller from
    /// config) holding `contents`. Commits the new thing to the vault repo
    /// and returns it.
    ///
    /// The folder is named after a slugified `name` (whitespace becomes
    /// underscores), while the original `name` is preserved as the initial
    /// update's h1 heading.
    pub fn new_thing(&self, name: &str, contents: &str, kind: &UpdateType) -> Result<Thing> {
        self.create_thing_in(&self.path, name, contents, kind)
    }

    /// Create a new thing nested inside the thing identified by `parent_id`.
    /// The child's folder lives inside its parent's folder.
    pub fn new_child_thing(
        &self,
        parent_id: &str,
        name: &str,
        contents: &str,
        kind: &UpdateType,
    ) -> Result<Thing> {
        let parent = self.find_thing(parent_id)?;
        self.create_thing_in(parent.path(), name, contents, kind)
    }

    /// Create a thing whose folder lives directly inside `base` (the vault root
    /// for a top-level thing, or a parent thing's folder for a child).
    fn create_thing_in(
        &self,
        base: &Path,
        name: &str,
        contents: &str,
        kind: &UpdateType,
    ) -> Result<Thing> {
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
        let doc = build_update(kind, &body, Some(&id));
        let update_path = dir.join("001.md");
        std::fs::write(&update_path, doc.render()?).map_err(io_err(&update_path))?;

        let rel = self.relative(&update_path);
        self.commit(&[&rel], &create_commit_message(trimmed, &id))?;

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

    /// Find the filesystem path of an update file by its `update-id`, searching
    /// the whole tree (every thing and its descendants). The lookup accepts ids
    /// with or without the `lot:` scheme, matching [`find_thing`](Self::find_thing).
    pub fn find_update_path(&self, update_id: &str) -> Result<PathBuf> {
        let target = crate::id::normalize(update_id);
        find_update_in(self.things()?, &target)
            .ok_or_else(|| Error::UpdateNotFound(update_id.to_string()))
    }

    /// Add an update to the thing identified by `id`, commit it, and return the
    /// new update's `update-id`.
    pub fn add_update(&self, id: &str, kind: &UpdateType, body: &str) -> Result<String> {
        let thing = self.find_thing(id)?;
        let (path, update_id) = thing.add_update(kind, body, None)?;
        let rel = self.relative(&path);
        self.commit(
            &[&rel],
            &format!("Add {} update to {:?}", kind.name, thing.name()),
        )?;
        Ok(update_id)
    }

    /// Archive the thing identified by `id`: commit the thing and all its
    /// descendants (when they have uncommitted changes), delete their folders,
    /// and commit the deletion. Returns the archived thing's id.
    ///
    /// Archiving is defined by commits — the thing's history must be preserved
    /// in git before its files disappear — so no file is deleted until every
    /// commit has succeeded (the deletion is staged and committed with the
    /// files still on disk, and they are only removed afterwards). It follows
    /// that a vault with `auto-commit` disabled (which never runs git) refuses
    /// to archive.
    ///
    /// It is an error when `id` matches no thing, or names an update rather
    /// than a thing.
    pub fn archive_thing(&self, id: &str) -> Result<String> {
        if !self.auto_commit {
            return Err(Error::ArchiveNeedsAutoCommit);
        }
        let thing = self.find_thing_strict(id)?;
        let thing_id = thing.id()?;
        let title = thing.title()?;
        let rel = self.relative(thing.path());

        // Commit any uncommitted changes under the thing's folder (its own
        // updates and every descendant's) so nothing is lost from history.
        if git::has_changes(&self.path, &rel)? {
            git::commit(
                &self.path,
                &[&rel],
                &thing_commit_message("Commit changes to ", &title, &thing_id),
            )?;
        }

        // Commit the deletion while the files are still on disk; only delete
        // them once the commit exists. A failed commit deletes nothing.
        git::commit_removal(
            &self.path,
            &rel,
            &thing_commit_message("Archive thing ", &title, &thing_id),
        )?;
        std::fs::remove_dir_all(thing.path()).map_err(io_err(thing.path()))?;
        Ok(thing_id)
    }

    /// Archive every done thing in the vault: every thing whose current
    /// status is a terminal state (`done`, or a custom update type declared
    /// `terminal = true` — see [`UpdateTypes::status_is_terminal`]). Each
    /// selected thing takes its whole subtree with it, exactly as
    /// [`archive_thing`](Self::archive_thing) would, so a done thing nested
    /// inside another done thing is covered by its ancestor and only the
    /// outermost done things are selected.
    ///
    /// Like a single archive, this is defined by commits: first any
    /// uncommitted changes under each selected thing's folder are committed
    /// (one commit per thing, as `archive_thing` makes), then the deletion of
    /// **all** the selected folders is staged and committed in one commit,
    /// and only then are the folders removed from disk — if any commit fails,
    /// nothing has been deleted. A vault with `auto-commit` disabled refuses,
    /// as it cannot preserve history.
    ///
    /// Returns the archived things' ids in tree order — empty (with no
    /// commits made) when nothing is in a terminal state.
    pub fn archive_done_things(&self, types: &UpdateTypes) -> Result<Vec<String>> {
        if !self.auto_commit {
            return Err(Error::ArchiveNeedsAutoCommit);
        }
        let mut done = Vec::new();
        collect_terminal(self.things()?, types, &mut done)?;
        if done.is_empty() {
            return Ok(Vec::new());
        }

        let mut ids = Vec::new();
        let mut titles = Vec::new();
        let mut rels = Vec::new();
        for thing in &done {
            ids.push(thing.id()?);
            titles.push(thing.title()?);
            rels.push(self.relative(thing.path()));
        }

        // Commit any uncommitted changes under each selected folder (its own
        // updates and every descendant's) so nothing is lost from history.
        for (i, rel) in rels.iter().enumerate() {
            if git::has_changes(&self.path, rel)? {
                git::commit(
                    &self.path,
                    &[rel],
                    &thing_commit_message("Commit changes to ", &titles[i], &ids[i]),
                )?;
            }
        }

        // Commit the deletion of every selected folder in one commit while
        // the files are still on disk; only delete them once the commit
        // exists. A failed commit deletes nothing.
        let rel_refs: Vec<&Path> = rels.iter().map(PathBuf::as_path).collect();
        git::commit_removals(&self.path, &rel_refs, &vault_archive_message(&titles, &ids))?;
        for thing in &done {
            std::fs::remove_dir_all(thing.path()).map_err(io_err(thing.path()))?;
        }
        Ok(ids)
    }

    /// Move the thing identified by `id` — and, because a thing is a folder,
    /// its whole subtree of descendants — under the thing identified by
    /// `new_parent`, or to the top level of the vault when `new_parent` is
    /// `None`. Returns the moved thing's id.
    ///
    /// With auto-commit on, any uncommitted changes under the thing's folder
    /// are committed first (so the move commit contains nothing but the
    /// rename), then the rename is staged the way `git mv` would and
    /// committed — letting `git log --follow` track history across the move.
    /// If the commit fails the rename is undone and nothing has changed.
    /// With auto-commit off the folder is simply renamed on disk (like
    /// `new_thing`, the change is left for an enclosing repo to version).
    ///
    /// It is an error when either id matches no thing or names an update
    /// rather than a thing, when the destination is the thing itself or one
    /// of its own descendants (the move would orphan the subtree), when the
    /// thing is already directly under the destination (a no-op — rejected
    /// so typos don't silently "succeed"), or when the destination already
    /// contains a folder with the thing's name.
    pub fn move_thing(&self, id: &str, new_parent: Option<&str>) -> Result<String> {
        let thing = self.find_thing_strict(id)?;
        let thing_id = thing.id()?;
        let title = thing.title()?;

        let dest_base = match new_parent {
            Some(parent_id) => self.find_thing_strict(parent_id)?.path().to_path_buf(),
            None => self.path.clone(),
        };

        // A destination inside the thing's own folder (including the folder
        // itself) would detach the subtree from the tree: refuse.
        if dest_base.starts_with(thing.path()) {
            return Err(Error::MoveIntoSelf(thing_id));
        }
        if thing.path().parent() == Some(dest_base.as_path()) {
            return Err(Error::MoveSameParent(thing_id));
        }
        let dest = dest_base.join(thing.name());
        if dest.exists() {
            return Err(Error::MoveDestinationExists(thing.name()));
        }

        let old_rel = self.relative(thing.path());
        let new_rel = self.relative(&dest);

        // Commit any pending changes under the folder first so the move
        // commit is purely the rename (mirrors archive_thing).
        if self.auto_commit && git::has_changes(&self.path, &old_rel)? {
            git::commit(
                &self.path,
                &[&old_rel],
                &thing_commit_message("Commit changes to ", &title, &thing_id),
            )?;
        }

        std::fs::rename(thing.path(), &dest).map_err(io_err(thing.path()))?;

        if self.auto_commit {
            if let Err(err) = git::commit_move(
                &self.path,
                &old_rel,
                &new_rel,
                &thing_commit_message("Move thing ", &title, &thing_id),
            ) {
                // Undo the rename (best-effort) so a failed commit leaves the
                // vault exactly as it was found.
                let _ = std::fs::rename(&dest, thing.path());
                return Err(err);
            }
        }
        Ok(thing_id)
    }

    /// [`find_thing`](Self::find_thing), but when the id belongs to an update
    /// rather than a thing the error says so. Front-ends surface these
    /// messages directly, and pointing a thing command at an update id is an
    /// easy mistake to make.
    fn find_thing_strict(&self, id: &str) -> Result<Thing> {
        match self.find_thing(id) {
            Ok(thing) => Ok(thing),
            Err(Error::ThingNotFound(_)) if self.find_update_path(id).is_ok() => {
                Err(Error::NotAThingId(id.to_string()))
            }
            Err(err) => Err(err),
        }
    }

    /// Commit `paths` to the vault repo — unless auto-commit is disabled, in
    /// which case the changes are left on disk for the user to commit.
    fn commit(&self, paths: &[&Path], message: &str) -> Result<()> {
        if !self.auto_commit {
            return Ok(());
        }
        git::commit(&self.path, paths, message)
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

/// Depth-first walk collecting every thing whose status is terminal, without
/// descending into the collected things: their subtrees are archived with
/// them, so a nested terminal thing is already covered by its ancestor.
fn collect_terminal(things: Vec<Thing>, types: &UpdateTypes, out: &mut Vec<Thing>) -> Result<()> {
    for thing in things {
        if types.status_is_terminal(&thing.status()?) {
            out.push(thing);
        } else {
            collect_terminal(thing.children()?, types, out)?;
        }
    }
    Ok(())
}

/// Depth-first search for the path of the update file whose `update-id` equals
/// `target`, descending into each thing's children.
fn find_update_in(things: Vec<Thing>, target: &str) -> Option<PathBuf> {
    for thing in things {
        if let Ok(Some(path)) = thing.update_path_by_id(target) {
            return Some(path);
        }
        if let Ok(children) = thing.children() {
            if let Some(found) = find_update_in(children, target) {
                return Some(found);
            }
        }
    }
    None
}

/// Expand a leading `~` in a vault path against the user's home directory,
/// matching how configured vault paths are resolved (see
/// [`crate::config::Config::vault_path`]).
/// The config seeded into a fresh vault (`<vault>/.lot/config.toml`): the
/// stock update types written out as explicit `[[update-types]]` entries plus
/// the `thing.default-update-type`, so the vault is self-describing and its
/// types can be edited, removed, or extended freely. This is the only way the
/// stock types reach a vault — `lot` itself has no built-in types (an existing
/// vault whose config defines none falls back to the same stock set).
fn default_vault_config() -> String {
    let mut out = String::from(
        "# This vault's update types, used as `lot update <name>`. Each type has a\n\
         # name plus two flags: takes-body (does it accept a body; default true) and\n\
         # terminal (does it retire the Thing; default false). Edit, remove, or add\n\
         # entries freely — this list *is* the vault's set of types.\n",
    );
    for t in default_update_types() {
        out.push_str(&format!("\n[[update-types]]\nname = \"{}\"\n", t.name));
        if !t.takes_body {
            out.push_str("takes-body = false\n");
        }
        if t.terminal {
            out.push_str("terminal = true\n");
        }
    }
    out.push_str(&format!(
        "\n# The type `lot thing new` writes as a Thing's first update.\n\
         [thing]\n\
         default-update-type = \"{DEFAULT_INITIAL_TYPE_NAME}\"\n"
    ));
    out
}

fn expand_path(path: &str) -> PathBuf {
    PathBuf::from(shellexpand::tilde(path).into_owned())
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
    thing_commit_message("Create thing ", name, id)
}

/// Build a commit message about the thing called `name`: `<prefix><name>` as
/// the subject (the name truncated so the subject stays within 50 characters)
/// with the thing's id on the third line, after a blank line. See
/// [`create_commit_message`] for an example.
fn thing_commit_message(prefix: &str, name: &str, id: &str) -> String {
    const MAX_SUBJECT: usize = 50;
    let budget = MAX_SUBJECT.saturating_sub(prefix.chars().count());
    format!("{prefix}{}\n\n{id}", truncate_chars(name, budget))
}

/// Build the single commit message recording a vault-wide archive. The
/// subject counts the things going; the body lists each one — its
/// human-readable name and id — so the commit says exactly what it removed:
///
/// ```text
/// Archive 2 done things
///
/// Buy some milk (lot:6Ic9Cg6kx0Xk2hQhVz3aBd)
/// Old project (lot:0Kj2mn4pq6Rs8tu0vwx2yz)
/// ```
fn vault_archive_message(titles: &[String], ids: &[String]) -> String {
    let plural = if titles.len() == 1 { "" } else { "s" };
    let mut message = format!("Archive {} done thing{plural}\n", titles.len());
    for (title, id) in titles.iter().zip(ids) {
        message.push_str(&format!("\n{title} ({id})"));
    }
    message
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

/// Build the body of the `note` update: the name as an h1 heading, followed
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
    use crate::update::test_types::{done, info, note, work};
    use crate::update::UpdateType;

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
        let thing = vault
            .new_thing("Buy milk", "remember the milk", &note())
            .unwrap();
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
        let thing = vault
            .new_thing("Buy some milk", "the contents", &note())
            .unwrap();
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
        let thing = vault.new_thing("Lonely task", "", &note()).unwrap();
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
        vault.new_thing("Dup", "", &note()).unwrap();
        assert!(matches!(
            vault.new_thing("Dup", "", &note()),
            Err(Error::ThingExists(_))
        ));
    }

    /// Set a git committer identity via env vars so tests that commit work on
    /// machines/CI with no global git identity, without clobbering the real
    /// git config.
    fn set_git_identity() {
        for (k, v) in [
            ("GIT_AUTHOR_NAME", "Test"),
            ("GIT_AUTHOR_EMAIL", "test@example.com"),
            ("GIT_COMMITTER_NAME", "Test"),
            ("GIT_COMMITTER_EMAIL", "test@example.com"),
        ] {
            std::env::set_var(k, v);
        }
    }

    #[test]
    fn create_initialises_a_fresh_vault() {
        if !git_available() {
            return;
        }
        set_git_identity();
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("fresh-vault");
        let vault = Vault::create(path.to_str().unwrap()).unwrap();

        // The folder, its seeded readme, and the git repo all exist.
        assert!(vault.path().is_dir());
        assert!(vault.path().join("readme.md").is_file());
        assert!(vault.path().join(".git").exists());
    }

    #[test]
    fn create_errors_when_path_already_exists() {
        let dir = tempfile::tempdir().unwrap();
        // The temp dir itself already exists, so creating a vault there fails.
        assert!(matches!(
            Vault::create(dir.path().to_str().unwrap()),
            Err(Error::VaultExists(_))
        ));
    }

    #[test]
    fn without_auto_commit_the_vault_never_touches_git() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("vault");
        let vault = Vault::open_with(&path, false).unwrap();

        // Initialisation seeds the folder and readme but creates no git repo,
        // so a vault nested inside a project repo won't shadow it.
        assert!(vault.path().join("readme.md").is_file());
        assert!(!vault.path().join(".git").exists());

        // Things and updates are written to disk without any commits (git is
        // never run, so this works even with no git identity configured).
        let thing = vault.new_thing("Task", "do the thing", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &work(), "step one").unwrap();
        assert!(thing.path().join("002.md").is_file());
        assert!(!vault.path().join(".git").exists());
    }

    #[test]
    fn add_update_returns_its_update_id() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing", &note()).unwrap();
        let id = thing.id().unwrap();
        let update_id = vault.add_update(&id, &work(), "step one").unwrap();
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
    fn find_update_path_resolves_and_errors() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "", &note()).unwrap();
        let parent_id = parent.id().unwrap();
        // A nested child so the search must descend the tree.
        let child = vault
            .new_child_thing(&parent_id, "Child", "kid", &note())
            .unwrap();
        let child_id = child.id().unwrap();
        let update_id = vault.add_update(&child_id, &work(), "step one").unwrap();

        // The update id resolves to the file that recorded it (002.md on the
        // child, since 001.md is its created `note`).
        let path = vault.find_update_path(&update_id).unwrap();
        assert_eq!(path, child.path().join("002.md"));

        // It accepts an id without the `lot:` scheme, like `find_thing`.
        let bare = update_id.strip_prefix("lot:").unwrap();
        assert_eq!(vault.find_update_path(bare).unwrap(), path);

        // The created `note` update is also findable (001.md on the child).
        let note_doc = child.created_update().unwrap();
        let note_id = note_doc
            .frontmatter
            .get("update-id")
            .and_then(|v| v.as_str())
            .unwrap()
            .to_string();
        assert_eq!(
            vault.find_update_path(&note_id).unwrap(),
            child.path().join("001.md")
        );

        // An unknown id is an error.
        assert!(matches!(
            vault.find_update_path("lot:doesnotexist0000000"),
            Err(Error::UpdateNotFound(_))
        ));
    }

    #[test]
    fn updates_merge_into_state() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &work(), "step one").unwrap();
        vault.add_update(&id, &info(), "finished").unwrap();
        let state = thing.compute_state().unwrap();
        assert_eq!(
            state.frontmatter.get("status").unwrap().as_str(),
            Some("info")
        );
        assert!(state.body.contains("do the thing"));
        assert!(state.body.contains("finished"));
    }

    #[test]
    fn custom_update_type_drives_status_and_timestamp() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing", &note()).unwrap();
        let id = thing.id().unwrap();
        let custom = crate::update::UpdateType {
            name: "wont-do".into(),
            takes_body: false,
            terminal: true,
        };
        vault
            .add_update(&id, &custom, "ignored: takes no body")
            .unwrap();

        // The thing's status becomes the custom type's name — statuses are
        // the last update's type, custom or built-in alike.
        assert_eq!(thing.status().unwrap(), "wont-do");
        let state = thing.compute_state().unwrap();
        assert!(state.frontmatter.get("wont-do-at").is_some());
        // The computed-state header names the custom type; the body it
        // carried was dropped (takes-body = false).
        assert!(state.body.contains("002 - wont-do - "));
        assert!(!state.body.contains("ignored: takes no body"));
        // The commit message names the type too.
        assert!(commit_subjects(&vault)[0].starts_with("Add wont-do update"));
    }

    #[test]
    fn child_thing_nests_in_parent_folder_and_is_findable() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Parent", "", &note()).unwrap();
        let parent_id = parent.id().unwrap();
        let child = vault
            .new_child_thing(&parent_id, "Child", "kid", &note())
            .unwrap();
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

    /// `git log --format=%s` for the vault repo: the commit subjects, newest
    /// first.
    fn commit_subjects(vault: &Vault) -> Vec<String> {
        let out = std::process::Command::new("git")
            .arg("-C")
            .arg(vault.path())
            .args(["log", "--format=%s"])
            .output()
            .unwrap();
        String::from_utf8_lossy(&out.stdout)
            .lines()
            .map(|l| l.to_string())
            .collect()
    }

    /// `git status --porcelain` for the vault repo.
    fn porcelain_status(vault: &Vault) -> String {
        let out = std::process::Command::new("git")
            .arg("-C")
            .arg(vault.path())
            .args(["status", "--porcelain"])
            .output()
            .unwrap();
        String::from_utf8_lossy(&out.stdout).into_owned()
    }

    #[test]
    fn archive_removes_thing_and_descendants_and_commits() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let parent = vault.new_thing("Old project", "history", &note()).unwrap();
        let parent_id = parent.id().unwrap();
        let child = vault
            .new_child_thing(&parent_id, "Sub task", "kid", &note())
            .unwrap();

        let archived = vault.archive_thing(&parent_id).unwrap();
        assert_eq!(archived, parent_id);

        // The thing's folder (and its descendant's, nested inside) is gone...
        assert!(!parent.path().exists());
        assert!(!child.path().exists());
        // ...the deletion is committed (the repo is clean)...
        assert_eq!(porcelain_status(&vault), "");
        // ...and the archive commit records the thing's human-readable name.
        assert_eq!(commit_subjects(&vault)[0], "Archive thing Old project");
        // Everything was already committed, so no pending-changes commit.
        assert!(!commit_subjects(&vault)
            .iter()
            .any(|s| s.starts_with("Commit changes to")));
    }

    #[test]
    fn archive_commits_uncommitted_changes_first() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Dirty", "", &note()).unwrap();
        let id = thing.id().unwrap();
        // An uncommitted (untracked) file inside the thing's folder.
        std::fs::write(thing.path().join("002.md"), "---\nstatus: work\n---\n").unwrap();

        vault.archive_thing(&id).unwrap();

        assert!(!thing.path().exists());
        assert_eq!(porcelain_status(&vault), "");
        // The pending changes were committed before the deletion commit.
        let subjects = commit_subjects(&vault);
        assert_eq!(subjects[0], "Archive thing Dirty");
        assert_eq!(subjects[1], "Commit changes to Dirty");
    }

    #[test]
    fn archive_deletes_nothing_when_a_commit_fails() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Keeper", "precious", &note()).unwrap();
        let id = thing.id().unwrap();
        std::fs::write(thing.path().join("002.md"), "uncommitted").unwrap();

        // A stale index lock makes every git write (add/rm/commit) fail.
        let lock = vault.path().join(".git").join("index.lock");
        std::fs::write(&lock, "").unwrap();
        let err = vault.archive_thing(&id).unwrap_err();
        std::fs::remove_file(&lock).unwrap();

        assert!(matches!(err, Error::Git(_)));
        // Nothing was deleted: the folder and every file are still there.
        assert!(thing.path().join("001.md").is_file());
        assert!(thing.path().join("002.md").is_file());
    }

    #[test]
    fn archive_refuses_without_auto_commit() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Vault::open_with(dir.path().join("vault"), false).unwrap();
        let thing = vault.new_thing("Task", "", &note()).unwrap();
        let id = thing.id().unwrap();

        assert!(matches!(
            vault.archive_thing(&id),
            Err(Error::ArchiveNeedsAutoCommit)
        ));
        // Nothing was deleted.
        assert!(thing.path().join("001.md").is_file());
    }

    #[test]
    fn archive_rejects_unknown_and_update_ids() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "", &note()).unwrap();
        let id = thing.id().unwrap();
        let update_id = vault.add_update(&id, &work(), "step").unwrap();

        // An id nothing carries.
        assert!(matches!(
            vault.archive_thing("lot:doesnotexist0000000"),
            Err(Error::ThingNotFound(_))
        ));
        // An update id is called out specifically.
        assert!(matches!(
            vault.archive_thing(&update_id),
            Err(Error::NotAThingId(_))
        ));
        // Neither attempt deleted anything.
        assert!(thing.path().join("001.md").is_file());
    }

    #[test]
    fn archived_child_leaves_parent_intact() {
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

        vault.archive_thing(&child_id).unwrap();

        assert!(!child.path().exists());
        assert!(parent.path().join("001.md").is_file());
        assert_eq!(porcelain_status(&vault), "");
        // The parent is still findable; the child is not.
        assert!(vault.find_thing(&parent_id).is_ok());
        assert!(matches!(
            vault.find_thing(&child_id),
            Err(Error::ThingNotFound(_))
        ));
    }

    /// The full message (`%B`) of the vault repo's newest commit.
    fn last_commit_message(vault: &Vault) -> String {
        let out = std::process::Command::new("git")
            .arg("-C")
            .arg(vault.path())
            .args(["log", "--format=%B", "-1"])
            .output()
            .unwrap();
        String::from_utf8_lossy(&out.stdout).into_owned()
    }

    #[test]
    fn vault_archive_archives_all_done_things_in_one_commit() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let finished = vault.new_thing("Finished", "", &note()).unwrap();
        let finished_id = finished.id().unwrap();
        let also_done = vault.new_thing("Also done", "", &note()).unwrap();
        let also_done_id = also_done.id().unwrap();
        let active = vault.new_thing("Still going", "", &note()).unwrap();
        vault.add_update(&finished_id, &done(), "").unwrap();
        vault.add_update(&also_done_id, &done(), "").unwrap();

        let archived = vault.archive_done_things(&UpdateTypes::default()).unwrap();

        // Both done things went (in tree order — `things()` sorts by name);
        // the active one stayed.
        assert_eq!(archived, vec![also_done_id, finished_id]);
        assert!(!finished.path().exists());
        assert!(!also_done.path().exists());
        assert!(active.path().join("001.md").is_file());
        assert_eq!(porcelain_status(&vault), "");

        // Both deletions landed in one commit, whose body names each thing.
        let subjects = commit_subjects(&vault);
        assert_eq!(subjects[0], "Archive 2 done things");
        assert!(!subjects[1].starts_with("Archive"));
        let message = last_commit_message(&vault);
        assert!(message.contains("Also done (lot:"));
        assert!(message.contains("Finished (lot:"));
    }

    #[test]
    fn vault_archive_takes_subtrees_and_skips_nested_done_things() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        // A done parent whose child (done or not) goes with it.
        let done_parent = vault.new_thing("Done parent", "", &note()).unwrap();
        let done_parent_id = done_parent.id().unwrap();
        let dragged_child = vault
            .new_child_thing(&done_parent_id, "Dragged along", "", &note())
            .unwrap();
        let dragged_child_id = dragged_child.id().unwrap();
        vault.add_update(&dragged_child_id, &done(), "").unwrap();
        vault.add_update(&done_parent_id, &done(), "").unwrap();
        // An active parent whose done child is archived on its own.
        let active_parent = vault.new_thing("Active parent", "", &note()).unwrap();
        let active_parent_id = active_parent.id().unwrap();
        let done_child = vault
            .new_child_thing(&active_parent_id, "Done child", "", &note())
            .unwrap();
        let done_child_id = done_child.id().unwrap();
        vault.add_update(&done_child_id, &done(), "").unwrap();

        let archived = vault.archive_done_things(&UpdateTypes::default()).unwrap();

        // The nested done child is covered by its archived ancestor: only the
        // outermost done things are selected.
        assert_eq!(archived, vec![done_child_id, done_parent_id]);
        assert!(!done_parent.path().exists());
        assert!(!done_child.path().exists());
        assert!(active_parent.path().join("001.md").is_file());
        assert_eq!(porcelain_status(&vault), "");
    }

    #[test]
    fn vault_archive_commits_pending_changes_first() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Dirty done", "", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &done(), "").unwrap();
        // An uncommitted (untracked) file inside the thing's folder. It keeps
        // the thing's computed status terminal (`status` merges newest-wins).
        std::fs::write(thing.path().join("999.md"), "---\nstatus: done\n---\n").unwrap();

        vault.archive_done_things(&UpdateTypes::default()).unwrap();

        assert!(!thing.path().exists());
        assert_eq!(porcelain_status(&vault), "");
        // The pending changes were committed before the deletion commit.
        let subjects = commit_subjects(&vault);
        assert_eq!(subjects[0], "Archive 1 done thing");
        assert_eq!(subjects[1], "Commit changes to Dirty done");
    }

    #[test]
    fn vault_archive_with_nothing_done_makes_no_commits() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Ongoing", "", &note()).unwrap();
        let before = commit_subjects(&vault);

        let archived = vault.archive_done_things(&UpdateTypes::default()).unwrap();

        assert!(archived.is_empty());
        assert!(thing.path().join("001.md").is_file());
        assert_eq!(commit_subjects(&vault), before);
    }

    #[test]
    fn vault_archive_respects_custom_terminal_types() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let wont_do = UpdateType {
            name: "wont-do".to_string(),
            takes_body: false,
            terminal: true,
        };
        let blocked = UpdateType {
            name: "blocked".to_string(),
            takes_body: true,
            terminal: false,
        };
        let types = UpdateTypes::effective(&[], &[wont_do.clone(), blocked.clone()]).unwrap();

        let abandoned = vault.new_thing("Abandoned", "", &note()).unwrap();
        let abandoned_id = abandoned.id().unwrap();
        vault.add_update(&abandoned_id, &wont_do, "").unwrap();
        let stuck = vault.new_thing("Stuck", "", &note()).unwrap();
        let stuck_id = stuck.id().unwrap();
        vault.add_update(&stuck_id, &blocked, "why").unwrap();

        let archived = vault.archive_done_things(&types).unwrap();

        // The custom terminal status is archived; the non-terminal one stays.
        assert_eq!(archived, vec![abandoned_id]);
        assert!(!abandoned.path().exists());
        assert!(stuck.path().join("001.md").is_file());
    }

    #[test]
    fn vault_archive_deletes_nothing_when_a_commit_fails() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Keeper", "precious", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &done(), "").unwrap();

        // A stale index lock makes every git write (add/rm/commit) fail.
        let lock = vault.path().join(".git").join("index.lock");
        std::fs::write(&lock, "").unwrap();
        let err = vault
            .archive_done_things(&UpdateTypes::default())
            .unwrap_err();
        std::fs::remove_file(&lock).unwrap();

        assert!(matches!(err, Error::Git(_)));
        // Nothing was deleted.
        assert!(thing.path().join("001.md").is_file());
        assert!(thing.path().join("002.md").is_file());
    }

    #[test]
    fn vault_archive_refuses_without_auto_commit() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Vault::open_with(dir.path().join("vault"), false).unwrap();
        let thing = vault.new_thing("Task", "", &note()).unwrap();

        assert!(matches!(
            vault.archive_done_things(&UpdateTypes::default()),
            Err(Error::ArchiveNeedsAutoCommit)
        ));
        // Nothing was deleted.
        assert!(thing.path().join("001.md").is_file());
    }

    #[test]
    fn move_reparents_thing_under_new_parent_and_commits() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let home = vault.new_thing("Home", "", &note()).unwrap();
        let home_id = home.id().unwrap();
        let task = vault.new_thing("Fix gate", "", &note()).unwrap();
        let task_id = task.id().unwrap();

        let moved = vault.move_thing(&task_id, Some(&home_id)).unwrap();
        assert_eq!(moved, task_id);

        // The thing is still findable and now lives inside its new parent.
        let found = vault.find_thing(&task_id).unwrap();
        assert_eq!(found.path(), home.path().join("Fix_gate"));
        assert!(!task.path().exists());
        assert_eq!(home.children().unwrap().len(), 1);

        // The move is committed and the repo is clean.
        assert_eq!(porcelain_status(&vault), "");
        assert_eq!(commit_subjects(&vault)[0], "Move thing Fix gate");
        // Everything was already committed, so no pending-changes commit.
        assert!(!commit_subjects(&vault)
            .iter()
            .any(|s| s.starts_with("Commit changes to")));
    }

    #[test]
    fn move_to_root_promotes_a_child() {
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

        vault.move_thing(&child_id, None).unwrap();

        let found = vault.find_thing(&child_id).unwrap();
        assert_eq!(found.path(), vault.path().join("Child"));
        assert!(parent.children().unwrap().is_empty());
        assert_eq!(porcelain_status(&vault), "");
    }

    #[test]
    fn move_carries_the_whole_subtree() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let project = vault.new_thing("Project", "", &note()).unwrap();
        let project_id = project.id().unwrap();
        let sub = vault
            .new_child_thing(&project_id, "Sub", "", &note())
            .unwrap();
        let sub_id = sub.id().unwrap();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();

        vault.move_thing(&project_id, Some(&dest_id)).unwrap();

        // The descendant moved with its parent and is still findable.
        let found_sub = vault.find_thing(&sub_id).unwrap();
        assert_eq!(found_sub.path(), dest.path().join("Project").join("Sub"));
        assert_eq!(porcelain_status(&vault), "");
    }

    #[test]
    fn move_history_survives_via_follow() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let home = vault.new_thing("Home", "", &note()).unwrap();
        let home_id = home.id().unwrap();
        let task = vault.new_thing("Task", "", &note()).unwrap();
        let task_id = task.id().unwrap();

        vault.move_thing(&task_id, Some(&home_id)).unwrap();

        // The rename was staged git-mv style, so `git log --follow` reaches
        // the creation commit through the move.
        let out = std::process::Command::new("git")
            .arg("-C")
            .arg(vault.path())
            .args(["log", "--follow", "--format=%s", "--", "Home/Task/001.md"])
            .output()
            .unwrap();
        let subjects = String::from_utf8_lossy(&out.stdout);
        assert!(subjects.contains("Create thing Task"), "got: {subjects}");
    }

    #[test]
    fn move_commits_pending_changes_first() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();
        let thing = vault.new_thing("Dirty", "", &note()).unwrap();
        let id = thing.id().unwrap();
        // An uncommitted (untracked) file inside the thing's folder.
        std::fs::write(thing.path().join("002.md"), "---\nstatus: work\n---\n").unwrap();

        vault.move_thing(&id, Some(&dest_id)).unwrap();

        assert_eq!(porcelain_status(&vault), "");
        // The pending changes were committed before the move commit, so the
        // move commit is purely the rename.
        let subjects = commit_subjects(&vault);
        assert_eq!(subjects[0], "Move thing Dirty");
        assert_eq!(subjects[1], "Commit changes to Dirty");
    }

    #[test]
    fn move_rejects_itself_and_its_descendants() {
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

        // Under itself.
        assert!(matches!(
            vault.move_thing(&parent_id, Some(&parent_id)),
            Err(Error::MoveIntoSelf(_))
        ));
        // Under its own descendant.
        assert!(matches!(
            vault.move_thing(&parent_id, Some(&child_id)),
            Err(Error::MoveIntoSelf(_))
        ));
        // Nothing moved.
        assert_eq!(child.path(), parent.path().join("Child"));
        assert!(child.path().is_dir());
    }

    #[test]
    fn move_rejects_a_no_op() {
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
        let top = vault.new_thing("Top", "", &note()).unwrap();
        let top_id = top.id().unwrap();

        // Already directly under that parent.
        assert!(matches!(
            vault.move_thing(&child_id, Some(&parent_id)),
            Err(Error::MoveSameParent(_))
        ));
        // Already at the top level.
        assert!(matches!(
            vault.move_thing(&top_id, None),
            Err(Error::MoveSameParent(_))
        ));
    }

    #[test]
    fn move_rejects_destination_name_collisions() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();
        // The destination already holds a thing whose folder is `Twin`.
        vault
            .new_child_thing(&dest_id, "Twin", "", &note())
            .unwrap();
        let mover = vault.new_thing("Twin", "", &note()).unwrap();
        let mover_id = mover.id().unwrap();

        assert!(matches!(
            vault.move_thing(&mover_id, Some(&dest_id)),
            Err(Error::MoveDestinationExists(_))
        ));
        // Nothing moved.
        assert!(mover.path().is_dir());
    }

    #[test]
    fn move_rejects_unknown_and_update_ids() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "", &note()).unwrap();
        let id = thing.id().unwrap();
        let update_id = vault.add_update(&id, &work(), "step").unwrap();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();

        // An id nothing carries — as the thing and as the parent.
        assert!(matches!(
            vault.move_thing("lot:doesnotexist0000000", Some(&dest_id)),
            Err(Error::ThingNotFound(_))
        ));
        assert!(matches!(
            vault.move_thing(&id, Some("lot:doesnotexist0000000")),
            Err(Error::ThingNotFound(_))
        ));
        // An update id is called out specifically — in either position.
        assert!(matches!(
            vault.move_thing(&update_id, Some(&dest_id)),
            Err(Error::NotAThingId(_))
        ));
        assert!(matches!(
            vault.move_thing(&id, Some(&update_id)),
            Err(Error::NotAThingId(_))
        ));
        // Nothing moved.
        assert_eq!(thing.path(), vault.path().join("Task"));
        assert!(thing.path().is_dir());
    }

    #[test]
    fn move_without_auto_commit_renames_on_disk_only() {
        let dir = tempfile::tempdir().unwrap();
        let vault = Vault::open_with(dir.path().join("vault"), false).unwrap();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();
        let thing = vault.new_thing("Task", "", &note()).unwrap();
        let id = thing.id().unwrap();

        // Unlike archive, a move works without git: it is just a rename,
        // representable as working-tree state for an enclosing repo.
        vault.move_thing(&id, Some(&dest_id)).unwrap();

        assert!(!thing.path().exists());
        assert!(dest.path().join("Task").join("001.md").is_file());
        assert!(!vault.path().join(".git").exists());
    }

    #[test]
    fn move_rolls_back_when_the_commit_fails() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let dest = vault.new_thing("Dest", "", &note()).unwrap();
        let dest_id = dest.id().unwrap();
        let thing = vault.new_thing("Task", "precious", &note()).unwrap();
        let id = thing.id().unwrap();

        // A stale index lock makes every git write (add/rm/commit) fail.
        let lock = vault.path().join(".git").join("index.lock");
        std::fs::write(&lock, "").unwrap();
        let err = vault.move_thing(&id, Some(&dest_id)).unwrap_err();
        std::fs::remove_file(&lock).unwrap();

        assert!(matches!(err, Error::Git(_)));
        // The rename was undone: the thing is back where it started.
        assert!(thing.path().join("001.md").is_file());
        assert!(!dest.path().join("Task").exists());
    }

    #[test]
    fn thing_commit_message_truncates_like_create() {
        let msg = thing_commit_message("Archive thing ", "Buy milk", "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        assert_eq!(msg, "Archive thing Buy milk\n\nlot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        let long = "A very long thing name that will not fit inside a fifty character subject";
        let msg = thing_commit_message("Archive thing ", long, "lot:id");
        let subject = msg.lines().next().unwrap();
        assert_eq!(subject.chars().count(), 50);
        assert!(subject.ends_with('…'));
    }

    #[test]
    fn computed_body_separates_updates_with_headers() {
        if !git_available() {
            return;
        }
        let (_dir, vault) = configured_temp_vault();
        let thing = vault.new_thing("Task", "do the thing", &note()).unwrap();
        let id = thing.id().unwrap();
        vault.add_update(&id, &work(), "step one").unwrap();
        let body = thing.compute_state().unwrap().body;

        // An 80-dash rule brackets each header.
        assert!(body.contains(&"-".repeat(80)));
        // Headers carry the number, type and a `lot:` update-id.
        assert!(body.contains("001 - note - "));
        assert!(body.contains("002 - work - "));
        assert!(body.contains(" - lot:"));
        // The created header precedes the task header.
        assert!(body.find("001 - note").unwrap() < body.find("002 - work").unwrap());
        // Bodies still appear, after their headers.
        assert!(body.find("001 - note").unwrap() < body.find("do the thing").unwrap());
        assert!(body.contains("step one"));
    }
}
