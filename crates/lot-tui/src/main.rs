//! `lot-tui`: a read-only terminal UI over a LoT vault.
//!
//! Kept entirely separate from `lot-cli`; both are thin front-ends over
//! `lot-core`. Launch it directly or via `lot tui`.

mod app;
mod command;
mod markdown;
mod model;
mod ui;

use anyhow::{bail, Context, Result};
use app::App;
use command::CommandNode;
use lot_core::Vault;
use notify::{RecommendedWatcher, RecursiveMode, Watcher};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyEventKind,
};
use ratatui::crossterm::execute;
use ratatui::crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::Terminal;
use std::io::{self, Write};
use std::path::Path;
use std::process::Command as ProcessCommand;
use std::sync::mpsc::{self, Receiver, Sender};
use std::time::Duration;

/// How long the event loop waits for keyboard input before checking the vault
/// watcher's queue. The vault itself is watched (not polled); this only bounds
/// how soon a change shows when the user is idle.
const INPUT_POLL: Duration = Duration::from_millis(200);

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    // Load the vault and discover the command tree before touching the
    // terminal so any error prints cleanly.
    let path = lot_core::resolve_vault_path().context("resolving vault path")?;
    let vault = Vault::open(path).context("opening vault")?;
    let commands = load_command_tree().context("discovering commands")?;
    let rows = model::load_rows(&vault).context("reading things")?;
    let mut app = App::new(rows, vault.path().display().to_string(), commands);

    let mut terminal = setup_terminal().context("setting up terminal")?;
    let result = event_loop(&mut terminal, &mut app, &vault);
    restore_terminal(&mut terminal).context("restoring terminal")?;
    result
}

type Tui = Terminal<CrosstermBackend<io::Stdout>>;

fn setup_terminal() -> Result<Tui> {
    enable_raw_mode()?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)?;
    let terminal = Terminal::new(CrosstermBackend::new(stdout))?;
    Ok(terminal)
}

fn restore_terminal(terminal: &mut Tui) -> Result<()> {
    disable_raw_mode()?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )?;
    terminal.show_cursor()?;
    Ok(())
}

fn event_loop(terminal: &mut Tui, app: &mut App, vault: &Vault) -> Result<()> {
    // Watch the vault so edits from any source — commands run from the TUI,
    // other `lot` invocations, or direct file edits — show up live. notify uses
    // the OS's native backend (FSEvents/inotify), not polling.
    let (tx, vault_changes) = mpsc::channel();
    let _watcher = watch_vault(vault.path(), tx).context("watching the vault")?;

    loop {
        terminal.draw(|f| ui::draw(f, app))?;

        // Wait briefly for input; on timeout we fall through to service the
        // watcher, so an idle TUI still reflects vault changes promptly.
        if event::poll(INPUT_POLL)? {
            match event::read()? {
                Event::Key(key) if key.kind == KeyEventKind::Press => app.on_key(key),
                Event::Mouse(mouse) => app.on_mouse(mouse),
                _ => {}
            }
        }

        // Apply any vault changes the watcher reported, coalesced into a single
        // reload. `reload` re-validates UI state so a vanished selection can't
        // linger.
        if drain(&vault_changes) {
            app.reload(model::load_rows(vault).context("reloading things")?);
        }

        if app.quit {
            return Ok(());
        }
        if let Some(args) = app.invoke.take() {
            invoke_command(terminal, app, vault, &args)?;
            // The command's own writes queued watcher pings; invoke_command
            // already reloaded, so drop them to avoid a redundant reload.
            drain(&vault_changes);
        }
    }
}

/// Start watching `path` recursively, sending a unit ping on each relevant
/// change. The returned watcher must be kept alive for watching to continue.
fn watch_vault(path: &Path, tx: Sender<()>) -> Result<RecommendedWatcher> {
    let mut watcher = notify::recommended_watcher(move |res: notify::Result<notify::Event>| {
        if let Ok(event) = res {
            // Ignore pure access (read) events: our own reloads read files and
            // would otherwise feed back into endless reloads.
            if !matches!(event.kind, notify::EventKind::Access(_)) {
                let _ = tx.send(());
            }
        }
    })?;
    watcher.watch(path, RecursiveMode::Recursive)?;
    Ok(watcher)
}

