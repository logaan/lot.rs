//! Names of the environment variables LoT honours.
//!
//! These are shared so every front-end agrees on them: the CLI *reads* them
//! (to locate the vault and to fall back for a thing id), and the TUI *sets*
//! them when it invokes a `lot` command on the user's behalf.

/// Overrides the configured vault path. Takes priority over both the
/// project-local `.lot.toml` and the user config. See
/// [`crate::config::resolve_vault_path`].
pub const VAULT_PATH: &str = "LOT_VAULT_PATH";

/// The default thing id for commands that take one. Used only when no id is
/// given on the command line.
pub const THING_ID: &str = "LOT_THING_ID";
