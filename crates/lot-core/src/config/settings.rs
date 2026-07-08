//! Front-end settings types and their merge rules: the `[tui]`,
//! `[[update-types]]`, and `[thing]` tables at the user and vault level, and
//! the assembly of the merged, effective configuration.

use super::resolve::{env_vault_path, resolve_vault_path};
use crate::error::{io_err, Error, Result};
use crate::update::{UpdateType, UpdateTypes};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// The vault-level config file, relative to the vault directory.
///
/// This is deliberately distinct from [`super::PROJECT_CONFIG_FILENAME`]
/// (`.lot.toml` in the *current working directory*, which points `lot` at a
/// vault): this file lives *inside* the vault and only carries the overrides
/// that win over the user-level config — the front-end `[tui]` table and
/// `[[update-types]]` definitions. Keeping it under a `.lot/` sub-directory
/// avoids ever conflating the two roles.
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

    /// Update types defined as `[[update-types]]` tables. Optional; absent
    /// means the stock defaults apply. The vault-level config may define the
    /// same key to override/extend these per vault (see
    /// [`UpdateTypes::effective`]).
    #[serde(default, rename = "update-types")]
    pub update_types: Vec<UpdateType>,

    /// Thing-creation settings (`[thing]` table). Optional; the vault-level
    /// config may set the same table, winning field-by-field.
    #[serde(default)]
    pub thing: ThingConfig,
}

/// Settings governing Things themselves, under the `[thing]` table. Present at
/// both the user and vault level (vault wins field-by-field).
#[derive(Debug, Clone, Default, Deserialize, Serialize, PartialEq, Eq)]
pub struct ThingConfig {
    /// The name of the update type `lot thing new` writes as a Thing's first
    /// update. Absent means the stock initial type (`note`). The named type
    /// must exist in the effective update types; anything else is a hard
    /// error (see [`UpdateTypes::default_type`]).
    #[serde(default, rename = "default-update-type")]
    pub default_update_type: Option<String>,
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

    /// Vault-level update types, overriding/extending the user-level ones by
    /// name (see [`UpdateTypes::effective`]).
    #[serde(default, rename = "update-types")]
    pub update_types: Vec<UpdateType>,

    /// Vault-level thing-creation settings, winning over the user-level ones
    /// field-by-field.
    #[serde(default)]
    pub thing: ThingConfig,
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
/// * `update-types` — the full effective set of update types (config-defined,
///   or the stock defaults when config defines none), each entry carrying
///   `name`, `takes-body`, and `terminal`. This is how front-ends discover
///   the types.
/// * `default-update-type` — the name of the type `lot thing new` writes as a
///   Thing's first update (`thing.default-update-type`, `note` by default).
#[derive(Debug, Clone, Serialize, PartialEq, Eq)]
pub struct EffectiveConfig {
    pub theme: Option<String>,
    pub keybindings: BTreeMap<String, String>,
    pub vaults: Vec<VaultEntry>,
    #[serde(rename = "vault-path")]
    pub vault_path: String,
    #[serde(rename = "update-types")]
    pub update_types: Vec<UpdateType>,
    #[serde(rename = "default-update-type")]
    pub default_update_type: String,
}

impl EffectiveConfig {
    /// Build the effective config from a merged [`TuiConfig`], the effective
    /// update types (with the resolved default type), and the active vault
    /// path.
    fn from_merged(
        tui: TuiConfig,
        types: &UpdateTypes,
        default_type: &UpdateType,
        vault_path: &Path,
    ) -> EffectiveConfig {
        EffectiveConfig {
            theme: tui.theme,
            keybindings: tui.keybindings,
            vaults: tui.vaults,
            vault_path: vault_path.display().to_string(),
            update_types: types.all().to_vec(),
            default_update_type: default_type.name.clone(),
        }
    }

