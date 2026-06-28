mod cli;
mod help;

use anyhow::{bail, Context, Result};
use clap::{CommandFactory, Parser};
use cli::{
    ClaudeCommand, Cli, Command, Format, HelpArgs, HelpFormat, ThingCommand, ThingFlag, ThingRef,
    UpdateArgs, UpdateCommand, VaultCommand,
};
use lot_core::skills;
use lot_core::update::UpdateKind;
use lot_core::{render, Vault};
use std::ffi::OsString;
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
        Command::Vault(cmd) => run_vault(cmd),
        Command::Thing(cmd) => run_thing(cmd),
        Command::Update(cmd) => run_update(cmd),
        Command::Claude(cmd) => run_claude(cmd),
        Command::Ui => run_tui(),
        Command::Help(args) => run_help(args),
    }
}

/// `lot help`: print the usual help, or — with `--format=yaml` — the whole
/// command tree as YAML for machine consumers (notably the TUI).
fn run_help(args: HelpArgs) -> Result<()> {
    match args.format {
        Some(HelpFormat::Yaml) => {
            let yaml = help::command_tree_yaml(&Cli::command()).context("rendering help YAML")?;
            print!("{yaml}");
        }
        None => {
            Cli::command().print_help().context("printing help")?;
            println!();
        }
    }
    Ok(())
}

