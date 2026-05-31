use crate::frontmatter::Document;
use chrono::Utc;
use serde_yaml_ng::Mapping;

/// The kind (type) of an update. Each kind maps to a `status` value and a
/// timestamp field that records when the update was made.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateKind {
    /// The first update in every thing; records `id` and `created-at`.
    Created,
    Task,
    Doing,
    Done,
    Archive,
}

impl UpdateKind {
    /// Whether this kind establishes a new thing and so records the `task-id`.
    pub fn is_created(self) -> bool {
        matches!(self, UpdateKind::Created)
    }

    /// The `status` string written into the update's frontmatter.
    pub fn status(self) -> &'static str {
        match self {
            UpdateKind::Created => "created",
            UpdateKind::Task => "task",
            UpdateKind::Doing => "doing",
            UpdateKind::Done => "done",
            UpdateKind::Archive => "archive",
        }
    }

    /// The frontmatter key that records this update's timestamp, e.g.
    /// `task-at` or `archived-at`.
    pub fn timestamp_field(self) -> &'static str {
        match self {
            UpdateKind::Created => "created-at",
            UpdateKind::Task => "task-at",
            UpdateKind::Doing => "doing-at",
            UpdateKind::Done => "done-at",
            UpdateKind::Archive => "archived-at",
        }
    }

    /// Whether updates of this kind are allowed to carry body content.
    pub fn allows_body(self) -> bool {
        !matches!(self, UpdateKind::Archive)
    }

    /// Parse a kind from the CLI sub-command name.
    pub fn from_name(name: &str) -> Option<UpdateKind> {
        match name {
            "task" => Some(UpdateKind::Task),
            "doing" => Some(UpdateKind::Doing),
            "done" => Some(UpdateKind::Done),
            "archive" => Some(UpdateKind::Archive),
            _ => None,
        }
    }
}

/// Build the [`Document`] for a new update of the given kind.
///
/// `body` is the markdown content. For [`UpdateKind::Archive`] the body is
/// ignored. Every update is stamped with a fresh `update-id`; the
/// [`UpdateKind::Created`] update additionally records the thing's `task-id`,
/// which must be supplied via `task_id`.
pub fn build_update(kind: UpdateKind, body: &str, task_id: Option<&str>) -> Document {
    let mut fm = Mapping::new();
    fm.insert("status".into(), kind.status().into());
    if let Some(task_id) = task_id {
        fm.insert("task-id".into(), task_id.into());
    }
    fm.insert("update-id".into(), crate::id::new().into());
    fm.insert(
        kind.timestamp_field().into(),
        Utc::now().to_rfc3339().into(),
    );

    let body = if kind.allows_body() {
        body.to_string()
    } else {
        String::new()
    };
    Document::new(fm, body)
}
