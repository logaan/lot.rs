//! `lot interface` / `lot web`: launch the Python Textual UI in a terminal, or
//! serve it to web browsers.

use crate::cli::WebArgs;
use crate::context::open_vault;
use anyhow::{bail, Context, Result};
use std::process::Command as ProcessCommand;

/// The environment variable marking that the Textual UI is being served to a
/// web browser rather than run in a terminal. `lot web` sets it on the server
/// process; textual-serve copies the environment into every per-session app
/// process, so the app can detect web mode and adapt.
const TEXTUAL_WEB_ENV: &str = "LOT_TEXTUAL_WEB";

/// `lot interface`: launch the Python Textual UI by running the
/// `lot-textual-ui` binary. Prefers a `lot-textual-ui` sitting next to this
/// executable (so an installed pair stay together), falling back to
/// `lot-textual-ui` on `PATH`.
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every `lot`
/// subprocess the TUI spawns hits the same vault regardless of its working
/// directory.
pub(crate) fn run_interface() -> Result<()> {
    let vault = open_vault()?;
    let program = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("lot-textual-ui")))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| "lot-textual-ui".into());
    let status = ProcessCommand::new(&program)
        .env(lot_core::env::VAULT_PATH, vault.path())
        .env(lot_core::env::AUTO_COMMIT, vault.auto_commit().to_string())
        .status()
        .with_context(|| {
            format!("failed to launch {program:?}; is `lot-textual-ui` installed and on PATH?")
        })?;
    if !status.success() {
        bail!("`lot-textual-ui` exited with status {status}");
    }
    Ok(())
}

/// `lot web`: serve the Python Textual UI to web browsers by running the
/// `lot-textual-ui-web` binary (a self-hosted textual-serve server that spawns
/// one `lot-textual-ui` process per browser session). The binary is resolved
/// next to this executable first, then on `PATH` — mirroring [`run_interface`].
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every served
/// session (and every `lot` subprocess it spawns) hits the same vault, and
/// `LOT_TEXTUAL_WEB=1` marks web mode for the served app processes. `--host`
/// and `--port` are passed through; the server prints the URL(s) to open.
pub(crate) fn run_web(args: WebArgs) -> Result<()> {
    let vault = open_vault()?;
    let program = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("lot-textual-ui-web")))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| "lot-textual-ui-web".into());
    let status = ProcessCommand::new(&program)
        .arg("--host")
        .arg(&args.host)
        .arg("--port")
        .arg(args.port.to_string())
        .env(lot_core::env::VAULT_PATH, vault.path())
        .env(lot_core::env::AUTO_COMMIT, vault.auto_commit().to_string())
        .env(TEXTUAL_WEB_ENV, "1")
        .status()
        .with_context(|| {
            format!("failed to launch {program:?}; is `lot-textual-ui-web` installed and on PATH?")
        })?;
    if !status.success() {
        bail!("`lot-textual-ui-web` exited with status {status}");
    }
    Ok(())
}
