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
        ThingCommand::New { editor, name } => {
            let name = name.join(" ");
            if name.trim().is_empty() {
                bail!("a name is required: lot thing new -- My Thing Name");
            }
            let contents = if editor {
                match read_via_editor()? {
                    Some(c) => c,
                    None => {
                        // Empty file: treat as a cancel, create nothing.
                        eprintln!("aborted: editor saved an empty file; no thing created");
                        return Ok(());
                    }
                }
            } else {
                read_stdin().unwrap_or_default()
            };
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

/// The editor command to launch: `$VISUAL`, then `$EDITOR`, falling back to
/// `nvim`.
fn editor_command() -> String {
    pick_editor(std::env::var_os("VISUAL"), std::env::var_os("EDITOR"))
}

/// Choose an editor command from the `VISUAL` / `EDITOR` values, falling back to
/// `nvim`. Blank/whitespace-only values are ignored so an empty `EDITOR=`
/// doesn't shadow the fallback.
fn pick_editor(visual: Option<std::ffi::OsString>, editor: Option<std::ffi::OsString>) -> String {
    for value in [visual, editor].into_iter().flatten() {
        let value = value.to_string_lossy().trim().to_string();
        if !value.is_empty() {
            return value;
        }
    }
    "nvim".to_string()
}

/// Open a fresh temp file in the user's editor and return its contents.
///
/// Returns `Ok(None)` when the saved file is empty (or only whitespace), which
/// the caller treats as a cancellation. The temp file is removed before
/// returning. The editor string is split on whitespace so values like
/// `code --wait` work.
fn read_via_editor() -> Result<Option<String>> {
    let tmp = std::env::temp_dir().join(format!("lot-new-{}.md", lot_core::id::new()));
    std::fs::write(&tmp, b"").with_context(|| format!("creating temp file {}", tmp.display()))?;

    let editor = editor_command();
    let mut parts = editor.split_whitespace();
    let program = parts
        .next()
        .context("no editor configured ($VISUAL/$EDITOR) and nvim fallback was empty")?;
    let status = ProcessCommand::new(program)
        .args(parts)
        .arg(&tmp)
        .status()
        .with_context(|| format!("failed to launch editor {editor:?}"))?;
    if !status.success() {
        let _ = std::fs::remove_file(&tmp);
        bail!("editor {editor:?} exited with status {status}");
    }

    let contents = std::fs::read_to_string(&tmp)
        .with_context(|| format!("reading temp file {}", tmp.display()))?;
    let _ = std::fs::remove_file(&tmp);

    if contents.trim().is_empty() {
        Ok(None)
    } else {
        Ok(Some(contents))
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

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;

    fn os(s: &str) -> Option<OsString> {
        Some(OsString::from(s))
    }

    #[test]
    fn editor_prefers_visual_then_editor_then_nvim() {
        assert_eq!(pick_editor(os("vim"), os("emacs")), "vim");
        assert_eq!(pick_editor(None, os("emacs")), "emacs");
        assert_eq!(pick_editor(None, None), "nvim");
    }

    #[test]
    fn editor_ignores_blank_values() {
        // An exported-but-empty VISUAL must not shadow EDITOR or the fallback.
        assert_eq!(pick_editor(os("   "), os("hx")), "hx");
        assert_eq!(pick_editor(os(""), None), "nvim");
    }
}
