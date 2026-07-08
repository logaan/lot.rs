use crate::error::{io_err, Error, Result};
use crate::update::{UpdateType, UpdateTypeInfo, UpdateTypes};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The example config that is written out on first run when no config exists.
pub const EXAMPLE_CONFIG: &str = include_str!("../../../data/config.example.toml");

/// Name of the project-local config file. When present in the current
/// directory it overrides the user config, letting a project point `lot` at its
/// own vault.
pub const PROJECT_CONFIG_FILENAME: &str = ".lot.toml";

/// The vault-level config file, relative to the vault directory.
///
/// This is deliberately distinct from [`PROJECT_CONFIG_FILENAME`] (`.lot.toml`
/// in the *current working directory*, which points `lot` at a vault): this
/// file lives *inside* the vault and only carries the overrides that win over
/// the user-level config — the front-end `[tui]` table and `[[update-types]]`
/// definitions. Keeping it under a `.lot/` sub-directory avoids ever conflating
/// the two roles.
pub const VAULT_CONFIG_RELATIVE_PATH: &str = ".lot/config.toml";

/// LoT configuration, read from `config.toml`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub vault: VaultConfig,

    /// Front-end (TUI) settings: theme, keybinding overrides, and the list of
    /// known vaults. Optional so existing configs without a `[tui]` table still
    /// parse; absent means an all-defaults [`TuiConfig`].
    #[serde(default)]
    pub tui: TuiConfig,

    /// Custom update types defined as `[[update-types]]` tables. Optional;
    /// absent means "no custom types". The vault-level config may define the
    /// same key to override/extend these per vault (see
    /// [`UpdateTypes::effective`]).
    #[serde(default, rename = "update-types")]
    pub update_types: Vec<UpdateType>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct VaultConfig {
    /// The path to the vault. May contain a leading `~` which is expanded
    /// against the user's home directory.
    pub path: String,
    /// Whether `lot` commits its changes to the vault's git repo (and
    /// initialises the repo on first run). Defaults to `true`. A project's
    /// `.lot.toml` can set it to `false` so vault changes can be batched into
    /// the project's own commits.
    #[serde(default = "default_auto_commit", rename = "auto-commit")]
    pub auto_commit: bool,
}

/// The default for `vault.auto-commit`: commit automatically.
fn default_auto_commit() -> bool {
    true
}

/// User-level front-end configuration under the `[tui]` table.
///
/// Every field is optional with a sensible default so a config without a
/// `[tui]` table (or with only some of these keys) still parses. The
/// vault-level `[tui]` table is the narrower [`VaultTuiConfig`] — it can
/// override `theme` and `keybindings` but *not* `vaults`, which is a per-user,
/// per-machine registry (see [`VaultTuiConfig`] and [`TuiConfig::overlaid_with`]).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct TuiConfig {
    /// The name of the colour scheme / theme to use. `None` (no `theme` key)
    /// leaves the choice to the front-end's own default.
    #[serde(default)]
    pub theme: Option<String>,

    /// Keybinding overrides as a map of action name -> key. Absent or empty
    /// means "no overrides"; the front-end supplies its own defaults for any
    /// action not listed here. A [`BTreeMap`] so the serialised order is stable.
    #[serde(default)]
    pub keybindings: BTreeMap<String, String>,

    /// The known vaults the front-end can switch between. Each entry has a
    /// `path` and an optional human-readable `name`. This is a user-level-only
    /// setting: a vault (a synced git repo) must not carry a list of *other*
    /// vaults' machine-specific paths, so [`VaultTuiConfig`] has no such field.
    #[serde(default)]
    pub vaults: Vec<VaultEntry>,
}

/// The vault-level `[tui]` table (in `<vault>/.lot/config.toml`).
///
/// A narrower shape than the user-level [`TuiConfig`]: it may override `theme`
/// and `keybindings` but deliberately has **no `vaults` field**. The vault
/// list is a per-user, per-machine registry of where one's vaults live — a
/// vault is a git repo that may be cloned or shared across machines, so its
/// config carrying other vaults' paths would be meaningless elsewhere and, via
/// vault-switching, could hide the user's real vault list. `deny_unknown_fields`
/// turns a stray `[[tui.vaults]]` here into a hard config-parse error rather
/// than a silently-ignored setting, matching the rest of the config's "never
/// silently ignore misconfiguration" contract.
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
pub struct VaultTuiConfig {
    /// See [`TuiConfig::theme`].
    #[serde(default)]
    pub theme: Option<String>,

