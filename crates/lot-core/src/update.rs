use crate::error::{Error, Result};
use crate::frontmatter::Document;
use chrono::Utc;
use serde::{Deserialize, Serialize};
use serde_yaml_ng::Mapping;

/// Names that a config-defined update type may not use even though they are
/// not update types themselves: they collide with `lot update` sub-commands.
const RESERVED_TYPE_NAMES: [&str; 1] = ["path"];

/// The name of the update type `lot thing new` writes when config does not set
/// `thing.default-update-type`.
pub const DEFAULT_INITIAL_TYPE_NAME: &str = "note";

/// An update type: its name plus the flags that govern how it behaves. This is
/// the TOML shape under `[[update-types]]`, the entry shape of the
/// `update-types` list emitted by `lot settings get`, and the kind passed
/// around when building updates.
///
/// `lot` has no built-in types: every type comes from config. The stock set —
/// `note` → `work` → `info` → `done` — exists only as the defaults seeded when
/// a vault's config is created (see [`default_update_types`]).
#[derive(Debug, Clone, PartialEq, Eq, Deserialize, Serialize)]
pub struct UpdateType {
    /// The type's name: the `lot update <name>` sub-command, the `status`
    /// written into its updates' frontmatter, and the stem of its `<name>-at`
    /// timestamp field.
    pub name: String,

    /// Whether updates of this type accept a body. Defaults to `true`; set it
    /// to `false` for a bare marker (like the stock `done`).
    #[serde(default = "default_takes_body", rename = "takes-body")]
    pub takes_body: bool,

    /// Whether this type counts as a terminal state — one that retires the
    /// Thing for status display and front-ends' "bulk archive things in
    /// terminal states". Defaults to `false`.
    #[serde(default)]
    pub terminal: bool,
}

impl UpdateType {
    /// The frontmatter key that records this type's update timestamps, e.g.
    /// `work-at` or `done-at` — always `<name>-at`.
    pub fn timestamp_field(&self) -> String {
        timestamp_field_for(&self.name)
    }
}

/// The default for an update type's `takes-body` flag: bodies are accepted.
fn default_takes_body() -> bool {
    true
}

/// The stock update types: the lifecycle `note` → `work` → `info` → `done`.
///
/// These are not built into `lot`'s behaviour anywhere — they are only the
/// defaults: the set seeded into a new vault's config file, and the set that
/// applies when no config level defines any `[[update-types]]` at all (so a
/// vault predating explicit type config keeps working).
pub fn default_update_types() -> Vec<UpdateType> {
    let plain = |name: &str| UpdateType {
        name: name.to_string(),
        takes_body: true,
        terminal: false,
    };
    vec![
        plain("note"),
        plain("work"),
        plain("info"),
        UpdateType {
            name: "done".to_string(),
            takes_body: false,
            terminal: true,
        },
    ]
}

/// The full effective set of update types, resolved from config (user-level
/// `[[update-types]]` overlaid by vault-level ones), falling back to
/// [`default_update_types`] when neither level defines any.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct UpdateTypes {
    types: Vec<UpdateType>,
}

impl Default for UpdateTypes {
    /// The effective set when nothing is configured: the stock defaults.
    fn default() -> Self {
        UpdateTypes {
            types: default_update_types(),
        }
    }
}

impl UpdateTypes {
    /// Merge the user-level and vault-level `[[update-types]]` lists into the
    /// effective set, validating every definition.
    ///
    /// Definitions are merged by name: the user's list is taken first, then
    /// the vault's list extends it, with a vault definition **replacing** a
    /// same-named user definition in place (mirroring how vault-level config
    /// wins field-by-field elsewhere). A repeated name within one list is
    /// likewise replaced by the later entry. When the merged list is empty —
    /// no level defines any types — the stock defaults apply
    /// (see [`default_update_types`]).
    ///
    /// Validation is a hard error, so misconfiguration is not silently
    /// ignored: a name must start with a lowercase ASCII letter and contain
    /// only lowercase letters, digits, and hyphens; and `path` is reserved
    /// (it collides with the `lot update path` command).
    pub fn effective(user: &[UpdateType], vault: &[UpdateType]) -> Result<UpdateTypes> {
        let mut types: Vec<UpdateType> = Vec::new();
        for t in user.iter().chain(vault) {
            validate_type(t)?;
            match types.iter_mut().find(|c| c.name == t.name) {
                Some(existing) => *existing = t.clone(),
                None => types.push(t.clone()),
            }
        }
        if types.is_empty() {
            types = default_update_types();
        }
        Ok(UpdateTypes { types })
    }

    /// Every effective type, in configured order.
    pub fn all(&self) -> &[UpdateType] {
        &self.types
    }

