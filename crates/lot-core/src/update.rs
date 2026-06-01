use crate::frontmatter::Document;
use chrono::Utc;
use serde_yaml_ng::Mapping;

/// The kind (type) of an update. Each kind maps to a `status` value and a
/// timestamp field that records when the update was made.
///
/// The lifecycle types are `note` → `work` → `info` → `done`:
/// `note` is the automatic first update of every thing (it carries the
/// `task-id`); `work` describes a task and records progress on it; `info`
/// records a conclusion or result; and `done` retires the thing (no body).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UpdateKind {
    /// The first update in every thing; records `task-id` and `note-at`.
    Note,
    Work,
    Info,
    Done,
}

impl UpdateKind {
    /// Whether this kind establishes a new thing and so records the `task-id`.
    pub fn is_note(self) -> bool {
        matches!(self, UpdateKind::Note)
    }

    /// The `status` string written into the update's frontmatter.
    pub fn status(self) -> &'static str {
        match self {
            UpdateKind::Note => "note",
            UpdateKind::Work => "work",
            UpdateKind::Info => "info",
            UpdateKind::Done => "done",
        }
    }

    /// The frontmatter key that records this update's timestamp, e.g.
    /// `work-at` or `done-at`.
    pub fn timestamp_field(self) -> &'static str {
        match self {
            UpdateKind::Note => "note-at",
            UpdateKind::Work => "work-at",
            UpdateKind::Info => "info-at",
            UpdateKind::Done => "done-at",
        }
    }

    /// Whether updates of this kind are allowed to carry body content. `done`
    /// (which retires the thing) is a bare marker.
    pub fn allows_body(self) -> bool {
        !matches!(self, UpdateKind::Done)
    }

    /// Parse a kind from the CLI sub-command name.
    pub fn from_name(name: &str) -> Option<UpdateKind> {
        match name {
            "work" => Some(UpdateKind::Work),
            "info" => Some(UpdateKind::Info),
            "done" => Some(UpdateKind::Done),
            _ => None,
        }
    }

    /// Parse a kind from the `status` string written into an update's
    /// frontmatter. Unlike [`from_name`], this recognises `note`.
    pub fn from_status(status: &str) -> Option<UpdateKind> {
        match status {
            "note" => Some(UpdateKind::Note),
            "work" => Some(UpdateKind::Work),
            "info" => Some(UpdateKind::Info),
            "done" => Some(UpdateKind::Done),
            _ => None,
        }
    }
}

/// Build the [`Document`] for a new update of the given kind.
///
/// `body` is the markdown content. For [`UpdateKind::Done`] the body is
/// ignored. Every update is stamped with a fresh `update-id`; the
/// [`UpdateKind::Note`] update additionally records the thing's `task-id`,
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