    /// See [`TuiConfig::keybindings`].
    #[serde(default)]
    pub keybindings: BTreeMap<String, String>,
}

impl TuiConfig {
    /// Merge `self` (user-level) with an `other` (vault-level) `[tui]` table,
    /// with **vault values winning** for the fields a vault may override,
    /// returning the effective settings:
    ///
    /// * `theme`: the vault's theme when it sets one, otherwise the user's.
    /// * `keybindings`: the union of both maps, where a binding present in the
    ///   vault config overrides the same-named user binding; user-only bindings
    ///   survive.
    /// * `vaults`: always the user's list — a vault cannot override it (see
    ///   [`VaultTuiConfig`]).
    #[must_use]
    pub fn overlaid_with(&self, other: &VaultTuiConfig) -> TuiConfig {
        let theme = other.theme.clone().or_else(|| self.theme.clone());

        let mut keybindings = self.keybindings.clone();
        keybindings.extend(other.keybindings.clone());

        TuiConfig {
            theme,
            keybindings,
            vaults: self.vaults.clone(),
        }
    }
}

/// A single vault the front-end knows about: where it lives and, optionally, a
/// display name.
#[derive(Debug, Clone, Deserialize, Serialize, PartialEq, Eq)]
pub struct VaultEntry {
    /// A human-readable name for the vault. Omitted from the serialised output
    /// when absent.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,

    /// The path to the vault. May contain a leading `~`.
    pub path: String,
}

/// The vault-level config file (`<vault>/.lot/config.toml`). Only the `[tui]`
/// table (a [`VaultTuiConfig`] — `theme`/`keybindings`, but not `vaults`) and
/// `[[update-types]]` definitions are meaningful; an absent file is treated as
/// an all-defaults value.
#[derive(Debug, Clone, Default, Deserialize, Serialize)]
pub struct VaultLevelConfig {
    #[serde(default)]
    pub tui: VaultTuiConfig,

    /// Vault-level custom update types, overriding/extending the user-level
    /// ones by name (see [`UpdateTypes::effective`]).
    #[serde(default, rename = "update-types")]
    pub update_types: Vec<UpdateType>,
}

impl VaultLevelConfig {
    /// Read the vault-level config from `<vault>/.lot/config.toml`. A missing
    /// file yields an all-defaults value (no overrides); a present-but-malformed
    /// file is a hard error so misconfiguration is not silently ignored.
    pub fn load_for_vault(vault: &Path) -> Result<VaultLevelConfig> {
        let path = vault.join(VAULT_CONFIG_RELATIVE_PATH);
        if !path.exists() {
            return Ok(VaultLevelConfig::default());
        }
        let raw = std::fs::read_to_string(&path).map_err(io_err(&path))?;
        toml::from_str(&raw).map_err(|source| Error::ConfigParse { path, source })
    }
}

/// The merged, effective front-end configuration emitted by `lot settings get`.
///
/// This is the documented, stable shape front-ends parse. Its keys are always
/// present (even when empty/null) so consumers can rely on them:
///
/// * `theme` — the effective theme, or `null` when none is configured.
/// * `keybindings` — the merged action -> key map (`{}` when empty).
/// * `vaults` — the effective list of `{name?, path}` entries (`[]` when empty).
/// * `vault-path` — the resolved path of the currently active vault.
/// * `update-types` — the full effective set of update types (built-ins plus
///   config-defined custom types), each entry carrying `name`, `takes-body`,
///   `terminal`, and `built-in`. This is how front-ends discover custom types.
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EffectiveConfig {
    pub theme: Option<String>,
    pub keybindings: BTreeMap<String, String>,
    pub vaults: Vec<VaultEntry>,
    #[serde(rename = "vault-path")]
    pub vault_path: String,
    #[serde(rename = "update-types")]
    pub update_types: Vec<UpdateTypeInfo>,
}

impl EffectiveConfig {
    /// Build the effective config from a merged [`TuiConfig`], the effective
    /// update types, and the active vault path.
    fn from_merged(tui: TuiConfig, types: &UpdateTypes, vault_path: &Path) -> EffectiveConfig {
        EffectiveConfig {
            theme: tui.theme,
            keybindings: tui.keybindings,
            vaults: tui.vaults,
            vault_path: vault_path.display().to_string(),
            update_types: types.infos(),
        }
    }

