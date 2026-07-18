//! Vault/env resolution: which vault an invocation opens and whether it
//! auto-commits, honouring the `LOT_VAULT_PATH` environment override.

use super::settings::load_user_layer;
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
/// config from the example on first run) from the merged user layer, where a
/// project-local `.lot.toml` overlays the user config (see
/// [`load_user_layer`]).
///
/// The result is always absolute — see [`resolve_vault_settings`].
pub fn resolve_vault_path() -> Result<PathBuf> {
    Ok(resolve_vault_settings()?.path)
}

/// Resolve the vault path together with the `vault.auto-commit` setting.
///
/// Path resolution is exactly [`resolve_vault_path`]. `auto_commit` always
/// comes from the merged user layer (the user config overlaid by a
/// project-local `.lot.toml` — see [`load_user_layer`]): `LOT_VAULT_PATH`
/// overrides only the *path*, so sessions launched by `lot interface`,
/// `lot web`, and `lot claude send` keep the config's auto-commit behaviour
/// rather than silently reverting to the default. When no config file exists
/// (possible only under the override, which never seeds one) auto-commit
/// keeps its default of `true`.
///
/// The resolved path is always **absolute**: a relative one (from either
/// source) is joined onto the current directory, which is what it already
/// meant for this process. Making that explicit matters because the path is
/// handed to child processes as `LOT_VAULT_PATH` (see `apply_vault_env` in
/// lot-cli), and a child that runs elsewhere — a `claude` session working in a
/// git worktree, say — would otherwise resolve the same relative value against
/// *its* directory and quietly open (or create) a different vault.
pub fn resolve_vault_settings() -> Result<VaultSettings> {
    let config = load_user_layer()?;
    let auto_commit = config
        .as_ref()
        .is_none_or(|config| config.vault.auto_commit);
    let path = match env_vault_path() {
        Some(path) => path,
        None => config
            .expect("the user config is seeded when LOT_VAULT_PATH is unset")
            .vault_path(),
    };
    Ok(VaultSettings {
        path: absolutize(path)?,
        auto_commit,
    })
}

/// Make `path` absolute by joining a relative one onto the current directory.
///
/// Deliberately not `std::fs::canonicalize`: the vault may not exist yet (the
/// first `lot` command in a new vault creates it), and canonicalising fails on
/// a missing path. Symlinks are left as they are for the same reason — the aim
/// is a path that means the same thing from any working directory, not a
/// unique one.
fn absolutize(path: PathBuf) -> Result<PathBuf> {
    if path.is_absolute() {
        return Ok(path);
    }
    Ok(std::env::current_dir()?.join(path))
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

    #[test]
    fn absolutize_joins_relative_paths_onto_the_current_directory() {
        // An absolute path is returned untouched, missing target and all.
        let absolute = PathBuf::from("/nowhere/in/particular/vault");
        assert_eq!(absolutize(absolute.clone()).unwrap(), absolute);

        // A relative one is anchored to the current directory, so it still
        // names the same vault once handed to a process running elsewhere.
        let cwd = std::env::current_dir().unwrap();
        assert_eq!(
            absolutize(PathBuf::from(".lot-vault")).unwrap(),
            cwd.join(".lot-vault")
        );
    }
}
