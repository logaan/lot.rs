use crate::error::{Error, Result};
use crate::frontmatter::Document;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_yaml_ng::Mapping;

/// The kind (type) of an update. Each kind maps to a `status` value and a
/// timestamp field that records when the update was made.
///
/// The built-in lifecycle types are `note` → `work` → `info` → `done`:
/// `note` is the automatic first update of every thing (it carries the
/// `task-id`); `work` describes a task and records progress on it; `info`
/// records a conclusion or result; and `done` retires the thing (no body).
///
/// Beyond the built-ins, config can define additional types (see
/// [`UpdateType`] and [`UpdateTypes`]); those are represented by the
/// [`UpdateKind::Custom`] variant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum UpdateKind {
    /// The first update in every thing; records `task-id` and `note-at`.
    Note,
    Work,
    Info,
    Done,
    /// A config-defined type (see [`UpdateTypes::resolve`]).
    Custom(UpdateType),
}

/// The names of the built-in update types, which config can never redefine.
pub const BUILTIN_TYPE_NAMES: [&str; 4] = ["note", "work", "info", "done"];

/// Names that a config-defined update type may not use even though they are
/// not update types themselves: they collide with `lot update` sub-commands.
const RESERVED_TYPE_NAMES: [&str; 1] = ["path"];

impl UpdateKind {
    /// Whether this kind establishes a new thing and so records the `task-id`.
    pub fn is_note(&self) -> bool {
        matches!(self, UpdateKind::Note)
    }

    /// The `status` string written into the update's frontmatter.
    pub fn status(&self) -> &str {
        match self {
            UpdateKind::Note => "note",
            UpdateKind::Work => "work",
            UpdateKind::Info => "info",
            UpdateKind::Done => "done",
            UpdateKind::Custom(t) => &t.name,
        }
    }

    /// The frontmatter key that records this update's timestamp, e.g.
    /// `work-at` or `done-at`. Custom types follow the same `<name>-at`
    /// convention as the built-ins.
    pub fn timestamp_field(&self) -> String {
        timestamp_field_for(self.status())
    }

    /// Whether updates of this kind are allowed to carry body content. `done`
    /// (which retires the thing) is a bare marker; a custom type declares this
    /// with its `takes-body` flag.
    pub fn allows_body(&self) -> bool {
        match self {
            UpdateKind::Done => false,
            UpdateKind::Custom(t) => t.takes_body,
            _ => true,
        }
    }

    /// Whether this kind is a terminal state — one that retires the thing for
    /// the purposes of status display and bulk archiving in front-ends. Among
    /// the built-ins only `done` is terminal; a custom type declares this with
    /// its `terminal` flag.
    pub fn is_terminal(&self) -> bool {
        match self {
            UpdateKind::Done => true,
            UpdateKind::Custom(t) => t.terminal,
            _ => false,
        }
    }
}

/// The frontmatter key that records the timestamp of an update whose `status`
/// is `status`: always `<status>-at` (`note-at`, `work-at`, …), for custom
/// types exactly as for built-ins.
pub fn timestamp_field_for(status: &str) -> String {
    format!("{status}-at")
}

/// A config-defined update type: its name plus the flags that govern how it
/// behaves. This is both the TOML shape under `[[update-types]]` and the
/// definition carried by [`UpdateKind::Custom`].
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct UpdateType {
    /// The type's name: the `lot update <name>` sub-command, the `status`
    /// written into its updates' frontmatter, and the stem of its `<name>-at`
    /// timestamp field.
    pub name: String,

    /// Whether updates of this type accept a body (like `work`/`info`).
    /// Defaults to `true`; set it to `false` for a bare marker like `done`.
    #[serde(default = "default_takes_body", rename = "takes-body")]
    pub takes_body: bool,

    /// Whether this type counts as a terminal state — like `done` — for
    /// status display and for front-ends' "bulk archive things in terminal
    /// states". Defaults to `false`.
    #[serde(default)]
    pub terminal: bool,
}