    /// Serialise to the documented YAML shape.
    pub fn to_yaml(&self) -> Result<String> {
        Ok(serde_yaml_ng::to_string(self)?)
    }
}

/// The two config layers a merge draws from: the user-level config (absent when
/// `LOT_VAULT_PATH` short-circuits config entirely), the resolved vault path,
/// and the vault-level config (all-defaults when its file is absent).
fn load_config_layers() -> Result<(Option<Config>, PathBuf, VaultLevelConfig)> {
    let user = match env_vault_path() {
        // `LOT_VAULT_PATH` short-circuits the user config entirely, matching
        // `resolve_vault_settings`.
        Some(_) => None,
        None => Some(Config::load_or_init()?),
    };
    let vault_path = resolve_vault_path()?;
    let vault = VaultLevelConfig::load_for_vault(&vault_path)?;
    Ok((user, vault_path, vault))
}

/// Resolve the effective front-end config: the active vault path plus the
/// user-level `[tui]` table overlaid by the vault-level `[tui]` table
/// (vault wins), and the effective update types (user-level `[[update-types]]`
/// extended/overridden by vault-level ones). See [`TuiConfig::overlaid_with`]
/// and [`UpdateTypes::effective`] for the per-field rules.
///
/// The active vault path honours `LOT_VAULT_PATH` exactly like
/// [`resolve_vault_path`]. The user-level settings are read from the same
/// config file that supplies the vault path (the user config, or a
/// project-local `.lot.toml`); when `LOT_VAULT_PATH` short-circuits config
/// there is no user file to read, so only vault-level overrides apply.
pub fn load_effective_config() -> Result<EffectiveConfig> {
    let (user, vault_path, vault) = load_config_layers()?;
    let (user_tui, user_types) = match user {
        Some(config) => (config.tui, config.update_types),
        None => (TuiConfig::default(), Vec::new()),
    };
    let merged = user_tui.overlaid_with(&vault.tui);
    let types = UpdateTypes::effective(&user_types, &vault.update_types)?;
    Ok(EffectiveConfig::from_merged(merged, &types, &vault_path))
}

/// Resolve the effective set of update types (built-ins plus the custom types
/// from the user- and vault-level configs). Sourcing rules match
/// [`load_effective_config`]: when `LOT_VAULT_PATH` short-circuits config, only
/// vault-level definitions apply.
pub fn load_update_types() -> Result<UpdateTypes> {
    let (user, _vault_path, vault) = load_config_layers()?;
    let user_types = user.map(|c| c.update_types).unwrap_or_default();
    UpdateTypes::effective(&user_types, &vault.update_types)
}

/// Persist the front-end `theme` into the user-level config file, returning the
/// path written.
///
/// The key lands in the same config file `lot` reads for user-level settings —
/// a project-local `.lot.toml` in the current directory when one exists,
/// otherwise `~/.config/lot/config.toml` (see [`Config::load_or_init`]) —
/// created from the bundled example first when it does not yet exist. Unlike
/// vault resolution this deliberately ignores `LOT_VAULT_PATH`: the theme is a
/// user preference, not a per-invocation vault override, so a front-end
/// launched with `LOT_VAULT_PATH` set (every `lot interface` session) still
/// writes to the user config rather than nowhere.
///
/// The edit is format-preserving: only `[tui].theme` is inserted or updated;
/// the rest of the file — its other keys, comments, and layout — is left as it
/// was (see [`Config::set_theme_at`]).
pub fn set_user_theme(theme: &str) -> Result<PathBuf> {
    let cwd = std::env::current_dir()?;
    let path = Config::resolve_path(&cwd, Config::default_path()?);
    Config::set_theme_at(&path, theme)?;
    Ok(path)
}

impl Config {
    /// The default config file path: `$XDG_CONFIG_HOME/lot/config.toml`,
    /// falling back to the platform config directory.
    ///
    /// The readme writes this as `~/config/lot/config.toml`; we treat that as
    /// the XDG config location (`~/.config/lot/config.toml` on most systems).
    pub fn default_path() -> Result<PathBuf> {
        let base = if let Some(xdg) = std::env::var_os("XDG_CONFIG_HOME") {
            PathBuf::from(xdg)
        } else {
            dirs_config_dir().ok_or(Error::NoConfigDir)?
        };
        Ok(base.join("lot").join("config.toml"))
    }

