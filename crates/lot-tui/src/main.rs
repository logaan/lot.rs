//! `lot-tui`: a read-only terminal UI over a LoT vault.
//!
//! Kept entirely separate from `lot-cli`; both are thin front-ends over
//! `lot-core`. Launch it directly or via `lot tui`.

mod app;
mod markdown;
mod model;
mod ui;

use anyhow::{Context, Result};
use app::App;
use lot_core::{Config, Vault};
use ratatui::backend::CrosstermBackend;
use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyEventKind,
};
use ratatui::crossterm::execute;
use ratatui::crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::Terminal;
use std::io;
use std::process::Command as ProcessCommand;

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    // Load the vault before touching the terminal so any error prints cleanly.
    let config = Config::load_or_init().context("loading config")?;
    let vault = Vault::open(config.vault_path()).context("opening vault")?;
    let rows = model::load_rows(&vault).context("reading things")?;
    let mut app = App::new(rows, vault.path().display().to_string());

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
    loop {
        terminal.draw(|f| ui::draw(f, app))?;
        match event::read()? {
            Event::Key(key) if key.kind == KeyEventKind::Press => app.on_key(key),
            Event::Mouse(mouse) => app.on_mouse(mouse),
            _ => {}
        }
        if app.quit {
            return Ok(());
        }
        if app.new_thing {
            app.new_thing = false;
            create_thing(terminal, app, vault)?;
        }
    }
}

/// Stand the TUI aside, run `lot thing new` (which drops the user into their
/// editor to compose the Thing, git-commit style), then resume and reload the
/// vault so the new Thing appears.
fn create_thing(terminal: &mut Tui, app: &mut App, vault: &Vault) -> Result<()> {
    restore_terminal(terminal).context("suspending the TUI")?;
    let launched = run_lot_thing_new();
    // Re-enter the alternate screen and raw mode before reporting anything, so
    // an error doesn't print over the editor's leftovers. A fresh `Terminal`
    // has empty buffers, so the next draw repaints everything.
    *terminal = setup_terminal().context("resuming the TUI")?;

    // Surface a launch failure (e.g. `lot` not found) only now the TUI is back.
    // A non-zero exit from `lot thing new` itself (e.g. a cancelled editor) is
    // not fatal: nothing was created, and the reload below is a harmless no-op.
    launched?;

    app.rows = model::load_rows(vault).context("reloading things")?;
    // Keep the cursor in range; rows can only have grown, but clamp defensively.
    app.cursor = app.cursor.min(app.rows.len().saturating_sub(1));
    app.detail_scroll = 0;
    Ok(())
}

/// Run `lot thing new` as a child process, inheriting this terminal so the
/// editor it spawns takes over the screen. Prefers a `lot` binary sitting next
/// to this `lot-tui` executable (so a cargo/installed pair stay together),
/// falling back to `lot` on `PATH` — mirroring how `lot tui` finds `lot-tui`.
fn run_lot_thing_new() -> Result<()> {
    let program = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("lot")))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| "lot".into());
    ProcessCommand::new(&program)
        .args(["thing", "new"])
        .status()
        .with_context(|| {
            format!("failed to launch {program:?}; is `lot` installed and on PATH?")
        })?;
    Ok(())
}
