//! LoT configuration: the user/vault config files and their merge rules.
//!
//! Split along its three responsibilities:
//!
//! * [`settings`](self) — the front-end settings types (`[tui]`,
//!   `[[update-types]]`, `[thing]`), their user/vault merge rules, and the
//!   effective-config assembly (`settings.rs`).
//! * config-file IO on [`Config`] — locating, loading/seeding, and editing
//!   the user config file (`file.rs`).
//! * vault/env resolution — [`VaultSettings`] and the `LOT_VAULT_PATH` /
//!   `LOT_AUTO_COMMIT` overrides (`resolve.rs`).
//!
//! Everything public is re-exported here, so consumers keep using
//! `config::…` paths.

mod file;
mod resolve;
mod settings;

pub use file::{set_user_theme, EXAMPLE_CONFIG, PROJECT_CONFIG_FILENAME};
pub use resolve::{resolve_vault_path, resolve_vault_settings, VaultSettings};
pub use settings::{
    load_default_update_type, load_effective_config, load_update_types, Config, EffectiveConfig,
    ThingConfig, TuiConfig, VaultConfig, VaultEntry, VaultLevelConfig, VAULT_CONFIG_RELATIVE_PATH,
};