    /// Load the config, creating the user config from the bundled example on
    /// first run.
    ///
    /// A project-local `.lot.toml` in the current working directory takes
    /// precedence over the user config (`~/.config/lot/config.toml`), so a
    /// project can point `lot` at its own vault. The project file is never
    /// auto-created; only the user config is.
    pub fn load_or_init() -> Result<Config> {
        let cwd = std::env::current_dir()?;
        let path = Self::resolve_path(&cwd, Self::default_path()?);
        Self::load_or_init_at(&path)
    }

    /// Decide which config file to load: a project-local `.lot.toml` in `cwd`
    /// when one exists, otherwise the user `default` path.
    fn resolve_path(cwd: &Path, default: PathBuf) -> PathBuf {
        let project = cwd.join(PROJECT_CONFIG_FILENAME);
        if project.is_file() {
            project
        } else {
            default
        }
    }

    /// Load the config from `path`, creating it from the bundled example if it
    /// does not yet exist.
    pub fn load_or_init_at(path: &Path) -> Result<Config> {
        if !path.exists() {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(io_err(parent))?;
            }
            std::fs::write(path, EXAMPLE_CONFIG).map_err(io_err(path))?;
        }
        let raw = std::fs::read_to_string(path).map_err(io_err(path))?;
        toml::from_str(&raw).map_err(|source| Error::ConfigParse {
            path: path.to_path_buf(),
            source,
        })
    }

    /// The vault path with `~` expanded.
    pub fn vault_path(&self) -> PathBuf {
        PathBuf::from(shellexpand::tilde(&self.vault.path).into_owned())
    }

    /// Write `[tui].theme = <theme>` into the config file at `path`, preserving
    /// everything else, and creating the file from the bundled example first
    /// when it does not yet exist (matching [`Config::load_or_init_at`]).
    ///
    /// The write goes through `toml_edit` so the rest of the document survives
    /// verbatim: other keys, comments, and whitespace are untouched, and an
    /// existing `[tui]` table keeps its other settings — only the `theme` key is
    /// inserted or replaced. A missing `[tui]` table is created.
    pub fn set_theme_at(path: &Path, theme: &str) -> Result<()> {
        if !path.exists() {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(io_err(parent))?;
            }
            std::fs::write(path, EXAMPLE_CONFIG).map_err(io_err(path))?;
        }
        let raw = std::fs::read_to_string(path).map_err(io_err(path))?;
        let mut doc =
            raw.parse::<toml_edit::DocumentMut>()
                .map_err(|source| Error::ConfigEdit {
                    path: path.to_path_buf(),
                    source: Box::new(source),
                })?;
        // Seed a proper `[tui]` header table when absent (rather than letting an
        // index auto-vivify an inline `tui = { … }` at the top of the file), so
        // the written config reads like the hand-authored example. An existing
        // `[tui]` — table or inline — is left in place and only its theme set.
        if !doc.contains_key("tui") {
            doc.insert("tui", toml_edit::Item::Table(toml_edit::Table::new()));
        }
        doc["tui"]["theme"] = toml_edit::value(theme);
        std::fs::write(path, doc.to_string()).map_err(io_err(path))?;
        Ok(())
    }
}

/// The resolved settings a front-end needs to open the vault: where it lives
/// and whether `lot` should commit its changes to the vault's git repo.
#[derive(Debug, Clone)]
pub struct VaultSettings {
    pub path: PathBuf,
    pub auto_commit: bool,
}

/// Resolve the vault path to open.
///
/// The `LOT_VAULT_PATH` environment variable wins over everything: when it is
/// set (and not blank) its value is used directly — with a leading `~`
/// expanded — and no config file is read or created. Otherwise the configured
/// vault path is loaded (creating the user config from the example on first
/// run), which itself honours a project-local `.lot.toml` over the user config.
pub fn resolve_vault_path() -> Result<PathBuf> {
    Ok(resolve_vault_settings()?.path)
}

