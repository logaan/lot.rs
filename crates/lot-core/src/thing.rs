use crate::error::{io_err, Error, Result};
use crate::frontmatter::{shallow_merge, Document};
use crate::update::{build_update, UpdateKind};
use std::path::{Path, PathBuf};

/// A Thing: a folder inside the vault containing sequentially numbered update
/// files.
#[derive(Debug, Clone)]
pub struct Thing {
    path: PathBuf,
}

impl Thing {
    pub(crate) fn new(path: PathBuf) -> Self {
        Self { path }
    }

    /// The filesystem path of the thing's folder.
    pub fn path(&self) -> &Path {
        &self.path
    }

    /// The thing's name, derived from its folder name.
    pub fn name(&self) -> String {
        self.path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    }

    /// The paths of all update files, sorted in numeric order.
    pub fn update_paths(&self) -> Result<Vec<PathBuf>> {
        let mut updates: Vec<(u32, PathBuf)> = Vec::new();
        for entry in std::fs::read_dir(&self.path).map_err(io_err(&self.path))? {
            let entry = entry.map_err(io_err(&self.path))?;
            let path = entry.path();
            if let Some(n) = update_number(&path) {
                updates.push((n, path));
            }
        }
        updates.sort_by_key(|(n, _)| *n);
        Ok(updates.into_iter().map(|(_, p)| p).collect())
    }

    /// The next update number (one higher than the most recent, or 1).
    pub fn next_update_number(&self) -> Result<u32> {
        let max = self
            .update_paths()?
            .iter()
            .filter_map(|p| update_number(p))
            .max()
            .unwrap_or(0);
        Ok(max + 1)
    }

    /// The path for an update with the given number, e.g. `001.md`.
    pub fn update_path(&self, number: u32) -> PathBuf {
        self.path.join(format!("{number:03}.md"))
    }

    /// Parse the `created` (first) update.
    pub fn created_update(&self) -> Result<Document> {
        let first = self.update_path(1);
        let raw = std::fs::read_to_string(&first).map_err(io_err(&first))?;
        Document::parse(&raw)
    }

    /// The thing's id, read from the `task-id` field of the created update.
    pub fn id(&self) -> Result<String> {
        let doc = self.created_update()?;
        doc.frontmatter
            .get("task-id")
            .and_then(|v| v.as_str())
            .map(|s| s.to_string())
            .ok_or_else(|| Error::ThingNotFound(self.name()))
    }

    /// Write a new update of the given kind and return its path.
    ///
    /// `task_id` is recorded only for [`UpdateKind::Created`] updates; pass
    /// `None` for ordinary updates. The caller is responsible for committing
    /// the change to git.
    pub fn add_update(
        &self,
        kind: UpdateKind,
        body: &str,
        task_id: Option<&str>,
    ) -> Result<PathBuf> {
        let number = self.next_update_number()?;
        let path = self.update_path(number);
        let doc = build_update(kind, body, task_id);
        std::fs::write(&path, doc.render()?).map_err(io_err(&path))?;
        Ok(path)
    }

    /// The thing's current status, taken from the `status` field of its merged
    /// state. Falls back to `created` if no update set a status.
    pub fn status(&self) -> Result<String> {
        let state = self.compute_state()?;
        Ok(state
            .frontmatter
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("created")
            .to_string())
    }

    /// Compute the thing's current state by reducing over every update:
    /// shallow-merging frontmatter (newer keys win) and appending bodies.
    pub fn compute_state(&self) -> Result<Document> {
        let mut merged = Document::default();
        let mut bodies: Vec<String> = Vec::new();
        for path in self.update_paths()? {
            let raw = std::fs::read_to_string(&path).map_err(io_err(&path))?;
            let doc = Document::parse(&raw)?;
            shallow_merge(&mut merged.frontmatter, &doc.frontmatter);
            let trimmed = doc.body.trim();
            if !trimmed.is_empty() {
                bodies.push(trimmed.to_string());
            }
        }
        merged.body = if bodies.is_empty() {
            String::new()
        } else {
            format!("{}\n", bodies.join("\n\n"))
        };
        Ok(merged)
    }
}

/// Extract the update number from a path like `.../012.md`.
fn update_number(path: &Path) -> Option<u32> {
    if path.extension().and_then(|e| e.to_str()) != Some("md") {
        return None;
    }
    path.file_stem()
        .and_then(|s| s.to_str())
        .and_then(|s| s.parse::<u32>().ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_update_numbers() {
        assert_eq!(update_number(Path::new("001.md")), Some(1));
        assert_eq!(update_number(Path::new("042.md")), Some(42));
        assert_eq!(update_number(Path::new("readme.md")), None);
        assert_eq!(update_number(Path::new("001.txt")), None);
    }
}
