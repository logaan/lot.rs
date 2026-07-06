//! Core logic for **Lists of Things (LoT)**.
//!
//! This crate is deliberately free of any CLI concerns so it can be reused by
//! future TUI / web / WebAssembly front-ends. It models three things:
//!
//! * [`config::Config`] — where the vault lives.
//! * [`vault::Vault`] — a git-backed directory of [`thing::Thing`]s.
//! * [`update::UpdateKind`] — the typed, append-only updates that make up a
//!   thing, computed into a current state via [`thing::Thing::compute_state`].

pub mod config;
pub mod env;
pub mod error;
pub mod frontmatter;
pub mod git;
pub mod id;
pub mod render;
pub mod skills;
pub mod thing;
pub mod update;
pub mod vault;
pub mod watch;

pub use config::{
    load_effective_config, load_update_types, resolve_vault_path, resolve_vault_settings,
    set_user_theme, Config, EffectiveConfig, TuiConfig, VaultEntry, VaultLevelConfig,
    VaultSettings,
};
pub use error::{Error, Result};
pub use frontmatter::Document;
pub use thing::Thing;
pub use update::{UpdateKind, UpdateType, UpdateTypeInfo, UpdateTypes};
pub use vault::Vault;