/// Resolve the vault path together with the `vault.auto-commit` setting.
///
/// Path resolution is exactly [`resolve_vault_path`]. `auto_commit` honours
/// the `LOT_AUTO_COMMIT` environment variable first — set alongside
/// `LOT_VAULT_PATH` by `lot interface`, `lot web`, and `lot claude send` so
/// child `lot` invocations keep the launching config's behaviour. Without the
/// override, `auto_commit` comes from the same config file that supplied the
/// path; when `LOT_VAULT_PATH` short-circuits config entirely it keeps its
/// default of `true`.
pub fn resolve_vault_settings() -> Result<VaultSettings> {
    let auto_commit_override = env_auto_commit()?;
    match env_vault_path() {
        Some(path) => Ok(VaultSettings {
            path,
            auto_commit: auto_commit_override.unwrap_or(true),
        }),
        None => {
            let config = Config::load_or_init()?;
            Ok(VaultSettings {
                path: config.vault_path(),
                auto_commit: auto_commit_override.unwrap_or(config.vault.auto_commit),
            })
        }
    }
}

/// The auto-commit override from `LOT_AUTO_COMMIT`, if it is set and not
/// blank.
fn env_auto_commit() -> Result<Option<bool>> {
    match std::env::var_os(crate::env::AUTO_COMMIT) {
        Some(raw) => parse_auto_commit(&raw.to_string_lossy()),
        None => Ok(None),
    }
}

/// Parse a `LOT_AUTO_COMMIT` value: blank means "no override", otherwise it
/// must read `true` or `false` (case-insensitive, surrounding whitespace
/// ignored). Anything else is a hard error rather than a silently-ignored
/// setting.
fn parse_auto_commit(raw: &str) -> Result<Option<bool>> {
    let trimmed = raw.trim();
    match trimmed.to_ascii_lowercase().as_str() {
        "" => Ok(None),
        "true" => Ok(Some(true)),
        "false" => Ok(Some(false)),
        _ => Err(Error::InvalidAutoCommitEnv(trimmed.to_string())),
    }
}

/// The vault path from `LOT_VAULT_PATH`, if it is set and not blank. A leading
/// `~` is expanded against the user's home directory, matching `vault.path`.
fn env_vault_path() -> Option<PathBuf> {
    let raw = std::env::var_os(crate::env::VAULT_PATH)?;
    let raw = raw.to_string_lossy();
    let trimmed = raw.trim();
    (!trimmed.is_empty()).then(|| PathBuf::from(shellexpand::tilde(trimmed).into_owned()))
}

