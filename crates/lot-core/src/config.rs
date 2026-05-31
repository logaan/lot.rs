use crate::error::{io_err, Error, Result};
use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// The example config that is written out on first run when no config exists.
pub const EXAMPLE_CONFIG: &str = include_str!("../../../data/config.example.toml");

/// Name of the project-local config file. When present in the current
/// directory it overrides the user config, letting a project point `lot` at its
/// own vault.
pub const PROJECT_CONFIG_FILENAME: &str = ".lot.toml";

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

    /// Load the config, creating the user config from the bundled example on
    /// first run.
    ///
    /// A project-local `.lot.toml` in the current working directory takes
    /// precedence over the user config (`~/.config/lot/config.toml`), so a
    /// project can point `lot` at its own vault. The project file is never
    /// auto-created; only the user config is.
    pub fn load_or_init() -> Result<Config> {
        let cwd = std::env::current_dir()?;
        let path = Self::resolve_path(&cwd, Self::default_path()?);
        Self::load_or_init_at(&path)
    }

    /// Decide which config file to load: a project-local `.lot.toml` in `cwd`
    /// when one exists, otherwise the user `default` path.
    fn resolve_path(cwd: &Path, default: PathBuf) -> PathBuf {
        let project = cwd.join(PROJECT_CONFIG_FILENAME);
        if project.is_file() {
            project
        } else {
            default
        }
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

    #[test]
    fn resolves_to_user_config_without_project_file() {
        let dir = tempfile::tempdir().unwrap();
        let default = PathBuf::from("/home/user/.config/lot/config.toml");
        let resolved = Config::resolve_path(dir.path(), default.clone());
        assert_eq!(resolved, default);
    }

    #[test]
    fn project_lot_toml_overrides_user_config() {
        let dir = tempfile::tempdir().unwrap();
        let project = dir.path().join(PROJECT_CONFIG_FILENAME);
        std::fs::write(&project, "[vault]\npath = \"./project-vault\"\n").unwrap();

        let default = PathBuf::from("/home/user/.config/lot/config.toml");
        let resolved = Config::resolve_path(dir.path(), default);
        assert_eq!(resolved, project);

        // And it parses to the project vault path.
        let cfg = Config::load_or_init_at(&resolved).unwrap();
        assert_eq!(cfg.vault.path, "./project-vault");
    }
}
