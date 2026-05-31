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
pub mod error;
pub mod frontmatter;
pub mod git;
pub mod skills;
pub mod thing;
pub mod update;
pub mod vault;

pub use config::Config;
pub use error::{Error, Result};
pub use frontmatter::Document;
pub use thing::Thing;
pub use update::UpdateKind;
pub use vault::Vault;
