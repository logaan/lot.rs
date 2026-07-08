//! Shared helpers the command handlers lean on: opening the resolved vault and
//! resolving a Thing id from the command line or the environment.

use anyhow::{bail, Context, Result};
use lot_core::Vault;
use std::ffi::OsString;

/// Resolve the vault settings (honouring `LOT_VAULT_PATH`, else config —
/// creating it on first run) and open the vault (initialising it on first
/// run), honouring the `vault.auto-commit` setting.
pub(crate) fn open_vault() -> Result<Vault> {
    let settings = lot_core::resolve_vault_settings().context("resolving vault settings")?;
    let vault = Vault::open_with(settings.path, settings.auto_commit).context("opening vault")?;
    Ok(vault)
}

/// Resolve a Thing id: an explicit command-line value wins; otherwise fall back
/// to the `LOT_THING_ID` environment variable. Errors when neither is present.
pub(crate) fn resolve_thing(arg: Option<String>) -> Result<String> {
    resolve_thing_with(arg, std::env::var_os(lot_core::env::THING_ID))
}

/// The id-resolution logic, with the environment value injected so it can be
/// tested without touching the process environment.
fn resolve_thing_with(arg: Option<String>, env: Option<OsString>) -> Result<String> {
    if let Some(id) = arg.filter(|s| !s.trim().is_empty()) {
        return Ok(id);
    }
    if let Some(env) = env {
        let env = env.to_string_lossy();
        let env = env.trim();
        if !env.is_empty() {
            return Ok(env.to_string());
        }
    }
    bail!("a thing id is required: pass it as an argument or set LOT_THING_ID");
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
            resolve_thing_with(Some("lot:arg".into()), os("lot:env")).unwrap(),
            "lot:arg"
        );
        // With no argument, fall back to LOT_THING_ID.
        assert_eq!(resolve_thing_with(None, os("lot:env")).unwrap(), "lot:env");
        // A blank argument is treated as absent and falls back too.
        assert_eq!(
            resolve_thing_with(Some("  ".into()), os("lot:env")).unwrap(),
            "lot:env"
        );
        // Neither present -> an error.
        assert!(resolve_thing_with(None, None).is_err());
        // A blank env var doesn't count.
        assert!(resolve_thing_with(None, os("   ")).is_err());
    }
}
