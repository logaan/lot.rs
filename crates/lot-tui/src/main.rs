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
    let result = event_loop(&mut terminal, &mut app);
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

fn event_loop(terminal: &mut Tui, app: &mut App) -> Result<()> {
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
    }
}