/// Drain all pending change pings, reporting whether there were any.
fn drain(rx: &Receiver<()>) -> bool {
    let mut changed = false;
    while rx.try_recv().is_ok() {
        changed = true;
    }
    changed
}

/// Discover the `lot` command tree by running `lot help --format=yaml` once at
/// startup, so the palette reflects whatever `lot` is installed.
fn load_command_tree() -> Result<CommandNode> {
    let program = lot_binary();
    let output = ProcessCommand::new(&program)
        .args(["help", "--format=yaml"])
        .output()
        .with_context(|| format!("failed to run {program:?}; is `lot` installed and on PATH?"))?;
    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        bail!("`lot help --format=yaml` failed: {}", stderr.trim());
    }
    let yaml = String::from_utf8_lossy(&output.stdout);
    CommandNode::parse(&yaml).context("parsing `lot help --format=yaml` output")
}

/// Stand the TUI aside, run `lot <args>` so its output (or an editor it spawns)
/// shows in the real terminal, wait for a keypress, then resume and reload the
/// vault so any changes appear.
fn invoke_command(terminal: &mut Tui, app: &mut App, vault: &Vault, args: &[String]) -> Result<()> {
    let thing_id = app.selected_id().map(str::to_string);
    restore_terminal(terminal).context("suspending the TUI")?;
    let launched = run_lot(args, vault, thing_id.as_deref());
    pause_for_key();
    // Re-enter the alternate screen and raw mode before reporting anything, so
    // an error doesn't print over the command's leftovers. A fresh `Terminal`
    // has empty buffers, so the next draw repaints everything.
    *terminal = setup_terminal().context("resuming the TUI")?;

    // Surface a launch failure (e.g. `lot` not found) only now the TUI is back.
    // A non-zero exit from the command itself (e.g. a missing required argument
    // or a cancelled editor) is not fatal: it was shown to the user, and the
    // reload below reflects whatever did or didn't change.
    launched?;

    app.reload(model::load_rows(vault).context("reloading things")?);
    Ok(())
}

/// Run `lot <args>` as a child process inheriting this terminal, with the
/// session's context in the environment so commands can pick up the current
/// vault and selected Thing without further input.
fn run_lot(args: &[String], vault: &Vault, thing_id: Option<&str>) -> Result<()> {
    let program = lot_binary();
    let mut command = ProcessCommand::new(&program);
    command
        .args(args)
        .env(lot_core::env::VAULT_PATH, vault.path());
    if let Some(id) = thing_id {
        command.env(lot_core::env::THING_ID, id);
    }
    // A non-zero exit is the command's own business (it has already reported
    // itself to the user); only a failure to launch is an error here.
    command.status().with_context(|| {
        format!("failed to launch {program:?}; is `lot` installed and on PATH?")
    })?;
    Ok(())
}

/// After a command runs in the plain terminal, wait for a keypress so its
/// output stays on screen until the user is ready to return to the TUI.
fn pause_for_key() {
    print!("\n-- press any key to return to lot-tui --");
    let _ = io::stdout().flush();
    // Read a single key in raw mode so *any* key continues, not just Enter.
    if enable_raw_mode().is_ok() {
        loop {
            match event::read() {
                Ok(Event::Key(k)) if k.kind == KeyEventKind::Press => break,
                Ok(_) => continue,
                Err(_) => break,
            }
        }
        let _ = disable_raw_mode();
    }
    println!();
}

/// The `lot` binary to drive: prefer one sitting next to this `lot-tui`
/// executable (so a cargo/installed pair stay together), falling back to `lot`
/// on `PATH` — mirroring how `lot tui` finds `lot-tui`.
fn lot_binary() -> std::ffi::OsString {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("lot")))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| "lot".into())
}
