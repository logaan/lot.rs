use crate::error::{io_err, Error, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// The example config that is written out on first run when no config exists.
pub const EXAMPLE_CONFIG: &str = include_str!("../../../data/config.example.toml");

/// LoT configuration, read from `config.toml`.
#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct Config {
    pub vault: VaultConfig,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct VaultConfig {
    /// The path to the vault. May contain a leading `~` which is expanded
    /// against the user's home directory.
    pub path: String,
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

    /// Load the config from the default path, creating it from the bundled
    /// example if it does not yet exist.
    pub fn load_or_init() -> Result<Config> {
        let path = Self::default_path()?;
        Self::load_or_init_at(&path)
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
    }

    #[test]
    fn creates_config_on_first_load() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lot").join("config.toml");
        let cfg = Config::load_or_init_at(&path).unwrap();
        assert!(path.exists());
        assert!(!cfg.vault.path.is_empty());
    }
}