    /// Serialise to the documented YAML shape.
    pub fn to_yaml(&self) -> Result<String> {
        Ok(serde_yaml_ng::to_string(self)?)
    }
}

/// The two config layers a merge draws from: the user-level config (absent
/// only when its file does not exist under a `LOT_VAULT_PATH` override), the
/// resolved vault path, and the vault-level config (all-defaults when its file
/// is absent).
///
/// `LOT_VAULT_PATH` overrides only the vault *path* (see
/// [`super::resolve_vault_settings`]); the user-level settings — `[tui]` and
/// `[[update-types]]` — are still read from the user config file, so an
/// interface session launched with the variable set sees the same preferences
/// as a plain invocation. The one difference: with the override in force an
/// absent user config is left uncreated (the invocation stays read-only with
/// respect to config files) instead of being seeded from the example.
fn load_config_layers() -> Result<(Option<Config>, PathBuf, VaultLevelConfig)> {
    let user = match env_vault_path() {
        Some(_) => Config::load_if_exists()?,
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
/// [`resolve_vault_path`], but the override stops there: the user-level
/// settings are read from the resolved user config file (a project-local
/// `.lot.toml`, else the user config) whether or not the variable is set, so a
/// front-end launched with `LOT_VAULT_PATH` set (every `lot interface`
/// session) still gets the user's theme, keybindings, and vault list. See
/// [`load_config_layers`].
pub fn load_effective_config() -> Result<EffectiveConfig> {
    let (user, vault_path, vault) = load_config_layers()?;
    let (user_tui, user_types, user_thing) = match user {
        Some(config) => (config.tui, config.update_types, config.thing),
        None => (TuiConfig::default(), Vec::new(), ThingConfig::default()),
    };
    let merged = user_tui.overlaid_with(&vault.tui);
    let types = UpdateTypes::effective(&user_types, &vault.update_types)?;
    let default_name = effective_default_type_name(&user_thing, &vault.thing);
    let default_type = types.default_type(default_name.as_deref())?;
    Ok(EffectiveConfig::from_merged(
        merged,
        &types,
        &default_type,
        &vault_path,
    ))
}

/// The configured `thing.default-update-type`, with the vault level winning
/// over the user level. `None` when neither level sets it (the stock initial
/// type applies — see [`UpdateTypes::default_type`]).
fn effective_default_type_name(user: &ThingConfig, vault: &ThingConfig) -> Option<String> {
    vault
        .default_update_type
        .clone()
        .or_else(|| user.default_update_type.clone())
}

/// Resolve the effective set of update types (the user- and vault-level
/// `[[update-types]]` lists merged, or the stock defaults when neither level
/// defines any). Sourcing rules match [`load_effective_config`]: user-level
/// definitions apply even under a `LOT_VAULT_PATH` override (see
/// [`load_config_layers`]).
pub fn load_update_types() -> Result<UpdateTypes> {
    let (user, _vault_path, vault) = load_config_layers()?;
    let user_types = user.map(|c| c.update_types).unwrap_or_default();
    UpdateTypes::effective(&user_types, &vault.update_types)
}

/// Resolve the update type `lot thing new` writes as a Thing's first update:
/// the effective `thing.default-update-type` (vault level winning over user
/// level) resolved against the effective update types. Unset means the stock
/// initial type; a configured name the types don't define is a hard error.
pub fn load_default_update_type() -> Result<UpdateType> {
    let (user, _vault_path, vault) = load_config_layers()?;
    let (user_types, user_thing) = match user {
        Some(config) => (config.update_types, config.thing),
        None => (Vec::new(), ThingConfig::default()),
    };
    let types = UpdateTypes::effective(&user_types, &vault.update_types)?;
    let default_name = effective_default_type_name(&user_thing, &vault.thing);
    types.default_type(default_name.as_deref())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn auto_commit_defaults_to_true_and_can_be_disabled() {
        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\n").unwrap();
        assert!(cfg.vault.auto_commit);

        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\nauto-commit = false\n").unwrap();
        assert!(!cfg.vault.auto_commit);
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

    /// The `update-types` + `default-update-type` YAML emitted when nothing is
    /// configured (the stock defaults) — the tail every such `settings get`
    /// document carries.
    const DEFAULT_UPDATE_TYPES_YAML: &str = "update-types:\n\
         - name: note\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         - name: work\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         - name: info\n\
         \x20 takes-body: true\n\
         \x20 terminal: false\n\
         - name: done\n\
         \x20 takes-body: false\n\
         \x20 terminal: true\n\
         default-update-type: note\n";

    /// The stock initial type (`note`), as [`EffectiveConfig::from_merged`]
    /// expects it resolved.
    fn stock_default_type() -> UpdateType {
        UpdateTypes::default().default_type(None).unwrap()
    }

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
            &stock_default_type(),
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
                 {DEFAULT_UPDATE_TYPES_YAML}"
            )
        );
    }

    #[test]
    fn effective_config_empty_fields_stay_present() {
        // theme -> null, keybindings -> {}, vaults -> [] must all still appear,
        // and `update-types`/`default-update-type` always carry the effective
        // set (the stock defaults when nothing is configured).
        let eff = EffectiveConfig::from_merged(
            TuiConfig::default(),
            &UpdateTypes::default(),
            &stock_default_type(),
            Path::new("/v"),
        );
        let yaml = eff.to_yaml().unwrap();
        assert_eq!(
            yaml,
            format!(
                "theme: null\nkeybindings: {{}}\nvaults: []\nvault-path: /v\n\
                 {DEFAULT_UPDATE_TYPES_YAML}"
            )
        );
    }

    #[test]
    fn effective_config_lists_configured_update_types_and_default() {
        // A configured list is the effective set (the stock defaults are not
        // merged in), and the configured default type is emitted by name.
        let todo = UpdateType {
            name: "todo".into(),
            takes_body: true,
            terminal: false,
        };
        let wont_do = UpdateType {
            name: "wont-do".into(),
            takes_body: false,
            terminal: true,
        };
        let types = UpdateTypes::effective(&[todo], &[wont_do]).unwrap();
        let default_type = types.default_type(Some("todo")).unwrap();
        let eff = EffectiveConfig::from_merged(
            TuiConfig::default(),
            &types,
            &default_type,
            Path::new("/v"),
        );
        let yaml = eff.to_yaml().unwrap();
        assert!(yaml.ends_with(
            "update-types:\n\
             - name: todo\n\
             \x20 takes-body: true\n\
             \x20 terminal: false\n\
             - name: wont-do\n\
             \x20 takes-body: false\n\
             \x20 terminal: true\n\
             default-update-type: todo\n"
        ));
        assert!(!yaml.contains("built-in"));
    }

    #[test]
    fn thing_config_parses_at_both_levels_and_vault_wins() {
        // The `[thing]` table is optional at both levels.
        let cfg: Config = toml::from_str("[vault]\npath = \"~/v\"\n").unwrap();
        assert_eq!(cfg.thing.default_update_type, None);

        let cfg: Config =
            toml::from_str("[vault]\npath = \"~/v\"\n\n[thing]\ndefault-update-type = \"work\"\n")
                .unwrap();
        assert_eq!(cfg.thing.default_update_type.as_deref(), Some("work"));

        let vault: VaultLevelConfig =
            toml::from_str("[thing]\ndefault-update-type = \"todo\"\n").unwrap();
        assert_eq!(vault.thing.default_update_type.as_deref(), Some("todo"));

        // The vault-level value wins; either level alone applies; neither
        // means unset.
        assert_eq!(
            effective_default_type_name(&cfg.thing, &vault.thing).as_deref(),
            Some("todo")
        );
        assert_eq!(
            effective_default_type_name(&cfg.thing, &ThingConfig::default()).as_deref(),
            Some("work")
        );
        assert_eq!(
            effective_default_type_name(&ThingConfig::default(), &ThingConfig::default()),
            None
        );
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
}
