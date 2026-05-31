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

    /// Create a new thing with `name` and an initial `created` update holding
    /// `contents`. Commits the new thing to the vault repo and returns it.
    pub fn new_thing(&self, name: &str, contents: &str) -> Result<Thing> {
        let trimmed = name.trim();
        if trimmed.is_empty() || trimmed.contains('/') || trimmed.contains('\\') {
            return Err(Error::InvalidThingName(name.to_string()));
        }

        let dir = self.path.join(trimmed);
        if dir.exists() {
            return Err(Error::ThingExists(trimmed.to_string()));
        }
        std::fs::create_dir(&dir).map_err(io_err(&dir))?;

        let id = id::new();
        let doc = build_update(UpdateKind::Created, contents, Some(&id));
        let update_path = dir.join("001.md");
        std::fs::write(&update_path, doc.render()?).map_err(io_err(&update_path))?;

        let rel = self.relative(&update_path);
        git::commit(
            &self.path,
            &[&rel],
            &format!("Create thing {trimmed:?} ({id})"),
        )?;

        Ok(Thing::new(dir))
    }

    /// Iterate over all things (immediate sub-folders that contain a `001.md`).
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

    /// Find a thing by its `task-id`. The lookup accepts ids with or without
    /// the `lot:` scheme; base62 ids are matched case-sensitively.
    pub fn find_thing(&self, id: &str) -> Result<Thing> {
        let target = crate::id::normalize(id);
        for thing in self.things()? {
            if let Ok(found) = thing.id() {
                if found == target {
                    return Ok(thing);
                }
            }
        }
        Err(Error::ThingNotFound(id.to_string()))
    }

    /// Add an update to the thing identified by `id`, then commit it.
    pub fn add_update(&self, id: &str, kind: UpdateKind, body: &str) -> Result<PathBuf> {
        let thing = self.find_thing(id)?;
        let path = thing.add_update(kind, body, None)?;
        let rel = self.relative(&path);
        git::commit(
            &self.path,
            &[&rel],
            &format!("Add {} update to {:?}", kind.status(), thing.name()),
        )?;
        Ok(path)
    }

    /// Make a path relative to the vault root, for passing to git.
    fn relative(&self, path: &Path) -> PathBuf {
        path.strip_prefix(&self.path)
            .map(|p| p.to_path_buf())
            .unwrap_or_else(|_| path.to_path_buf())
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
        let vault = Vault::open(&path).unwrap();
        // Ensure commits work inside the throwaway repo.
        for (k, v) in [("user.email", "test@example.com"), ("user.name", "Test")] {
            std::process::Command::new("git")
                .arg("-C")
                .arg(&path)
                .args(["config", k, v])
                .output()
                .unwrap();
        }
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
        assert_eq!(found.name(), "Buy milk");
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
}
