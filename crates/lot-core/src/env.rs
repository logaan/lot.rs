//! Names of the environment variables LoT honours.
//!
//! These are shared so every front-end agrees on them: the CLI *reads* them
//! (to locate the vault and to fall back for a thing id), and launchers such
//! as `lot interface`, `lot web`, and `lot claude send` *set* them so the
//! `lot` commands their children run keep the launching configuration.

/// Overrides the configured vault path — and only the path: every other
/// setting (auto-commit included) still comes from normal config resolution.
/// Takes priority over both the project-local `.lot.toml` and the user
/// config. See [`crate::config::resolve_vault_path`].
pub const VAULT_PATH: &str = "LOT_VAULT_PATH";

/// The default thing id for commands that take one. Used only when no id is
/// given on the command line.
pub const THING_ID: &str = "LOT_THING_ID";

/// Marks that the Textual UI is being served to a web browser rather than run
/// in a terminal: `"1"` when serving, unset otherwise. `lot web` sets it on
/// the server process; textual-serve copies the environment into every
/// per-session app process, so the app can detect web mode and adapt.
pub const TEXTUAL_WEB: &str = "LOT_TEXTUAL_WEB";
