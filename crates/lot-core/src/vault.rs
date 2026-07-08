use crate::config::VAULT_CONFIG_RELATIVE_PATH;
use crate::error::{io_err, Error, Result};
use crate::git;
use crate::id;
use crate::thing::Thing;
use crate::update::{
    build_update, default_update_types, UpdateType, UpdateTypes, DEFAULT_INITIAL_TYPE_NAME,
};
use std::path::{Path, PathBuf};

mod format;
mod search;

use format::{
    create_commit_message, created_body, expand_path, slugify, thing_commit_message,
    vault_archive_message,
};
use search::{collect_terminal, find_in, find_update_in};

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

    /// Whether this vault commits its changes to git.
    pub fn auto_commit(&self) -> bool {
        self.auto_commit
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

/// The config seeded into a fresh vault (`<vault>/.lot/config.toml`): the
/// stock update types written out as explicit `[[update-types]]` entries plus
/// the `thing.default-update-type`, so the vault is self-describing and its
/// types can be edited, removed, or extended freely. This is the only way the
/// stock types reach a vault — `lot` itself has no built-in types and no
/// runtime fallback (a vault whose config defines none has no types at all,
/// and `lot` warns about it).
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

#[cfg(test)]
mod tests;