/// Resolve the platform config directory without pulling in the `dirs` crate.
fn dirs_config_dir() -> Option<PathBuf> {
    if let Some(home) = std::env::var_os("HOME") {
        let home = PathBuf::from(home);
        if cfg!(target_os = "macos") {
            // Prefer XDG-style on macOS too, matching common dotfile setups.
            return Some(home.join(".config"));
        }
        return Some(home.join(".config"));
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example_config_parses() {
        let cfg: Config = toml::from_str(EXAMPLE_CONFIG).unwrap();
        assert!(!cfg.vault.path.is_empty());
        // The example leaves auto-commit unset, so the default applies.
        assert!(cfg.vault.auto_commit);
    }

    #[test]
    fn auto_commit_defaults_to_true_and_can_be_disabled() {
        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\n").unwrap();
        assert!(cfg.vault.auto_commit);

        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\nauto-commit = false\n").unwrap();
        assert!(!cfg.vault.auto_commit);
    }

    #[test]
    fn creates_config_on_first_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lot").join("config.toml");
        let cfg = Config::load_or_init_at(&path).unwrap();
        assert!(path.exists());
        assert!(!cfg.vault.path.is_empty());
    }

    #[test]
    fn resolves_to_user_config_without_project_file() {
        let dir = tempfile::tempdir().unwrap();
        let default = PathBuf::from("/home/user/.config/lot/config.toml");
        let resolved = Config::resolve_path(dir.path(), default.clone());
        assert_eq!(resolved, default);
    }

    #[test]
    fn env_vault_path_overrides_config_and_expands_tilde() {
        // This test owns all `LOT_VAULT_PATH`/`LOT_AUTO_COMMIT` mutation:
        // tests run in parallel threads, so no other test may touch these
        // process-wide variables.

        // Blank/unset env -> no override (config is consulted instead).
        std::env::remove_var(crate::env::VAULT_PATH);
        std::env::remove_var(crate::env::AUTO_COMMIT);
        assert_eq!(env_vault_path(), None);
        std::env::set_var(crate::env::VAULT_PATH, "   ");
        assert_eq!(env_vault_path(), None);

        // A set value wins and has its leading `~` expanded.
        std::env::set_var(crate::env::VAULT_PATH, "~/my-vault");
        let resolved = env_vault_path().unwrap();
        assert!(resolved.is_absolute() || !resolved.starts_with("~"));
        assert!(resolved.ends_with("my-vault"));

        // With `LOT_VAULT_PATH` set and no `LOT_AUTO_COMMIT`, auto-commit
        // keeps its default of true.
        let settings = resolve_vault_settings().unwrap();
        assert!(settings.auto_commit);

        // `LOT_AUTO_COMMIT=false` disables it — the contract `lot interface`,
        // `lot web`, and `lot claude send` rely on to keep the launching
        // config's setting alive in child processes.
        std::env::set_var(crate::env::AUTO_COMMIT, "false");
        let settings = resolve_vault_settings().unwrap();
        assert!(!settings.auto_commit);

        std::env::remove_var(crate::env::AUTO_COMMIT);
        std::env::remove_var(crate::env::VAULT_PATH);
    }

    #[test]
    fn parse_auto_commit_accepts_bools_and_rejects_garbage() {
        assert_eq!(parse_auto_commit("").unwrap(), None);
        assert_eq!(parse_auto_commit("   ").unwrap(), None);
        assert_eq!(parse_auto_commit("true").unwrap(), Some(true));
        assert_eq!(parse_auto_commit("false").unwrap(), Some(false));
        assert_eq!(parse_auto_commit(" TRUE ").unwrap(), Some(true));
        assert_eq!(parse_auto_commit("False").unwrap(), Some(false));
        assert!(matches!(
            parse_auto_commit("yes"),
            Err(Error::InvalidAutoCommitEnv(v)) if v == "yes"
        ));
    }

    #[test]
    fn config_without_tui_parses_with_default_tui() {
        // Old configs (no `[tui]` table) must still parse, yielding all-default
        // TUI settings.
        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\n").unwrap();
        assert_eq!(cfg.tui, TuiConfig::default());
        assert!(cfg.tui.theme.is_none());
        assert!(cfg.tui.keybindings.is_empty());
        assert!(cfg.tui.vaults.is_empty());
    }

    #[test]
    fn user_tui_fields_round_trip() {
        let raw = r#"
[vault]
path = "~/v"

[tui]
theme = "solarized-dark"

[tui.keybindings]
quit = "q"
down = "j"

[[tui.vaults]]
name = "Personal"
path = "~/personal-vault"

[[tui.vaults]]
path = "~/work-vault"
"#;
        let cfg: Config = toml::from_str(raw).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("solarized-dark"));
        assert_eq!(
            cfg.tui.keybindings.get("quit").map(String::as_str),
            Some("q")
        );
        assert_eq!(
            cfg.tui.keybindings.get("down").map(String::as_str),
            Some("j")
        );
        assert_eq!(cfg.tui.vaults.len(), 2);
        assert_eq!(cfg.tui.vaults[0].name.as_deref(), Some("Personal"));
        assert_eq!(cfg.tui.vaults[0].path, "~/personal-vault");
        assert_eq!(cfg.tui.vaults[1].name, None);
        assert_eq!(cfg.tui.vaults[1].path, "~/work-vault");
    }

    #[test]
    fn vault_overrides_user_per_field() {
        let user = TuiConfig {
            theme: Some("light".into()),
            keybindings: BTreeMap::from([("quit".into(), "q".into()), ("down".into(), "j".into())]),
            vaults: vec![VaultEntry {
                name: Some("User".into()),
                path: "~/user-vault".into(),
            }],
        };
        let vault = VaultTuiConfig {
            theme: Some("dark".into()),
            keybindings: BTreeMap::from([("down".into(), "n".into())]),
        };

        let merged = user.overlaid_with(&vault);
        // theme: vault wins.
        assert_eq!(merged.theme.as_deref(), Some("dark"));
        // keybindings: union, vault key wins for `down`, user-only `quit` survives.
        assert_eq!(
            merged.keybindings.get("down").map(String::as_str),
            Some("n")
        );
        assert_eq!(
            merged.keybindings.get("quit").map(String::as_str),
            Some("q")
        );
        // vaults: always the user's list — a vault cannot override it.
        assert_eq!(merged.vaults.len(), 1);
        assert_eq!(merged.vaults[0].path, "~/user-vault");
    }

    #[test]
    fn empty_vault_tui_keeps_user_values() {
        let user = TuiConfig {
            theme: Some("light".into()),
            keybindings: BTreeMap::from([("quit".into(), "q".into())]),
            vaults: vec![VaultEntry {
                name: Some("User".into()),
                path: "~/user-vault".into(),
            }],
        };
        // An all-default vault-level `[tui]` (absent file) overrides nothing.
        let merged = user.overlaid_with(&VaultTuiConfig::default());
        assert_eq!(merged, user);
    }

    #[test]
    fn vault_level_tui_rejects_a_vaults_list() {
        // A `[[tui.vaults]]` in a vault-level config is misconfiguration —
        // the vault list is user-level only — and must be a hard parse error
        // rather than a silently-ignored setting.
        let raw = "[tui]\ntheme = \"dark\"\n\n[[tui.vaults]]\npath = \"~/sneaky\"\n";
        let err = toml::from_str::<VaultLevelConfig>(raw).unwrap_err();
        assert!(
            err.to_string().contains("vaults"),
            "error should name the offending key: {err}"
        );
    }

    #[test]
    fn vault_level_config_absent_is_default() {
        let dir = tempfile::tempdir().unwrap();
        let cfg = VaultLevelConfig::load_for_vault(dir.path()).unwrap();
        assert_eq!(cfg.tui, VaultTuiConfig::default());
    }

    #[test]
    fn vault_level_config_reads_tui_table() {
        let dir = tempfile::tempdir().unwrap();
        let cfg_path = dir.path().join(VAULT_CONFIG_RELATIVE_PATH);
        std::fs::create_dir_all(cfg_path.parent().unwrap()).unwrap();
        std::fs::write(&cfg_path, "[tui]\ntheme = \"dark\"\n").unwrap();

        let cfg = VaultLevelConfig::load_for_vault(dir.path()).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("dark"));
    }

    /// The `update-types` YAML emitted for the four built-ins alone (an empty
    /// custom set) — the tail every `settings get` document carries.
    const BUILTIN_UPDATE_TYPES_YAML: &str = "update-types:\n\
         - name: note\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         \x20 built-in: true\n\
         - name: work\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         \x20 built-in: true\n\
         - name: info\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         \x20 built-in: true\n\
         - name: done\n\
         \x20 takes-body: false\n\
         \x20 terminal: true\n\
         \x20 built-in: true\n";

    #[test]
    fn effective_config_serialises_documented_shape() {
        let tui = TuiConfig {
            theme: Some("dark".into()),
            keybindings: BTreeMap::from([("quit".into(), "q".into())]),
            vaults: vec![
                VaultEntry {
                    name: Some("Personal".into()),
                    path: "~/personal".into(),
                },
                VaultEntry {
                    name: None,
                    path: "~/work".into(),
                },
            ],
        };
        let eff = EffectiveConfig::from_merged(
            tui,
            &UpdateTypes::default(),
            Path::new("/home/you/personal"),
        );
        let yaml = eff.to_yaml().unwrap();
        assert_eq!(
            yaml,
            format!(
                "theme: dark\n\
                 keybindings:\n\
                 \x20 quit: q\n\
                 vaults:\n\
                 - name: Personal\n\
                 \x20 path: ~/personal\n\
                 - path: ~/work\n\
                 vault-path: /home/you/personal\n\
                 {BUILTIN_UPDATE_TYPES_YAML}"
            )
        );
    }

    #[test]
    fn effective_config_empty_fields_stay_present() {
        // theme -> null, keybindings -> {}, vaults -> [] must all still appear,
        // and `update-types` always carries at least the built-ins.
        let eff = EffectiveConfig::from_merged(
            TuiConfig::default(),
            &UpdateTypes::default(),
            Path::new("/v"),
        );
        let yaml = eff.to_yaml().unwrap();
        assert_eq!(
            yaml,
            format!(
                "theme: null\nkeybindings: {{}}\nvaults: []\nvault-path: /v\n\
                 {BUILTIN_UPDATE_TYPES_YAML}"
            )
        );
    }

    #[test]
    fn effective_config_lists_custom_update_types_after_builtins() {
        let types = UpdateTypes::effective(
            &[UpdateType {
                name: "wont-do".into(),
                takes_body: false,
                terminal: true,
            }],
            &[],
        )
        .unwrap();
        let eff = EffectiveConfig::from_merged(TuiConfig::default(), &types, Path::new("/v"));
        let yaml = eff.to_yaml().unwrap();
        assert!(yaml.ends_with(
            "- name: wont-do\n\
             \x20 takes-body: false\n\
             \x20 terminal: true\n\
             \x20 built-in: false\n"
        ));
    }

    #[test]
    fn config_parses_update_types_at_both_levels() {
        let raw = r#"
[vault]
path = "~/v"

[[update-types]]
name = "blocked"

[[update-types]]
name = "wont-do"
takes-body = false
terminal = true
"#;
        let cfg: Config = toml::from_str(raw).unwrap();
        assert_eq!(cfg.update_types.len(), 2);
        assert_eq!(cfg.update_types[0].name, "blocked");
        // Defaults: takes-body true, terminal false.
        assert!(cfg.update_types[0].takes_body);
        assert!(!cfg.update_types[0].terminal);
        assert_eq!(cfg.update_types[1].name, "wont-do");
        assert!(!cfg.update_types[1].takes_body);
        assert!(cfg.update_types[1].terminal);

        // A config without the key still parses (no custom types).
        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\n").unwrap();
        assert!(cfg.update_types.is_empty());

        // The vault-level file takes the same shape.
        let vault: VaultLevelConfig =
            toml::from_str("[[update-types]]\nname = \"blocked\"\nterminal = true\n").unwrap();
        assert_eq!(vault.update_types.len(), 1);
        assert!(vault.update_types[0].terminal);
    }

    #[test]
    fn vault_level_config_reads_update_types_from_disk() {
        let dir = tempfile::tempdir().unwrap();
        let cfg_path = dir.path().join(VAULT_CONFIG_RELATIVE_PATH);
        std::fs::create_dir_all(cfg_path.parent().unwrap()).unwrap();
        std::fs::write(
            &cfg_path,
            "[[update-types]]\nname = \"blocked\"\ntakes-body = true\n",
        )
        .unwrap();

        let cfg = VaultLevelConfig::load_for_vault(dir.path()).unwrap();
        assert_eq!(cfg.update_types.len(), 1);
        assert_eq!(cfg.update_types[0].name, "blocked");
    }

    #[test]
    fn set_theme_creates_config_then_writes_it() {
        // With no config yet, the file is seeded from the example and the theme
        // written; reloading reads it back.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lot").join("config.toml");
        Config::set_theme_at(&path, "ansi-dark").unwrap();
        assert!(path.exists());
        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
    }

    #[test]
    fn set_theme_preserves_existing_content_and_comments() {
        // An existing file with a comment and other keys keeps them; only the
        // theme is added under a fresh `[tui]` table.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "# my vault\n[vault]\npath = \"~/v\"\n").unwrap();
        Config::set_theme_at(&path, "ansi-dark").unwrap();

        let raw = std::fs::read_to_string(&path).unwrap();
        assert!(raw.contains("# my vault"), "comment survives: {raw}");
        assert!(raw.contains("path = \"~/v\""), "vault path survives: {raw}");

        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.vault.path, "~/v");
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
    }

    #[test]
    fn set_theme_updates_existing_tui_table_in_place() {
        // A pre-existing `[tui]` table keeps its other keys; only theme changes.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "[vault]\npath = \"~/v\"\n\n[tui]\ntheme = \"nord\"\n\n[tui.keybindings]\nquit = \"q\"\n",
        )
        .unwrap();
        Config::set_theme_at(&path, "ansi-dark").unwrap();

        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
        // The sibling keybinding under [tui] is untouched.
        assert_eq!(
            cfg.tui.keybindings.get("quit").map(String::as_str),
            Some("q")
        );
    }

    #[test]
    fn project_lot_toml_overrides_user_config() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join(PROJECT_CONFIG_FILENAME);
        std::fs::write(&project, "[vault]\npath = \"./project-vault\"\n").unwrap();

        let default = PathBuf::from("/home/user/.config/lot/config.toml");
        let resolved = Config::resolve_path(dir.path(), default);
        assert_eq!(resolved, project);

        // And it parses to the project vault path.
        let cfg = Config::load_or_init_at(&resolved).unwrap();
        assert_eq!(cfg.vault.path, "./project-vault");
    }
}
