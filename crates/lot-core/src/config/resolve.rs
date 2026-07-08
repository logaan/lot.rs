//! Vault/env resolution: which vault an invocation opens and whether it
//! auto-commits, honouring the `LOT_VAULT_PATH` environment override.

use super::settings::Config;
use crate::error::Result;
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
/// expanded. Otherwise the configured vault path is loaded (creating the user
/// config from the example on first run), which itself honours a project-local
/// `.lot.toml` over the user config.
pub fn resolve_vault_path() -> Result<PathBuf> {
    Ok(resolve_vault_settings()?.path)
}

/// Resolve the vault path together with the `vault.auto-commit` setting.
///
/// Path resolution is exactly [`resolve_vault_path`]. `auto_commit` always
/// comes from normal config resolution (a project-local `.lot.toml` in the
/// current directory, else the user config): `LOT_VAULT_PATH` overrides only
/// the *path*, so sessions launched by `lot interface`, `lot web`, and
/// `lot claude send` keep the config's auto-commit behaviour rather than
/// silently reverting to the default. The one difference under the override:
/// an absent user config is left uncreated (the invocation stays read-only
/// with respect to config files) and auto-commit keeps its default of `true`.
pub fn resolve_vault_settings() -> Result<VaultSettings> {
    match env_vault_path() {
        Some(path) => Ok(VaultSettings {
            path,
            auto_commit: Config::load_if_exists()?.is_none_or(|config| config.vault.auto_commit),
        }),
        None => {
            let config = Config::load_or_init()?;
            Ok(VaultSettings {
                path: config.vault_path(),
                auto_commit: config.vault.auto_commit,
            })
        }
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
        // This test owns all `LOT_VAULT_PATH` mutation: tests run in parallel
        // threads, so no other test may touch this process-wide variable.

        // Blank/unset env -> no override (config is consulted instead).
        std::env::remove_var(crate::env::VAULT_PATH);
        assert_eq!(env_vault_path(), None);
        std::env::set_var(crate::env::VAULT_PATH, "   ");
        assert_eq!(env_vault_path(), None);

        // A set value wins and has its leading `~` expanded.
        std::env::set_var(crate::env::VAULT_PATH, "~/my-vault");
        let resolved = env_vault_path().unwrap();
        assert!(resolved.is_absolute() || !resolved.starts_with("~"));
        assert!(resolved.ends_with("my-vault"));

        std::env::remove_var(crate::env::VAULT_PATH);
    }
}
