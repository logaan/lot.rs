//! Config-file IO on [`Config`]: locating the file to read (project-local
//! `.lot.toml` over the user config), seeding it from the bundled example on
//! first run, and format-preserving edits.

use super::resolve::dirs_config_dir;
use super::settings::Config;
use crate::error::{io_err, Error, Result};
use std::path::{Path, PathBuf};

/// The example config that is written out on first run when no config exists.
pub const EXAMPLE_CONFIG: &str = include_str!("../../../../data/config.example.toml");

/// Name of the project-local config file. When present in the current
/// directory it overrides the user config, letting a project point `lot` at its
/// own vault.
pub const PROJECT_CONFIG_FILENAME: &str = ".lot.toml";

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
        Self::load_at(path)
    }

    /// Load the resolved config file — a project-local `.lot.toml` in the
    /// current directory when one exists, otherwise the user config — without
    /// creating anything when neither file exists.
    ///
    /// This is the read path the config-layer merge uses under a
    /// `LOT_VAULT_PATH` override: user-level preferences still apply, but an
    /// env-driven invocation never seeds a config file as a side effect.
    pub fn load_if_exists() -> Result<Option<Config>> {
        let cwd = std::env::current_dir()?;
        let path = Self::resolve_path(&cwd, Self::default_path()?);
        Self::load_if_exists_at(&path)
    }

    /// Load the config from `path` when the file exists; `None` (never a
    /// created file) when it does not.
    fn load_if_exists_at(path: &Path) -> Result<Option<Config>> {
        if !path.is_file() {
            return Ok(None);
        }
        Self::load_at(path).map(Some)
    }

    /// Load the *user* config (`~/.config/lot/config.toml`) specifically,
    /// creating it from the bundled example on first run. Unlike
    /// [`load_or_init`](Self::load_or_init) this never resolves to a
    /// project-local `.lot.toml`: it is the base user layer that a project-local
    /// file overlays (see [`Config::overlaid_with_project`] and the
    /// `load_config_layers` merge).
    pub fn load_user_or_init() -> Result<Config> {
        Self::load_or_init_at(&Self::default_path()?)
    }

    /// Load the *user* config (`~/.config/lot/config.toml`) when it exists,
    /// without creating it. The read-only counterpart to
    /// [`load_user_or_init`](Self::load_user_or_init) used under a
    /// `LOT_VAULT_PATH` override, where an env-driven invocation must not seed
    /// a config file as a side effect.
    pub fn load_user_if_exists() -> Result<Option<Config>> {
        Self::load_if_exists_at(&Self::default_path()?)
    }

    /// Load the project-local `.lot.toml` in the current directory when one is
    /// present; `None` otherwise. Never created — a project-local file is only
    /// ever read. This is the overlay applied on top of the user config (see
    /// [`Config::overlaid_with_project`]).
    pub fn load_project_if_exists() -> Result<Option<Config>> {
        let cwd = std::env::current_dir()?;
        Self::load_if_exists_at(&cwd.join(PROJECT_CONFIG_FILENAME))
    }

    /// Parse the config file at `path`.
    fn load_at(path: &Path) -> Result<Config> {
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

    /// Write `[tui].theme = <theme>` into the config file at `path`, preserving
    /// everything else, and creating the file from the bundled example first
    /// when it does not yet exist (matching [`Config::load_or_init_at`]).
    ///
    /// The write goes through `toml_edit` so the rest of the document survives
    /// verbatim: other keys, comments, and whitespace are untouched, and an
    /// existing `[tui]` table keeps its other settings — only the `theme` key is
    /// inserted or replaced. A missing `[tui]` table is created.
    pub fn set_theme_at(path: &Path, theme: &str) -> Result<()> {
        if !path.exists() {
            if let Some(parent) = path.parent() {
                std::fs::create_dir_all(parent).map_err(io_err(parent))?;
            }
            std::fs::write(path, EXAMPLE_CONFIG).map_err(io_err(path))?;
        }
        let raw = std::fs::read_to_string(path).map_err(io_err(path))?;
        let mut doc =
            raw.parse::<toml_edit::DocumentMut>()
                .map_err(|source| Error::ConfigEdit {
                    path: path.to_path_buf(),
                    source: Box::new(source),
                })?;
        // Seed a proper `[tui]` header table when absent (rather than letting an
        // index auto-vivify an inline `tui = { … }` at the top of the file), so
        // the written config reads like the hand-authored example. An existing
        // `[tui]` — table or inline — is left in place and only its theme set.
        if !doc.contains_key("tui") {
            doc.insert("tui", toml_edit::Item::Table(toml_edit::Table::new()));
        }
        doc["tui"]["theme"] = toml_edit::value(theme);
        std::fs::write(path, doc.to_string()).map_err(io_err(path))?;
        Ok(())
    }
}