/// Resolve a Thing id: an explicit command-line value wins; otherwise fall back
/// to the `LOT_THING_ID` environment variable. Errors when neither is present.
fn resolve_thing(arg: Option<String>) -> Result<String> {
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

/// Launch the terminal UI by running the `lot-tui` binary. Prefers a `lot-tui`
/// sitting next to this executable (so a cargo/installed pair stay together),
/// falling back to `lot-tui` on `PATH`.
fn run_tui() -> Result<()> {
    let program = std::env::current_exe()
        .ok()
        .and_then(|exe| exe.parent().map(|dir| dir.join("lot-tui")))
        .filter(|candidate| candidate.exists())
        .map(|candidate| candidate.into_os_string())
        .unwrap_or_else(|| "lot-tui".into());
    let status = ProcessCommand::new(&program).status().with_context(|| {
        format!("failed to launch {program:?}; is `lot-tui` installed and on PATH?")
    })?;
    if !status.success() {
        bail!("`lot-tui` exited with status {status}");
    }
    Ok(())
}

fn run_vault(cmd: VaultCommand) -> Result<()> {
    match cmd {
        VaultCommand::New { path } => {
            let vault = Vault::create(&path).context("creating vault")?;
            // Print the vault path so it can be referenced by scripts.
            println!("{}", vault.path().display());
        }
    }
    Ok(())
}

/// Resolve the vault path (honouring `LOT_VAULT_PATH`, else config — creating it
/// on first run) and open the vault (initialising it on first run).
fn open_vault() -> Result<Vault> {
    let path = lot_core::resolve_vault_path().context("resolving vault path")?;
    let vault = Vault::open(path).context("opening vault")?;
    Ok(vault)
}

fn run_thing(cmd: ThingCommand) -> Result<()> {
    match cmd {
        ThingCommand::New {
            editor,
            parent,
            name,
        } => {
            let name = name.join(" ");
            let (name, contents) = if name.trim().is_empty() {
                // No name given: compose both the name and body in the editor
                // from a seeded template (markdown h1 for the name, then the
                // body). This needs an interactive terminal — there's no
                // sensible name to fall back to otherwise.
                if !std::io::stdin().is_terminal() {
                    bail!("a name is required: lot thing new -- My Thing Name");
                }
                match split_name_and_body(&edit_temp_file(NEW_THING_TEMPLATE)?) {
                    Some(parsed) => parsed,
                    None => {
                        // Empty file: treat as a cancel, create nothing.
                        eprintln!("aborted: editor saved an empty file; no thing created");
                        return Ok(());
                    }
                }
            } else {
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
                (name, contents)
            };
            let vault = open_vault()?;
            let thing = match parent {
                Some(parent_id) => vault.new_child_thing(&parent_id, &name, &contents)?,
                None => vault.new_thing(&name, &contents)?,
            };
            // Print the id so the new Thing can be referenced by scripts.
            println!("{}", thing.id()?);
        }
        ThingCommand::Path(ThingRef { thing }) => {
            let thing = resolve_thing(thing)?;
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            println!("{}", found.path().display());
        }
        ThingCommand::Get {
            thing: ThingRef { thing },
            format,
        } => {
            let thing = resolve_thing(thing)?;
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
        UpdateCommand::Work(a) => (UpdateKind::Work, a.thing.clone(), resolve_content(a)?),
        UpdateCommand::Info(a) => (UpdateKind::Info, a.thing.clone(), resolve_content(a)?),
        UpdateCommand::Done(ThingFlag { thing }) => (UpdateKind::Done, thing, String::new()),
    };
    let thing = resolve_thing(thing)?;

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

/// Open a temp `.md` file (seeded with `initial`) in the user's editor and
/// return the saved contents (which may be empty or whitespace-only).
///
/// The temp file is removed before returning. The editor string is split on
/// whitespace so values like `code --wait` work.
fn edit_temp_file(initial: &str) -> Result<String> {
    let tmp = std::env::temp_dir().join(format!("lot-new-{}.md", lot_core::id::new()));
    std::fs::write(&tmp, initial)
        .with_context(|| format!("creating temp file {}", tmp.display()))?;

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
    Ok(contents)
}

/// Open a fresh temp file to compose a Thing's *contents* (the name is supplied
/// separately).
///
/// Returns `Ok(None)` when the saved file is empty (or only whitespace), which
/// the caller treats as a cancellation.
fn read_via_editor() -> Result<Option<String>> {
    let contents = edit_temp_file("")?;
    if contents.trim().is_empty() {
        Ok(None)
    } else {
        Ok(Some(contents))
    }
}

/// Template seeded into the editor when composing a Thing with no name.
///
/// Line 1 is the markdown h1 the user names the Thing on (type after `# ` — in
/// vim, `A` appends there). Line 2 is a throwaway one-line comment, stripped on
/// save, sitting where the blank separator would be. The trailing blank line is
/// the body (in vim, `G` jumps to it).
const NEW_THING_TEMPLATE: &str = concat!(
    "# \n",
    "<!-- Name the note on the h1 above; body below. Empty name cancels. -->\n",
    "\n",
);

/// Split the [`NEW_THING_TEMPLATE`] editor buffer into a Thing's `(name, body)`.
///
/// The name is the first non-blank line's markdown h1 text (the `# ...` line);
/// the body is everything after it. The throwaway one-line comment is stripped.
///
/// Returns `None` when no name was entered (the h1 is empty or whitespace-only),
/// which the caller treats as a cancellation.
fn split_name_and_body(buf: &str) -> Option<(String, String)> {
    // Drop the throwaway one-line markdown comment (`<!-- ... -->`).
    let mut lines = buf.lines().filter(|line| {
        let t = line.trim();
        !(t.starts_with("<!--") && t.ends_with("-->"))
    });
    // The name is the first non-blank line, with its `#` heading markers
    // stripped. A bare `# ` (no text) means nothing was entered: cancel.
    let name = loop {
        let trimmed = lines.next()?.trim();
        if trimmed.is_empty() {
            continue;
        }
        let heading = trimmed.trim_start_matches('#').trim();
        if heading.is_empty() {
            return None;
        }
        break heading.to_string();
    };
    // Everything after the name is the body; trim surrounding blank lines.
    let body = lines.collect::<Vec<_>>().join("\n").trim().to_string();
    Some((name, body))
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
            let thing = resolve_thing(thing)?;
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

    #[test]
    fn split_parses_h1_name_and_body() {
        // The template: h1 name line, throwaway comment, then the body.
        let (name, body) =
            split_name_and_body("# Buy milk\n<!-- hint -->\nGet the oat one\nand some bread")
                .unwrap();
        assert_eq!(name, "Buy milk");
        assert_eq!(body, "Get the oat one\nand some bread");
    }

    #[test]
    fn split_name_only_yields_empty_body() {
        // Name typed, body left as the template's trailing blank line.
        let (name, body) = split_name_and_body("# Just a title\n<!-- hint -->\n\n").unwrap();
        assert_eq!(name, "Just a title");
        assert_eq!(body, "");
    }

    #[test]
    fn split_keeps_markdown_headings_in_body() {
        // Only the first h1 is the name; headings inside the body survive.
        let (name, body) =
            split_name_and_body("#  Title here  \n<!-- hint -->\n\n# Heading\n- a\n- b\n").unwrap();
        assert_eq!(name, "Title here");
        assert_eq!(body, "# Heading\n- a\n- b");
    }

    #[test]
    fn split_empty_name_is_a_cancellation() {
        // The unedited template (bare `# `) cancels, as does a blank file.
        assert!(split_name_and_body(NEW_THING_TEMPLATE).is_none());
        assert!(split_name_and_body("# \n<!-- hint -->\n\n").is_none());
        assert!(split_name_and_body("").is_none());
        assert!(split_name_and_body("   \n\n\t\n").is_none());
    }
}