/// The default for an update type's `takes-body` flag: bodies are accepted.
fn default_takes_body() -> bool {
    true
}

/// One entry in the machine-readable list of effective update types emitted by
/// `lot settings get` (the `update-types` key): everything a front-end needs to
/// know about a type without understanding config files.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct UpdateTypeInfo {
    pub name: String,
    #[serde(rename = "takes-body")]
    pub takes_body: bool,
    pub terminal: bool,
    #[serde(rename = "built-in")]
    pub built_in: bool,
}

/// The full effective set of update types: the four built-ins plus the custom
/// types defined in config (user-level overlaid by vault-level).
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct UpdateTypes {
    custom: Vec<UpdateType>,
}

impl UpdateTypes {
    /// Merge the user-level and vault-level `[[update-types]]` lists into the
    /// effective set, validating every definition.
    ///
    /// Definitions are merged by name: the user's list is taken first, then
    /// the vault's list extends it, with a vault definition **replacing** a
    /// same-named user definition in place (mirroring how vault-level config
    /// wins field-by-field elsewhere). A repeated name within one list is
    /// likewise replaced by the later entry.
    ///
    /// Validation is a hard error, so misconfiguration is not silently
    /// ignored: a name must start with a lowercase ASCII letter and contain
    /// only lowercase letters, digits, and hyphens; the built-in types
    /// (`note`/`work`/`info`/`done`) cannot be redefined; and `path` is
    /// reserved (it collides with the `lot update path` command).
    pub fn effective(user: &[UpdateType], vault: &[UpdateType]) -> Result<UpdateTypes> {
        let mut custom: Vec<UpdateType> = Vec::new();
        for t in user.iter().chain(vault) {
            validate_type(t)?;
            match custom.iter_mut().find(|c| c.name == t.name) {
                Some(existing) => *existing = t.clone(),
                None => custom.push(t.clone()),
            }
        }
        Ok(UpdateTypes { custom })
    }

    /// The custom (config-defined) types, in definition order.
    pub fn custom(&self) -> &[UpdateType] {
        &self.custom
    }

    /// Resolve an update type by name for `lot update <name>`.
    ///
    /// Recognises the creatable built-ins (`work`/`info`/`done` — `note` is
    /// only ever created by `lot thing new`) and every custom type. An
    /// unknown name is an error whose message lists the known types.
    pub fn resolve(&self, name: &str) -> Result<UpdateKind> {
        match name {
            "work" => return Ok(UpdateKind::Work),
            "info" => return Ok(UpdateKind::Info),
            "done" => return Ok(UpdateKind::Done),
            _ => {}
        }
        if let Some(t) = self.custom.iter().find(|t| t.name == name) {
            return Ok(UpdateKind::Custom(t.clone()));
        }
        let known: Vec<&str> = ["work", "info", "done"]
            .into_iter()
            .chain(self.custom.iter().map(|t| t.name.as_str()))
            .collect();
        Err(Error::UnknownUpdateType(name.to_string(), known.join(", ")))
    }

    /// Whether `status` names a terminal state: the built-in `done`, or a
    /// custom type declared with `terminal = true`. A status this set doesn't
    /// know (e.g. a custom type since removed from config) is not terminal —
    /// erring towards keeping things is the safe default for bulk archiving.
    pub fn status_is_terminal(&self, status: &str) -> bool {
        status == "done" || self.custom.iter().any(|t| t.name == status && t.terminal)
    }

    /// The full effective set — built-ins first (in lifecycle order), then the
    /// custom types in definition order — as the machine-readable entries
    /// `lot settings get` emits.
    pub fn infos(&self) -> Vec<UpdateTypeInfo> {
        let builtin = |kind: UpdateKind| UpdateTypeInfo {
            name: kind.status().to_string(),
            takes_body: kind.allows_body(),
            terminal: kind.is_terminal(),
            built_in: true,
        };
        [
            UpdateKind::Note,
            UpdateKind::Work,
            UpdateKind::Info,
            UpdateKind::Done,
        ]
        .into_iter()
        .map(builtin)
        .chain(self.custom.iter().map(|t| UpdateTypeInfo {
            name: t.name.clone(),
            takes_body: t.takes_body,
            terminal: t.terminal,
            built_in: false,
        }))
        .collect()
    }
}