/// Persist the front-end `theme` into the user-level config file, returning the
/// path written.
///
/// The key lands in the same config file `lot` reads for user-level settings —
/// a project-local `.lot.toml` in the current directory when one exists,
/// otherwise `~/.config/lot/config.toml` (see [`Config::load_or_init`]) —
/// created from the bundled example first when it does not yet exist. Unlike
/// vault resolution this deliberately ignores `LOT_VAULT_PATH`: the theme is a
/// user preference, not a per-invocation vault override, so a front-end
/// launched with `LOT_VAULT_PATH` set (every `lot interface` session) still
/// writes to the user config rather than nowhere.
///
/// The edit is format-preserving: only `[tui].theme` is inserted or updated;
/// the rest of the file — its other keys, comments, and layout — is left as it
/// was (see [`Config::set_theme_at`]).
pub fn set_user_theme(theme: &str) -> Result<PathBuf> {
    let cwd = std::env::current_dir()?;
    let path = Config::resolve_path(&cwd, Config::default_path()?);
    Config::set_theme_at(&path, theme)?;
    Ok(path)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn example_config_parses() {
        let cfg: Config = toml::from_str(EXAMPLE_CONFIG).unwrap();
        assert!(!cfg.vault.path.is_empty());
        // The example leaves auto-commit unset, so the default applies.
        assert!(cfg.vault.auto_commit);
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
    fn load_if_exists_at_absent_returns_none_without_creating() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lot").join("config.toml");
        assert!(Config::load_if_exists_at(&path).unwrap().is_none());
        // Unlike load_or_init_at, the file must not be seeded as a side effect.
        assert!(!path.exists());
    }

    #[test]
    fn load_if_exists_at_reads_existing_config() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "[vault]\npath = \"~/v\"\n\n[tui]\ntheme = \"ansi-dark\"\n",
        )
        .unwrap();
        let cfg = Config::load_if_exists_at(&path).unwrap().unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
    }

    #[test]
    fn resolves_to_user_config_without_project_file() {
        let dir = tempfile::tempdir().unwrap();
        let default = PathBuf::from("/home/user/.config/lot/config.toml");
        let resolved = Config::resolve_path(dir.path(), default.clone());
        assert_eq!(resolved, default);
    }

    #[test]
    fn set_theme_creates_config_then_writes_it() {
        // With no config yet, the file is seeded from the example and the theme
        // written; reloading reads it back.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("lot").join("config.toml");
        Config::set_theme_at(&path, "ansi-dark").unwrap();
        assert!(path.exists());
        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
    }

    #[test]
    fn set_theme_preserves_existing_content_and_comments() {
        // An existing file with a comment and other keys keeps them; only the
        // theme is added under a fresh `[tui]` table.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(&path, "# my vault\n[vault]\npath = \"~/v\"\n").unwrap();
        Config::set_theme_at(&path, "ansi-dark").unwrap();

        let raw = std::fs::read_to_string(&path).unwrap();
        assert!(raw.contains("# my vault"), "comment survives: {raw}");
        assert!(raw.contains("path = \"~/v\""), "vault path survives: {raw}");

        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.vault.path, "~/v");
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
    }

    #[test]
    fn set_theme_updates_existing_tui_table_in_place() {
        // A pre-existing `[tui]` table keeps its other keys; only theme changes.
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        std::fs::write(
            &path,
            "[vault]\npath = \"~/v\"\n\n[tui]\ntheme = \"nord\"\n\n[tui.keybindings]\nquit = \"q\"\n",
        )
        .unwrap();
        Config::set_theme_at(&path, "ansi-dark").unwrap();

        let cfg = Config::load_or_init_at(&path).unwrap();
        assert_eq!(cfg.tui.theme.as_deref(), Some("ansi-dark"));
        // The sibling keybinding under [tui] is untouched.
        assert_eq!(
            cfg.tui.keybindings.get("quit").map(String::as_str),
            Some("q")
        );
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
