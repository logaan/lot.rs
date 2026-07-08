use crate::error::{io_err, Error, Result};
use crate::frontmatter::{shallow_merge, Document};
use crate::update::{build_update, UpdateType};
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

    /// The thing's name, derived from its folder name (the on-disk slug, e.g.
    /// `Buy_some_milk`).
    pub fn name(&self) -> String {
        self.path
            .file_name()
            .map(|n| n.to_string_lossy().into_owned())
            .unwrap_or_default()
    }

    /// The thing's immediate child things: sub-folders of this thing that
    /// themselves contain a `001.md`, sorted by folder name. (A thing's folder
    /// holds both its own numbered update files and any child folders.)
    pub fn children(&self) -> Result<Vec<Thing>> {
        let mut children = Vec::new();
        for entry in std::fs::read_dir(&self.path).map_err(io_err(&self.path))? {
            let entry = entry.map_err(io_err(&self.path))?;
            let path = entry.path();
            if path.is_dir() && path.join("001.md").exists() {
                children.push(Thing::new(path));
            }
        }
        children.sort_by_key(|t| t.name());
        Ok(children)
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

    /// Parse every update in the thing, oldest first (one [`Document`] per
    /// update file). Unlike [`compute_state`](Self::compute_state), which
    /// reduces the updates into a single merged state, this keeps each update
    /// separate so callers can render the thread as independent items.
    pub fn updates(&self) -> Result<Vec<Document>> {
        let mut docs = Vec::new();
        for path in self.update_paths()? {
            let raw = std::fs::read_to_string(&path).map_err(io_err(&path))?;
            docs.push(Document::parse(&raw)?);
        }
        Ok(docs)
    }

    /// The path of the update file whose `update-id` equals `id`, if this thing
    /// has one. Returns `Ok(None)` when no update in this thing carries that id
    /// (the caller decides whether to keep searching descendants).
    pub fn update_path_by_id(&self, id: &str) -> Result<Option<PathBuf>> {
        for path in self.update_paths()? {
            let raw = std::fs::read_to_string(&path).map_err(io_err(&path))?;
            let doc = Document::parse(&raw)?;
            if doc.frontmatter.get("update-id").and_then(|v| v.as_str()) == Some(id) {
                return Ok(Some(path));
            }
        }
        Ok(None)
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

    /// Write a new update of the given type, returning its path and `update-id`.
    ///
    /// `task_id` is recorded only for a Thing's first update (which
    /// establishes its id); pass `None` for ordinary updates. The caller is
    /// responsible for committing the change to git.
    pub fn add_update(
        &self,
        kind: &UpdateType,
        body: &str,
        task_id: Option<&str>,
    ) -> Result<(PathBuf, String)> {
        let number = self.next_update_number()?;
        let path = self.update_path(number);
        let doc = build_update(kind, body, task_id);
        let update_id = doc
            .frontmatter
            .get("update-id")
            .and_then(|v| v.as_str())
            .unwrap_or_default()
            .to_string();
        std::fs::write(&path, doc.render()?).map_err(io_err(&path))?;
        Ok((path, update_id))
    }

    /// The thing's display title: the first level-1 markdown heading (`# ...`)
    /// in its computed state, which is the human-readable name (with spaces)
    /// recorded in the `created` update. Falls back to the folder name (the
    /// on-disk slug) when there is no h1.
    pub fn title(&self) -> Result<String> {
        let state = self.compute_state()?;
        Ok(first_h1(&state.body).unwrap_or_else(|| self.name()))
    }

    /// The thing's current status, taken from the `status` field of its merged
    /// state. Falls back to `unknown` if no update set a status (every update
    /// `lot` writes sets one, so this only fires on hand-made files).
    pub fn status(&self) -> Result<String> {
        let state = self.compute_state()?;
        Ok(state
            .frontmatter
            .get("status")
            .and_then(|v| v.as_str())
            .unwrap_or("unknown")
            .to_string())
    }

    /// Compute the thing's current state by reducing over every update:
    /// shallow-merging frontmatter (newer keys win) and appending bodies.
    ///
    /// Each update's content is preceded by a header that separates it from the
    /// previous update, recording the update's number, type, timestamp and
    /// `update-id`:
    ///
    /// ```text
    /// --------------------------------------------------------------------------------
    /// 001 - note - 2026-05-31T14:06:42.600298+00:00 - lot:033QI8ChY3vGg0spUGXJlp
    /// --------------------------------------------------------------------------------
    /// ```
    pub fn compute_state(&self) -> Result<Document> {
        let mut merged = Document::default();
        let mut sections: Vec<String> = Vec::new();
        for path in self.update_paths()? {
            let raw = std::fs::read_to_string(&path).map_err(io_err(&path))?;
            let doc = Document::parse(&raw)?;
            shallow_merge(&mut merged.frontmatter, &doc.frontmatter);

            let header = crate::render::update_section_header(&path, &doc);
            let body = doc.body.trim();
            sections.push(if body.is_empty() {
                header
            } else {
                format!("{header}\n\n{body}")
            });
        }
        merged.body = if sections.is_empty() {
            String::new()
        } else {
            format!("{}\n", sections.join("\n\n"))
        };
        Ok(merged)
    }
}

/// The first level-1 markdown heading in `body`: the text after a line that
/// begins with exactly `# ` (one hash and a space, so `## ...` is ignored).
fn first_h1(body: &str) -> Option<String> {
    body.lines().find_map(|line| {
        let title = line.strip_prefix("# ")?.trim();
        (!title.is_empty()).then(|| title.to_string())
    })
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
