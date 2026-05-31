mod cli;

use anyhow::{bail, Context, Result};
use clap::Parser;
use cli::{
    ClaudeCommand, Cli, Command, Format, ThingCommand, ThingFlag, ThingRef, UpdateArgs,
    UpdateCommand,
};
use lot_core::skills;
use lot_core::update::UpdateKind;
use lot_core::{render, Config, Vault};
use std::io::{IsTerminal, Read};
use std::process::Command as ProcessCommand;

fn main() {
    if let Err(err) = run() {
        eprintln!("error: {err:#}");
        std::process::exit(1);
    }
}

fn run() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Command::Thing(cmd) => run_thing(cmd),
        Command::Update(cmd) => run_update(cmd),
        Command::Claude(cmd) => run_claude(cmd),
    }
}

/// Load config (creating it on first run) and open the vault (initialising it
/// on first run).
fn open_vault() -> Result<Vault> {
    let config = Config::load_or_init().context("loading config")?;
    let vault = Vault::open(config.vault_path()).context("opening vault")?;
    Ok(vault)
}

fn run_thing(cmd: ThingCommand) -> Result<()> {
    match cmd {
        ThingCommand::New { name } => {
            let name = name.join(" ");
            if name.trim().is_empty() {
                bail!("a name is required: lot thing new -- My Thing Name");
            }
            let contents = read_stdin().unwrap_or_default();
            let vault = open_vault()?;
            let thing = vault.new_thing(&name, &contents)?;
            // Print the id so the new Thing can be referenced by scripts.
            println!("{}", thing.id()?);
        }
        ThingCommand::Path(ThingRef { thing }) => {
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            println!("{}", found.path().display());
        }
        ThingCommand::Get {
            thing: ThingRef { thing },
            format,
        } => {
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            let state = found.compute_state()?;
            let out = match format {
                Format::Yaml => state.to_yaml()?,
                Format::Markdown => state.render()?,
            };
            print!("{out}");
        }
        ThingCommand::List { format } => {
            let vault = open_vault()?;
            let out = match format {
                Format::Yaml => render::thing_list_yaml(&vault)?,
                Format::Markdown => render::thing_list_markdown(&vault)?,
            };
            print!("{out}");
        }
    }
    Ok(())
}

fn run_update(cmd: UpdateCommand) -> Result<()> {
    let (kind, thing, content) = match cmd {
        UpdateCommand::Task(a) => (UpdateKind::Task, a.thing.clone(), resolve_content(a)?),
        UpdateCommand::Doing(a) => (UpdateKind::Doing, a.thing.clone(), resolve_content(a)?),
        UpdateCommand::Done(a) => (UpdateKind::Done, a.thing.clone(), resolve_content(a)?),
        UpdateCommand::Archive(ThingFlag { thing }) => (UpdateKind::Archive, thing, String::new()),
    };

    let vault = open_vault()?;
    let update_id = vault.add_update(&thing, kind, &content)?;
    // Print the update-id so the new Update can be referenced by scripts.
    println!("{update_id}");
    Ok(())
}

/// Resolve update content from either stdin or the trailing `--` argument,
/// erroring if both are supplied.
fn resolve_content(args: UpdateArgs) -> Result<String> {
    let arg_content = args.content.join(" ");
    let arg_present = !arg_content.trim().is_empty();
    let stdin_content = read_stdin();

    match (arg_present, stdin_content) {
        (true, Some(_)) => bail!(lot_core::Error::AmbiguousContent),
        (true, None) => Ok(arg_content),
        (false, Some(s)) => Ok(s),
        (false, None) => Ok(String::new()),
    }
}

/// Read stdin if it is piped (not a terminal). Returns `None` when stdin is a
/// terminal so interactive invocations don't block.
fn read_stdin() -> Option<String> {
    let stdin = std::io::stdin();
    if stdin.is_terminal() {
        return None;
    }
    let mut buf = String::new();
    if stdin.lock().read_to_string(&mut buf).is_ok() && !buf.is_empty() {
        Some(buf)
    } else {
        None
    }
}

fn run_claude(cmd: ClaudeCommand) -> Result<()> {
    match cmd {
        ClaudeCommand::Install => {
            let written = skills::install()?;
            for path in written {
                println!("installed {}", path.display());
            }
        }
        ClaudeCommand::Send(ThingRef { thing }) => {
            // Validate the Thing exists before spawning Claude.
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            let id = found.id()?;

            let prompt = format!("/{} {}", skills::LOT_TASK_SKILL_NAME, id);
            // Start a background Claude session that loads the lot-task skill.
            let status = ProcessCommand::new("claude")
                .arg("--bg")
                .arg(&prompt)
                .status()
                .context("failed to launch `claude`; is it installed and on PATH?")?;
            if !status.success() {
                bail!("`claude` exited with status {status}");
            }
        }
    }
    Ok(())
}