/// Validate one config-defined update type (see [`UpdateTypes::effective`]).
fn validate_type(t: &UpdateType) -> Result<()> {
    if BUILTIN_TYPE_NAMES.contains(&t.name.as_str()) {
        return Err(Error::BuiltinUpdateTypeRedefined(t.name.clone()));
    }
    if RESERVED_TYPE_NAMES.contains(&t.name.as_str()) {
        return Err(Error::ReservedUpdateTypeName(t.name.clone()));
    }
    let mut chars = t.name.chars();
    let starts_lower = chars.next().is_some_and(|c| c.is_ascii_lowercase());
    let rest_ok = chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-');
    if !starts_lower || !rest_ok {
        return Err(Error::InvalidUpdateTypeName(t.name.clone()));
    }
    Ok(())
}

/// Build the [`Document`] for a new update of the given kind.
///
/// `body` is the markdown content, ignored for kinds that take no body
/// ([`UpdateKind::Done`] and custom types with `takes-body = false`). Every
/// update is stamped with a fresh `update-id`; the [`UpdateKind::Note`] update
/// additionally records the thing's `task-id`, which must be supplied via
/// `task_id`.
pub fn build_update(kind: &UpdateKind, body: &str, task_id: Option<&str>) -> Document {
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

#[cfg(test)]
mod tests {
    use super::*;

    fn custom(name: &str, takes_body: bool, terminal: bool) -> UpdateType {
        UpdateType {
            name: name.to_string(),
            takes_body,
            terminal,
        }
    }

    #[test]
    fn timestamp_fields_follow_the_name_at_convention() {
        assert_eq!(UpdateKind::Note.timestamp_field(), "note-at");
        assert_eq!(UpdateKind::Done.timestamp_field(), "done-at");
        assert_eq!(
            UpdateKind::Custom(custom("blocked", true, false)).timestamp_field(),
            "blocked-at"
        );
        assert_eq!(timestamp_field_for("wont-do"), "wont-do-at");
    }

    #[test]
    fn custom_kind_carries_its_flags() {
        let kind = UpdateKind::Custom(custom("wont-do", false, true));
        assert_eq!(kind.status(), "wont-do");
        assert!(!kind.allows_body());
        assert!(kind.is_terminal());
        assert!(!kind.is_note());

        let kind = UpdateKind::Custom(custom("blocked", true, false));
        assert!(kind.allows_body());
        assert!(!kind.is_terminal());
    }

    #[test]
    fn only_done_is_terminal_among_builtins() {
        assert!(!UpdateKind::Note.is_terminal());
        assert!(!UpdateKind::Work.is_terminal());
        assert!(!UpdateKind::Info.is_terminal());
        assert!(UpdateKind::Done.is_terminal());
    }

    #[test]
    fn effective_merges_user_then_vault_with_vault_winning_by_name() {
        let user = [
            custom("blocked", true, false),
            custom("waiting", true, false),
        ];
        let vault = [
            custom("blocked", false, true),
            custom("wont-do", false, true),
        ];
        let types = UpdateTypes::effective(&user, &vault).unwrap();

        // The vault redefinition replaced `blocked` in place (order kept);
        // user-only `waiting` survives; vault-only `wont-do` extends the list.
        assert_eq!(
            types.custom(),
            &[
                custom("blocked", false, true),
                custom("waiting", true, false),
                custom("wont-do", false, true),
            ]
        );
    }

    #[test]
    fn effective_rejects_builtin_redefinitions() {
        for name in BUILTIN_TYPE_NAMES {
            let err = UpdateTypes::effective(&[custom(name, true, false)], &[]).unwrap_err();
            assert!(
                matches!(&err, Error::BuiltinUpdateTypeRedefined(n) if n == name),
                "expected builtin error for {name}, got {err:?}"
            );
        }
        // The vault level is validated the same way.
        assert!(matches!(
            UpdateTypes::effective(&[], &[custom("done", true, false)]),
            Err(Error::BuiltinUpdateTypeRedefined(_))
        ));
    }

    #[test]
    fn effective_rejects_reserved_and_invalid_names() {
        // `path` collides with `lot update path`.
        assert!(matches!(
            UpdateTypes::effective(&[custom("path", true, false)], &[]),
            Err(Error::ReservedUpdateTypeName(_))
        ));
        for bad in ["", "Blocked", "1st", "-x", "has space", "emoji✨"] {
            assert!(
                matches!(
                    UpdateTypes::effective(&[custom(bad, true, false)], &[]),
                    Err(Error::InvalidUpdateTypeName(_))
                ),
                "expected invalid-name error for {bad:?}"
            );
        }
        // Digits and hyphens after the leading letter are fine.
        assert!(UpdateTypes::effective(&[custom("wont-do-2", true, false)], &[]).is_ok());
    }

    #[test]
    fn resolve_finds_builtins_and_customs_and_errors_on_unknown() {
        let types = UpdateTypes::effective(&[custom("blocked", true, false)], &[]).unwrap();
        assert_eq!(types.resolve("work").unwrap(), UpdateKind::Work);
        assert_eq!(types.resolve("done").unwrap(), UpdateKind::Done);
        assert_eq!(
            types.resolve("blocked").unwrap(),
            UpdateKind::Custom(custom("blocked", true, false))
        );

        // `note` is not creatable via `lot update`, and unknown names list the
        // known creatable types.
        let err = types.resolve("note").unwrap_err();
        assert!(matches!(err, Error::UnknownUpdateType(_, _)));
        let err = types.resolve("bogus").unwrap_err().to_string();
        assert!(err.contains("bogus"));
        assert!(err.contains("work, info, done, blocked"));
    }

    #[test]
    fn infos_lists_builtins_then_customs_with_flags() {
        let types = UpdateTypes::effective(&[custom("wont-do", false, true)], &[]).unwrap();
        let infos = types.infos();
        let names: Vec<&str> = infos.iter().map(|i| i.name.as_str()).collect();
        assert_eq!(names, ["note", "work", "info", "done", "wont-do"]);

        let done = &infos[3];
        assert!(done.built_in && done.terminal && !done.takes_body);
        let note = &infos[0];
        assert!(note.built_in && !note.terminal && note.takes_body);
        let wont_do = &infos[4];
        assert!(!wont_do.built_in && wont_do.terminal && !wont_do.takes_body);
    }

    #[test]
    fn update_type_toml_defaults() {
        // `takes-body` defaults to true, `terminal` to false.
        let t: UpdateType = toml::from_str("name = \"blocked\"").unwrap();
        assert_eq!(t, custom("blocked", true, false));
        let t: UpdateType =
            toml::from_str("name = \"wont-do\"\ntakes-body = false\nterminal = true").unwrap();
        assert_eq!(t, custom("wont-do", false, true));
    }

    #[test]
    fn build_update_with_custom_type_stamps_status_and_timestamp() {
        let kind = UpdateKind::Custom(custom("blocked", true, false));
        let doc = build_update(&kind, "waiting on parts", None);
        assert_eq!(
            doc.frontmatter.get("status").and_then(|v| v.as_str()),
            Some("blocked")
        );
        assert!(doc.frontmatter.get("blocked-at").is_some());
        assert_eq!(doc.body, "waiting on parts");

        // A no-body custom type blanks the body like `done`.
        let kind = UpdateKind::Custom(custom("wont-do", false, true));
        let doc = build_update(&kind, "ignored", None);
        assert_eq!(doc.body, "");
        assert!(doc.frontmatter.get("wont-do-at").is_some());
    }
}
