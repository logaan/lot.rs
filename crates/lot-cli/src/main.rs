mod cli;
mod commands;
mod context;
mod editor;
mod help;

use anyhow::Result;
use clap::Parser;
use cli::{Cli, Command};

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Vault(cmd) => commands::vault::run(cmd),
        Command::Thing(cmd) => commands::thing::run(cmd),
        Command::Update(cmd) => commands::update::run(cmd),
        Command::Settings(cmd) => commands::settings::run(cmd),
        Command::Claude(cmd) => commands::claude::run(cmd),
        Command::Interface => commands::ui::run_interface(),
        Command::Web(args) => commands::ui::run_web(args),
        Command::Watch(thing) => commands::watch::run(thing),
        Command::Help(args) => commands::help::run(args),
    }
}