    /// Resolve an update type by name for `lot update <name>`.
    ///
    /// Every configured type is creatable — including the vault's initial
    /// type (stock `note`). An unknown name is an error whose message lists
    /// the known types.
    pub fn resolve(&self, name: &str) -> Result<UpdateType> {
        if let Some(t) = self.types.iter().find(|t| t.name == name) {
            return Ok(t.clone());
        }
        Err(Error::UnknownUpdateType(
            name.to_string(),
            self.known_names(),
        ))
    }

    /// The type `lot thing new` writes as a Thing's first update: the
    /// `thing.default-update-type` config value (`configured`), falling back
    /// to [`DEFAULT_INITIAL_TYPE_NAME`] when unset. Naming a type the
    /// effective set does not define is a hard error, so a misconfigured
    /// default is not silently substituted.
    pub fn default_type(&self, configured: Option<&str>) -> Result<UpdateType> {
        let name = configured.unwrap_or(DEFAULT_INITIAL_TYPE_NAME);
        if let Some(t) = self.types.iter().find(|t| t.name == name) {
            return Ok(t.clone());
        }
        Err(Error::UnknownDefaultUpdateType(
            name.to_string(),
            self.known_names(),
        ))
    }

    /// The effective type names, comma-joined for error messages.
    fn known_names(&self) -> String {
        self.types
            .iter()
            .map(|t| t.name.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    }

    /// Whether `status` names a terminal state: a type declared with
    /// `terminal = true`. A status this set doesn't know (e.g. a type since
    /// removed from config) is not terminal — erring towards keeping things
    /// is the safe default for bulk archiving.
    pub fn status_is_terminal(&self, status: &str) -> bool {
        self.types.iter().any(|t| t.name == status && t.terminal)
    }
}

/// The frontmatter key that records the timestamp of an update whose `status`
/// is `status`: always `<status>-at` (`note-at`, `work-at`, …).
pub fn timestamp_field_for(status: &str) -> String {
    format!("{status}-at")
}

/// Validate one config-defined update type (see [`UpdateTypes::effective`]).
fn validate_type(t: &UpdateType) -> Result<()> {
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

/// Build the [`Document`] for a new update of the given type.
///
/// `body` is the markdown content, ignored for types that take no body
/// (`takes-body = false`). Every update is stamped with a fresh `update-id`;
/// a Thing's first update additionally records the thing's `task-id`, which
/// its creator supplies via `task_id` (pass `None` for ordinary updates).
pub fn build_update(kind: &UpdateType, body: &str, task_id: Option<&str>) -> Document {
    let mut fm = Mapping::new();
    fm.insert("status".into(), kind.name.as_str().into());
    if let Some(task_id) = task_id {
        fm.insert("task-id".into(), task_id.into());
    }
    fm.insert("update-id".into(), crate::id::new().into());
    fm.insert(
        kind.timestamp_field().into(),
        Utc::now().to_rfc3339().into(),
    );

    let body = if kind.takes_body {
        body.to_string()
    } else {
        String::new()
    };
    Document::new(fm, body)
}

/// Test helpers: the stock types by name, for tests across the crate that
/// need a concrete [`UpdateType`] to create things and updates with.
#[cfg(test)]
pub(crate) mod test_types {
    use super::*;

    /// The stock type with the given name (panics on an unknown name).
    fn stock(name: &str) -> UpdateType {
        default_update_types()
            .into_iter()
            .find(|t| t.name == name)
            .expect("a stock update type")
    }

    pub fn note() -> UpdateType {
        stock("note")
    }

    pub fn work() -> UpdateType {
        stock("work")
    }

    pub fn info() -> UpdateType {
        stock("info")
    }

    pub fn done() -> UpdateType {
        stock("done")
    }
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
        assert_eq!(custom("note", true, false).timestamp_field(), "note-at");
        assert_eq!(custom("done", false, true).timestamp_field(), "done-at");
        assert_eq!(
            custom("blocked", true, false).timestamp_field(),
            "blocked-at"
        );
        assert_eq!(timestamp_field_for("wont-do"), "wont-do-at");
    }

    #[test]
    fn default_types_are_the_stock_lifecycle() {
        let types = default_update_types();
        let names: Vec<&str> = types.iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["note", "work", "info", "done"]);
        // Only `done` is a bodyless terminal marker.
        assert!(types[..3].iter().all(|t| t.takes_body && !t.terminal));
        let done = &types[3];
        assert!(!done.takes_body && done.terminal);
    }

    #[test]
    fn empty_config_falls_back_to_the_defaults() {
        // No `[[update-types]]` at either level: the stock set applies, so
        // vaults that predate explicit type config keep working.
        let types = UpdateTypes::effective(&[], &[]).unwrap();
        assert_eq!(types.all(), default_update_types().as_slice());
        assert_eq!(types, UpdateTypes::default());
    }

