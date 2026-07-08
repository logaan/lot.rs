//! Vault/env resolution: which vault an invocation opens and whether it
//! auto-commits, honouring the `LOT_VAULT_PATH` and `LOT_AUTO_COMMIT`
//! environment overrides.

use super::settings::Config;
use crate::error::{Error, Result};
use std::path::PathBuf;

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
pub(super) fn env_vault_path() -> Option<PathBuf> {
    let raw = std::env::var_os(crate::env::VAULT_PATH)?;
    let raw = raw.to_string_lossy();
    let trimmed = raw.trim();
    (!trimmed.is_empty()).then(|| PathBuf::from(shellexpand::tilde(trimmed).into_owned()))
}

/// Resolve the platform config directory without pulling in the `dirs` crate:
/// `$HOME/.config` on every platform (XDG-style on macOS too, matching common
/// dotfile setups).
pub(super) fn dirs_config_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".config"))
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
