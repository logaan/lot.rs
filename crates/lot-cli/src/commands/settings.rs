//! `lot settings`: read the effective config (`get`) or persist a user-level
//! setting (`set`).

use crate::cli::{Format, SettingsCommand, SettingsSet};
use anyhow::{bail, Context, Result};

/// `get` merges the user-level `[tui]` with the vault-level `[tui]` (vault
/// wins) — all in `lot-core` — and only picks the output format; `yaml` (the
/// default) is the stable shape front-ends parse. `set` writes a single key
/// back into the user config file via `lot-core`, leaving the rest untouched.
pub(crate) fn run(cmd: SettingsCommand) -> Result<()> {
    match cmd {
        SettingsCommand::Get { format } => {
            let effective =
                lot_core::load_effective_config().context("resolving effective config")?;
            let out = match format {
                Format::Yaml => effective.to_yaml().context("rendering config YAML")?,
                Format::Markdown => render_config_markdown(&effective),
            };
            print!("{out}");
        }
        SettingsCommand::Set(SettingsSet::Theme { name }) => {
            if name.trim().is_empty() {
                bail!("a theme name is required: lot settings set theme <name>");
            }
            let path = lot_core::set_user_theme(&name).context("writing the theme to config")?;
            // Confirm what was written and where, so the change is traceable.
            println!("set theme = {name:?} in {}", path.display());
        }
    }
    Ok(())
}

/// A simple human-readable view of the effective config for `--format=markdown`.
/// The `yaml` form is the machine-readable surface; this is a convenience.
fn render_config_markdown(cfg: &lot_core::EffectiveConfig) -> String {
    let mut out = String::from("# Effective config\n\n");
    out.push_str(&format!("- vault-path: {}\n", cfg.vault_path));
    out.push_str(&format!(
        "- theme: {}\n",
        cfg.theme.as_deref().unwrap_or("(none)")
    ));
    out.push_str("- keybindings:\n");
    if cfg.keybindings.is_empty() {
        out.push_str("  - (none)\n");
    } else {
        for (action, key) in &cfg.keybindings {
            out.push_str(&format!("  - {action}: {key}\n"));
        }
    }
    out.push_str("- vaults:\n");
    if cfg.vaults.is_empty() {
        out.push_str("  - (none)\n");
    } else {
        for entry in &cfg.vaults {
            match &entry.name {
                Some(name) => out.push_str(&format!("  - {} ({})\n", name, entry.path)),
                None => out.push_str(&format!("  - {}\n", entry.path)),
            }
        }
    }
    out.push_str("- update-types:\n");
    for t in &cfg.update_types {
        out.push_str(&format!(
            "  - {} (takes-body: {}, terminal: {})\n",
            t.name, t.takes_body, t.terminal
        ));
    }
    out.push_str(&format!(
        "- default-update-type: {}\n",
        cfg.default_update_type
    ));
    out
}