    #[test]
    fn configured_types_fully_replace_the_defaults() {
        // Any configured list *is* the effective set: the stock types are not
        // merged in behind it.
        let types = UpdateTypes::effective(&[custom("todo", true, false)], &[]).unwrap();
        let names: Vec<&str> = types.all().iter().map(|t| t.name.as_str()).collect();
        assert_eq!(names, ["todo"]);
        assert!(types.resolve("work").is_err());
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
            types.all(),
            &[
                custom("blocked", false, true),
                custom("waiting", true, false),
                custom("wont-do", false, true),
            ]
        );
    }

    #[test]
    fn stock_names_can_be_redefined() {
        // With no built-ins there is nothing to protect: a config may define
        // (or redefine) `done`, `note`, etc. however it likes.
        let types = UpdateTypes::effective(&[custom("done", true, false)], &[]).unwrap();
        assert_eq!(types.resolve("done").unwrap(), custom("done", true, false));
        assert!(!types.status_is_terminal("done"));
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
    fn resolve_finds_every_configured_type_and_errors_on_unknown() {
        let types = UpdateTypes::effective(
            &[custom("note", true, false), custom("blocked", true, false)],
            &[],
        )
        .unwrap();
        // Every configured type is creatable — `note` included.
        assert_eq!(types.resolve("note").unwrap(), custom("note", true, false));
        assert_eq!(
            types.resolve("blocked").unwrap(),
            custom("blocked", true, false)
        );

        // Unknown names list the known types.
        let err = types.resolve("bogus").unwrap_err().to_string();
        assert!(err.contains("bogus"));
        assert!(err.contains("note, blocked"));
    }

    #[test]
    fn resolve_covers_the_default_set_when_unconfigured() {
        let types = UpdateTypes::default();
        assert_eq!(types.resolve("work").unwrap().name, "work");
        assert_eq!(types.resolve("note").unwrap().name, "note");
        let err = types.resolve("bogus").unwrap_err().to_string();
        assert!(err.contains("note, work, info, done"));
    }

    #[test]
    fn default_type_falls_back_to_note_and_validates_configured_names() {
        let types = UpdateTypes::default();
        // Unset: the stock initial type.
        assert_eq!(types.default_type(None).unwrap().name, "note");
        // Set to a known type: that type.
        assert_eq!(types.default_type(Some("work")).unwrap().name, "work");
        // Set to an unknown type: a hard error naming the config key's value.
        let err = types.default_type(Some("bogus")).unwrap_err();
        assert!(matches!(err, Error::UnknownDefaultUpdateType(_, _)));
        assert!(err.to_string().contains("default-update-type"));

        // A custom set without `note` errors when nothing is configured, so a
        // vault that renames its types must also configure its default.
        let types = UpdateTypes::effective(&[custom("todo", true, false)], &[]).unwrap();
        assert!(types.default_type(None).is_err());
        assert_eq!(types.default_type(Some("todo")).unwrap().name, "todo");
    }

    #[test]
    fn terminal_statuses_follow_the_configured_flags() {
        let types = UpdateTypes::default();
        assert!(types.status_is_terminal("done"));
        assert!(!types.status_is_terminal("note"));
        assert!(!types.status_is_terminal("work"));
        // Unknown statuses are not terminal (safe for bulk archiving).
        assert!(!types.status_is_terminal("bogus"));

        let types = UpdateTypes::effective(&[custom("wont-do", false, true)], &[]).unwrap();
        assert!(types.status_is_terminal("wont-do"));
        // The configured list replaced the defaults: `done` is now unknown.
        assert!(!types.status_is_terminal("done"));
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
    fn build_update_stamps_status_and_timestamp() {
        let doc = build_update(&custom("blocked", true, false), "waiting on parts", None);
        assert_eq!(
            doc.frontmatter.get("status").and_then(|v| v.as_str()),
            Some("blocked")
        );
        assert!(doc.frontmatter.get("blocked-at").is_some());
        assert_eq!(doc.body, "waiting on parts");

        // A no-body type blanks the body.
        let doc = build_update(&custom("wont-do", false, true), "ignored", None);
        assert_eq!(doc.body, "");
        assert!(doc.frontmatter.get("wont-do-at").is_some());
    }

    #[test]
    fn build_update_records_task_id_only_when_supplied() {
        // A Thing's first update carries the `task-id` its creator passes.
        let doc = build_update(&custom("note", true, false), "hello", Some("lot:abc"));
        assert_eq!(
            doc.frontmatter.get("task-id").and_then(|v| v.as_str()),
            Some("lot:abc")
        );
        // Ordinary updates never record one.
        let doc = build_update(&custom("work", true, false), "hello", None);
        assert!(doc.frontmatter.get("task-id").is_none());
    }
}
