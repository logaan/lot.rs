//! Shared helpers the command handlers lean on: opening the resolved vault and
//! resolving a Thing id from the command line or the environment.

use anyhow::{bail, Context, Result};
use lot_core::Vault;
use std::ffi::OsString;
use std::process::Command as ProcessCommand;

/// Resolve the vault settings (honouring `LOT_VAULT_PATH`, else config —
/// creating it on first run) and open the vault (initialising it on first
/// run), honouring the `vault.auto-commit` setting.
pub(crate) fn open_vault() -> Result<Vault> {
    let settings = lot_core::resolve_vault_settings().context("resolving vault settings")?;
    let vault = Vault::open_with(settings.path, settings.auto_commit).context("opening vault")?;
    Ok(vault)
}

/// Apply the vault's environment to a child process: `LOT_VAULT_PATH`, so
/// every `lot` invocation the child makes hits this vault regardless of its
/// working directory. Only the path is forwarded — settings like auto-commit
/// come from the child's own config resolution. Every process `lot` spawns on
/// the vault's behalf — the Textual UI, the web server, `claude` — gets this
/// same variable.
pub(crate) fn apply_vault_env(command: &mut ProcessCommand, vault: &Vault) {
    command.env(lot_core::env::VAULT_PATH, vault.path());
}

/// Warn on stderr when config defines no update types at all.
///
/// Commands that still *succeed* without any types (`lot settings get`,
/// `lot help`, `lot vault archive`) call this so a typeless config never
/// passes silently; commands that *need* a type (`lot update <name>`,
/// `lot thing new`) fail instead with the equivalent hard error from
/// lot-core. There is no fallback set: the stock types are only seeded into
/// new vault configs.
pub(crate) fn warn_if_no_update_types(types: &[lot_core::UpdateType]) {
    if types.is_empty() {
        eprintln!(
            "warning: no update types are configured, so updates cannot be created; \
             add [[update-types]] entries to ~/.config/lot/config.toml or \
             <vault>/.lot/config.toml (new vaults are seeded with note, work, info, and done)"
        );
    }
}

/// Resolve a Thing id: an explicit command-line value wins; otherwise fall back
/// to the `LOT_THING_ID` environment variable. Errors when neither is present.
pub(crate) fn resolve_thing(arg: Option<String>) -> Result<String> {
    match resolve_thing_optional(arg) {
        Some(id) => Ok(id),
        None => bail!("a thing id is required: pass it as an argument or set LOT_THING_ID"),
    }
}

/// Resolve an optional Thing id: an explicit command-line value wins; otherwise
/// fall back to the `LOT_THING_ID` environment variable. Unlike [`resolve_thing`],
/// a missing value on both sides is not an error — it resolves to `None`, for
/// commands where a Thing id merely scopes behaviour (e.g. `lot watch --thing`).
pub(crate) fn resolve_thing_optional(arg: Option<String>) -> Option<String> {
    resolve_thing_optional_with(arg, std::env::var_os(lot_core::env::THING_ID))
}

/// The id-resolution logic, with the environment value injected so it can be
/// tested without touching the process environment.
fn resolve_thing_optional_with(arg: Option<String>, env: Option<OsString>) -> Option<String> {
    if let Some(id) = arg.filter(|s| !s.trim().is_empty()) {
        return Some(id);
    }
    if let Some(env) = env {
        let env = env.to_string_lossy();
        let env = env.trim();
        if !env.is_empty() {
            return Some(env.to_string());
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    fn os(s: &str) -> Option<OsString> {
        Some(OsString::from(s))
    }

    #[test]
    fn thing_id_prefers_argument_then_env() {
        // An explicit id always wins, even when the env var is set.
        assert_eq!(
            resolve_thing_optional_with(Some("lot:arg".into()), os("lot:env")),
            Some("lot:arg".to_string())
        );
        // With no argument, fall back to LOT_THING_ID.
        assert_eq!(
            resolve_thing_optional_with(None, os("lot:env")),
            Some("lot:env".to_string())
        );
        // A blank argument is treated as absent and falls back too.
        assert_eq!(
            resolve_thing_optional_with(Some("  ".into()), os("lot:env")),
            Some("lot:env".to_string())
        );
        // Neither present -> None.
        assert_eq!(resolve_thing_optional_with(None, None), None);
        // A blank env var doesn't count.
        assert_eq!(resolve_thing_optional_with(None, os("   ")), None);
    }
}
