//! `lot interface` / `lot web`: launch the Python Textual UI in a terminal, or
//! serve it to web browsers. Both run a separate UI binary — resolved next to
//! this executable first, then on `PATH` — with the vault's environment
//! applied, differing only in the binary's name and its extra args/env.

use crate::cli::WebArgs;
use crate::context::{apply_vault_env, open_vault};
use anyhow::{bail, Context, Result};
use std::ffi::OsString;
use std::process::Command as ProcessCommand;

/// `lot interface`: launch the Python Textual UI by running the
/// `lot-textual-ui` binary. Prefers a `lot-textual-ui` sitting next to this
/// executable (so an installed pair stay together), falling back to
/// `lot-textual-ui` on `PATH`.
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every `lot`
/// subprocess the TUI spawns hits the same vault regardless of its working
/// directory.
pub(crate) fn run_interface() -> Result<()> {
    launch_ui("lot-textual-ui", |_| {})
}

/// `lot web`: serve the Python Textual UI to web browsers by running the
/// `lot-textual-ui-web` binary (a self-hosted textual-serve server that spawns
/// one `lot-textual-ui` process per browser session).
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every served
/// session (and every `lot` subprocess it spawns) hits the same vault, and
/// `LOT_TEXTUAL_WEB=1` marks web mode for the served app processes. `--host`
/// and `--port` are passed through; the server prints the URL(s) to open.
pub(crate) fn run_web(args: WebArgs) -> Result<()> {
    launch_ui("lot-textual-ui-web", |command| {
        command
            .arg("--host")
            .arg(&args.host)
            .arg("--port")
            .arg(args.port.to_string())
            .env(lot_core::env::TEXTUAL_WEB, "1");
    })
}

/// Resolve a UI binary: prefer `name` sitting next to the current executable
/// (so an installed pair stay together), falling back to the bare `name` on
/// `PATH`.
fn sibling_or_path(name: &str) -> OsString {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join(name)))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| name.into())
}

/// Open the vault and run the UI binary `name` (resolved via
/// [`sibling_or_path`]) with the vault's environment applied. `configure` adds
/// the caller's extra args/env before the launch. Errors when the binary can't
/// be launched or exits unsuccessfully.
fn launch_ui(name: &str, configure: impl FnOnce(&mut ProcessCommand)) -> Result<()> {
    let vault = open_vault()?;
    let program = sibling_or_path(name);
    let mut command = ProcessCommand::new(&program);
    apply_vault_env(&mut command, &vault);
    configure(&mut command);
    let status = command.status().with_context(|| {
        format!("failed to launch {program:?}; is `{name}` installed and on PATH?")
    })?;
    if !status.success() {
        bail!("`{name}` exited with status {status}");
    }
    Ok(())
}
