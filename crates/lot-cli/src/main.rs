mod cli;
mod help;

use anyhow::{bail, Context, Result};
use clap::{CommandFactory, Parser};
use cli::{
    ClaudeCommand, Cli, Command, Format, HelpArgs, HelpFormat, SettingsCommand, SettingsSet,
    ThingCommand, ThingFlag, ThingRef, UpdateArgs, UpdateCommand, UpdateRef, VaultCommand, WebArgs,
};
use lot_core::skills;
use lot_core::update::UpdateKind;
use lot_core::{render, Vault};
use std::ffi::OsString;
use std::io::{IsTerminal, Read, Write};
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
        Command::Settings(cmd) => run_settings(cmd),
        Command::Claude(cmd) => run_claude(cmd),
        Command::Interface => run_interface(),
        Command::Web(args) => run_web(args),
        Command::Watch => run_watch(),
        Command::Help(args) => run_help(args),
    }
}

/// `lot help`: print the usual help, or — with `--format=yaml` — the whole
/// command tree as YAML for machine consumers (notably the TUI).
///
/// The YAML tree also lists config-defined update types as sub-commands of
/// `update`, so a front-end's command palette offers them alongside the
/// built-ins (their flags — takes-body/terminal — live in `lot settings get`,
/// the canonical discovery surface).
fn run_help(args: HelpArgs) -> Result<()> {
    match args.format {
        Some(HelpFormat::Yaml) => {
            let types = lot_core::load_update_types().context("resolving update types")?;
            let cmd = help::with_custom_update_types(Cli::command(), types.custom());
            let yaml = help::command_tree_yaml(&cmd).context("rendering help YAML")?;
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

/// `lot interface`: launch the Python Textual UI by running the
/// `lot-textual-ui` binary. Prefers a `lot-textual-ui` sitting next to this
/// executable (so an installed pair stay together), falling back to
/// `lot-textual-ui` on `PATH`.
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every `lot`
/// subprocess the TUI spawns hits the same vault regardless of its working
/// directory.
fn run_interface() -> Result<()> {
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

/// The environment variable marking that the Textual UI is being served to a
/// web browser rather than run in a terminal. `lot web` sets it on the server
/// process; textual-serve copies the environment into every per-session app
/// process, so the app can detect web mode and adapt.
const TEXTUAL_WEB_ENV: &str = "LOT_TEXTUAL_WEB";

/// `lot web`: serve the Python Textual UI to web browsers by running the
/// `lot-textual-ui-web` binary (a self-hosted textual-serve server that spawns
/// one `lot-textual-ui` process per browser session). The binary is resolved
/// next to this executable first, then on `PATH` — mirroring [`run_interface`].
///
/// The resolved vault path is forwarded via `LOT_VAULT_PATH` so every served
/// session (and every `lot` subprocess it spawns) hits the same vault, and
/// `LOT_TEXTUAL_WEB=1` marks web mode for the served app processes. `--host`
/// and `--port` are passed through; the server prints the URL(s) to open.
fn run_web(args: WebArgs) -> Result<()> {
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

/// `lot watch`: watch the resolved vault and stream one YAML event per change on
/// stdout. Each event is framed with a leading `---` document marker and flushed
/// immediately, so a consumer can read one YAML document at a time even off a
/// live pipe. This blocks until the process is interrupted (Ctrl-C).
fn run_watch() -> Result<()> {
    let vault = open_vault()?;
    let mut stdout = std::io::stdout();
    lot_core::watch::watch(&vault, |event| {
        let yaml = event.to_yaml()?;
        // The `---` marker separates documents in the stream; the YAML body is
        // block-style with all content indented, so a bare `---` at column 0
        // only ever marks an event boundary. Flush so live consumers see each
        // event immediately rather than when the OS buffer fills. IO errors
        // convert into `lot_core::Error` via `?`, matching the closure's result
        // type.
        write!(stdout, "---\n{yaml}")?;
        stdout.flush()?;
        Ok(())
    })
    .context("watching the vault")?;
    Ok(())
}

/// `lot settings`: read the effective config (`get`) or persist a user-level
/// setting (`set`).
///
/// `get` merges the user-level `[tui]` with the vault-level `[tui]` (vault
/// wins) — all in `lot-core` — and only picks the output format; `yaml` (the
/// default) is the stable shape front-ends parse. `set` writes a single key
/// back into the user config file via `lot-core`, leaving the rest untouched.
fn run_settings(cmd: SettingsCommand) -> Result<()> {
    match cmd {
        SettingsCommand::Get { format } => {
            let effective =
                lot_core::load_effective_config().context("resolving effective config")?;
            let out = match format {
                Format::Yaml => effective.to_yaml().context("rendering config YAML")?,
                Format::Markdown => render_config_markdown(&effective),
            };
            print!("{out}");
        }
        SettingsCommand::Set(SettingsSet::Theme { name }) => {
            if name.trim().is_empty() {
                bail!("a theme name is required: lot settings set theme <name>");
            }
            let path = lot_core::set_user_theme(&name).context("writing the theme to config")?;
            // Confirm what was written and where, so the change is traceable.
            println!("set theme = {name:?} in {}", path.display());
        }
    }
    Ok(())
}

/// A simple human-readable view of the effective config for `--format=markdown`.
/// The `yaml` form is the machine-readable surface; this is a convenience.
fn render_config_markdown(cfg: &lot_core::EffectiveConfig) -> String {
    let mut out = String::from("# Effective config\n\n");
    out.push_str(&format!("- vault-path: {}\n", cfg.vault_path));
    out.push_str(&format!(
        "- theme: {}\n",
        cfg.theme.as_deref().unwrap_or("(none)")
    ));
    out.push_str("- keybindings:\n");
    if cfg.keybindings.is_empty() {
        out.push_str("  - (none)\n");
    } else {
        for (action, key) in &cfg.keybindings {
            out.push_str(&format!("  - {action}: {key}\n"));
        }
    }
    out.push_str("- vaults:\n");
    if cfg.vaults.is_empty() {
        out.push_str("  - (none)\n");
    } else {
        for entry in &cfg.vaults {
            match &entry.name {
                Some(name) => out.push_str(&format!("  - {} ({})\n", name, entry.path)),
                None => out.push_str(&format!("  - {}\n", entry.path)),
            }
        }
    }
    out.push_str("- update-types:\n");
    for t in &cfg.update_types {
        out.push_str(&format!(
            "  - {} (takes-body: {}, terminal: {}, built-in: {})\n",
            t.name, t.takes_body, t.terminal, t.built_in
        ));
    }
    out
}

fn run_vault(cmd: VaultCommand) -> Result<()> {
    match cmd {
        VaultCommand::New { path } => {
            let vault = Vault::create(&path).context("creating vault")?;
            // Print the vault path so it can be referenced by scripts.
            println!("{}", vault.path().display());
        }
        VaultCommand::Archive => {
            let vault = open_vault()?;
            // Which statuses count as terminal comes from the effective update
            // types (built-ins plus config-defined ones).
            let types = lot_core::load_update_types().context("resolving update types")?;
            let archived = vault.archive_done_things(&types)?;
            // Print the archived Things' ids so scripts can confirm what went.
            for id in archived {
                println!("{id}");
            }
        }
    }
    Ok(())
}

/// Resolve the vault settings (honouring `LOT_VAULT_PATH`, else config —
/// creating it on first run) and open the vault (initialising it on first
/// run), honouring the `vault.auto-commit` setting.
fn open_vault() -> Result<Vault> {
    let settings = lot_core::resolve_vault_settings().context("resolving vault settings")?;
    let vault = Vault::open_with(settings.path, settings.auto_commit).context("opening vault")?;
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
        ThingCommand::Updates(ThingRef { thing }) => {
            let thing = resolve_thing(thing)?;
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            let out = render::thing_updates_yaml(&found)?;
            print!("{out}");
        }
        ThingCommand::Archive(ThingRef { thing }) => {
            let thing = resolve_thing(thing)?;
            let vault = open_vault()?;
            let archived = vault.archive_thing(&thing)?;
            // Print the archived Thing's id so scripts can confirm what went.
            println!("{archived}");
        }
        ThingCommand::Move(cli::MoveArgs {
            thing,
            parent,
            // Clap's `destination` group guarantees exactly one of
            // `--parent`/`--root`, so "no parent" can only mean `--root`.
            root: _,
        }) => {
            let thing = resolve_thing(thing)?;
            let vault = open_vault()?;
            let moved = vault.move_thing(&thing, parent.as_deref())?;
            // Print the moved Thing's id so scripts can confirm what moved.
            println!("{moved}");
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
    // `done` — and any custom type with `takes-body = false` — is a bare
    // marker: it never carries a body, so it skips the content-resolution
    // (and editor) flow entirely.
    let (kind, args) = match cmd {
        UpdateCommand::Work(a) => (UpdateKind::Work, a),
        UpdateCommand::Info(a) => (UpdateKind::Info, a),
        UpdateCommand::Done(ThingFlag { thing }) => {
            let thing = resolve_thing(thing)?;
            return write_update(UpdateKind::Done, &thing, "");
        }
        UpdateCommand::Path(UpdateRef { update }) => {
            let vault = open_vault()?;
            let path = vault.find_update_path(&update)?;
            println!("{}", path.display());
            return Ok(());
        }
        UpdateCommand::Custom(mut argv) => {
            // The external-subcommand fallback: argv[0] is the sub-command
            // name, the rest its raw arguments. Resolving the name against
            // the effective update types (built-ins plus config-defined ones)
            // lives in lot-core; an unknown name errors there with the list
            // of known types.
            let name = argv.remove(0);
            let types = lot_core::load_update_types().context("resolving update types")?;
            let kind = types.resolve(&name)?;
            if kind.allows_body() {
                // Body-bearing custom types take exactly the arguments of
                // `work`/`info` and share their stdin/`--`/editor handling.
                let args: UpdateArgs = parse_custom_update_args(&name, argv);
                (kind, args)
            } else {
                // Bare-marker custom types take exactly the arguments of
                // `done`; supplied content is rejected by the parser.
                let ThingFlag { thing } = parse_custom_update_args(&name, argv);
                let thing = resolve_thing(thing)?;
                return write_update(kind, &thing, "");
            }
        }
    };

    let thing = resolve_thing(args.thing.clone())?;
    let content = match resolve_content(args, &kind)? {
        Some(content) => content,
        None => {
            // The editor was opened and left unchanged: create nothing.
            eprintln!("aborted: editor saved an empty update; nothing created");
            return Ok(());
        }
    };
    write_update(kind, &thing, &content)
}

/// Parse the raw arguments captured by the `lot update` external-subcommand
/// fallback against an [`clap::Args`] shape (`UpdateArgs` for body-bearing
/// types, `ThingFlag` for bare markers), so custom types get exactly the
/// argument handling — and error/help output — of their built-in equivalents.
///
/// Parse failures print clap's usual message (with a `lot update <type>`
/// usage line) and exit, matching how the static sub-commands behave.
fn parse_custom_update_args<T: clap::FromArgMatches + clap::Args>(
    type_name: &str,
    argv: Vec<String>,
) -> T {
    let cmd =
        T::augment_args(clap::Command::new(format!("lot update {type_name}"))).no_binary_name(true);
    let matches = cmd.try_get_matches_from(argv).unwrap_or_else(|e| e.exit());
    T::from_arg_matches(&matches).unwrap_or_else(|e| e.exit())
}

/// Add an update to `thing` and print its `update-id` so the new Update can be
/// referenced by scripts.
fn write_update(kind: UpdateKind, thing: &str, content: &str) -> Result<()> {
    let vault = open_vault()?;
    let update_id = vault.add_update(thing, kind, content)?;
    println!("{update_id}");
    Ok(())
}

/// Resolve the body for a content-bearing update.
///
/// Content may be supplied after `--` or piped on stdin (it is an error to give
/// both). When neither is present and stdin is an interactive terminal, the
/// user's editor is opened on a seeded template (see
/// [`compose_update_via_editor`]).
///
/// `Ok(None)` means the editor was opened and left unchanged — a cancellation.
/// A non-interactive invocation with no content yields an empty body, which
/// preserves the previous behaviour for scripts (e.g. `lot update work < /dev/null`).
fn resolve_content(args: UpdateArgs, kind: &UpdateKind) -> Result<Option<String>> {
    let arg_content = args.content.join(" ");
    let arg_present = !arg_content.trim().is_empty();
    let stdin_content = read_stdin();

    match (arg_present, stdin_content) {
        (true, Some(_)) => bail!(lot_core::Error::AmbiguousContent),
        (true, None) => Ok(Some(arg_content)),
        (false, Some(s)) => Ok(Some(s)),
        // No inline content: compose in the editor when interactive, otherwise
        // (e.g. an empty pipe) fall back to an empty body.
        (false, None) => {
            if std::io::stdin().is_terminal() {
                compose_update_via_editor(kind)
            } else {
                Ok(Some(String::new()))
            }
        }
    }
}

/// Open the editor on a temp file seeded with a preview of the update being
/// composed (its type and timestamp) and return the body the user wrote.
///
/// Returns `Ok(None)` when the user saves without adding a body — i.e. leaves
/// the seeded template unchanged — which the caller treats as a cancellation.
fn compose_update_via_editor(kind: &UpdateKind) -> Result<Option<String>> {
    let saved = edit_temp_file(&update_editor_template(kind))?;
    Ok(strip_update_template(&saved))
}

/// Seed text for the editor when composing an update with no inline content.
///
/// The two `<!-- ... -->` lines preview the update's type and timestamp and say
/// how to cancel; both are stripped on save (see [`strip_update_template`]). The
/// trailing blank line is where the body goes.
fn update_editor_template(kind: &UpdateKind) -> String {
    format!(
        concat!(
            "<!-- {status} update — {timestamp} -->\n",
            "<!-- Write the update body below; leave it blank to cancel. -->\n",
            "\n",
        ),
        status = kind.status(),
        timestamp = chrono::Utc::now().to_rfc3339(),
    )
}

/// Strip the [`update_editor_template`] hint comments from a saved buffer,
/// returning the body the user wrote.
///
/// Returns `None` when nothing but blank lines remains once the one-line
/// `<!-- ... -->` comments are removed — i.e. the template was left unchanged —
/// which the caller treats as a cancellation.
fn strip_update_template(buf: &str) -> Option<String> {
    let body = buf
        .lines()
        .filter(|line| {
            let t = line.trim();
            !(t.starts_with("<!--") && t.ends_with("-->"))
        })
        .collect::<Vec<_>>()
        .join("\n");
    let body = body.trim();
    (!body.is_empty()).then(|| body.to_string())
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
    let mut command = ProcessCommand::new(program);
    command.args(parts).arg(&tmp);
    // Point the editor's display at the controlling terminal directly, rather
    // than at our own stdout. The editor's UI then renders correctly even when
    // our stdout is captured (e.g. by the TUI, which reads it to detect the
    // printed id) or piped (`lot thing new | cat`), and that captured/piped
    // stdout carries only the id we print, not the editor's escape codes. With
    // no controlling terminal we fall back to inheriting our stdio.
    if let Ok(tty) = std::fs::OpenOptions::new()
        .read(true)
        .write(true)
        .open("/dev/tty")
    {
        if let Ok(tty_err) = tty.try_clone() {
            command.stdout(tty).stderr(tty_err);
        }
    }
    let status = command
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

/// Build the display name for a background Claude session.
///
/// The name prefixes the Thing's `title` with the vault's name in square
/// brackets — `[wavelet] Buy milk` — so sessions from different vaults are
/// distinguishable in `claude agents` and other listings. A vault's name is
/// the name of the directory that *contains* the vault, e.g. the vault at
/// `/Users/logaan/code/personal/rust/wavelet/.lot-vault` is named `wavelet`.
///
/// If the containing directory can't be determined (the vault path has no
/// usable parent, e.g. a bare root), the title is returned unprefixed.
fn session_name(vault_path: &std::path::Path, title: &str) -> String {
    match vault_path
        .parent()
        .and_then(|p| p.file_name())
        .and_then(|n| n.to_str())
    {
        Some(vault) if !vault.is_empty() => format!("[{vault}] {title}"),
        _ => title.to_string(),
    }
}

/// Compose the `work` update body recorded when a background Claude session is
/// launched via `lot claude send`. It notes the model and folds in whatever the
/// `claude --bg` launch printed (its session/job reference) so the session can
/// be located from the Thing's history.
fn format_send_update(model_flag: &str, stdout: &str, stderr: &str) -> String {
    let mut body = format!("Launched a background Claude session (model: {model_flag}).");
    let launch_output: String = [stdout, stderr]
        .iter()
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .collect::<Vec<_>>()
        .join("\n");
    if !launch_output.is_empty() {
        // Fence the captured output as a `text` code block so it renders
        // verbatim (its box-drawing/indentation survives Markdown) wherever the
        // Thing's history is displayed.
        body.push_str("\n\nLaunch output:\n\n```text\n");
        body.push_str(&launch_output);
        body.push_str("\n```");
    }
    body
}

/// Commit any uncommitted changes in the git work tree containing the current
/// working directory, so a background agent launched from here that branches a
/// fresh worktree picks them up (readme §5.3.2).
///
/// This targets the *code* repo the spawned `claude` inherits as its CWD, not
/// the vault (the vault already commits every update as it is written). If the
/// working directory is not inside a git repo, or the tree is already clean,
/// there is nothing to do. A failed commit is fatal: proceeding would send the
/// agent to work from a tree that silently omits the caller's latest changes,
/// which is exactly what this guards against.
fn commit_working_tree_before_send() -> Result<()> {
    let cwd = std::env::current_dir().context("failed to determine working directory")?;
    let Some(root) = lot_core::git::work_tree_root(&cwd) else {
        return Ok(());
    };
    if lot_core::git::has_changes(&root, std::path::Path::new("."))? {
        lot_core::git::commit_all(&root, "Commit before sending to Claude")
            .context("failed to commit working-tree changes before sending to Claude")?;
        println!(
            "Committed working-tree changes in {} before sending.",
            root.display()
        );
    }
    Ok(())
}

fn run_claude(cmd: ClaudeCommand) -> Result<()> {
    match cmd {
        ClaudeCommand::Install => {
            let written = skills::install()?;
            for path in written {
                println!("installed {}", path.display());
            }
        }
        ClaudeCommand::Send(model) => {
            // `send` always selects a model; the `--model` value is passed
            // through to `claude`. `resolve_thing` still falls back to
            // `LOT_THING_ID` when no id is given on the command line.
            let model_flag = model.flag();
            let thing = resolve_thing(model.thing())?;
            // Validate the Thing exists before spawning Claude.
            let vault = open_vault()?;
            let found = vault.find_thing(&thing)?;
            let id = found.id()?;
            let title = found.title()?;
            // Prefix the session's display name with the vault's name so
            // sessions from different vaults are distinguishable in listings.
            let session_name = session_name(vault.path(), &title);

            // Commit any uncommitted changes in the working directory's repo
            // before launching. The background `claude` inherits this CWD and,
            // per the project workflow, branches a fresh worktree from the
            // committed tip — so anything left uncommitted here would be
            // invisible to it. Committing first hands the agent the current
            // state of the code.
            commit_working_tree_before_send()?;

            let prompt = format!("/{} {}", skills::LOT_TASK_SKILL_NAME, id);
            // Start a background Claude session that loads the lot-task skill.
            // The session's context goes in the environment — the same contract
            // the TUI uses for every `lot` invocation — so `lot` commands in the
            // receiving session hit this vault regardless of their working
            // directory.
            //
            // Capture the launch output rather than letting it inherit the
            // terminal: `claude --bg` prints where the background session went
            // (its job/session reference), which we both echo back to the caller
            // and record on the Thing as a `work` update so the launch is
            // traceable from the Thing's own history.
            // Name the session after the Thing (prefixed with the vault name)
            // so it's recognisable in `claude agents` and other session
            // listings.
            let output = ProcessCommand::new("claude")
                .arg("--bg")
                .arg("--model")
                .arg(model_flag)
                .arg("--name")
                .arg(&session_name)
                .arg(&prompt)
                .env(lot_core::env::VAULT_PATH, vault.path())
                .env(lot_core::env::AUTO_COMMIT, vault.auto_commit().to_string())
                .env(lot_core::env::THING_ID, &id)
                .output()
                .context("failed to launch `claude`; is it installed and on PATH?")?;

            let stdout = String::from_utf8_lossy(&output.stdout);
            let stderr = String::from_utf8_lossy(&output.stderr);
            // Echo the launch output straight through so the caller still sees
            // it, exactly as they did when it inherited the terminal.
            print!("{stdout}");
            eprint!("{stderr}");

            if !output.status.success() {
                bail!("`claude` exited with status {}", output.status);
            }

            // Record the launch on the Thing. The body carries the model and
            // the captured launch output so the session can be found later.
            let body = format_send_update(model_flag, &stdout, &stderr);
            vault.add_update(&id, UpdateKind::Work, &body)?;
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
    fn send_update_notes_model_and_folds_in_launch_output() {
        let body = format_send_update("opus", "session lot-bg-123\n", "");
        assert!(body.contains("model: opus"));
        assert!(body.contains("Launch output:"));
        assert!(body.contains("session lot-bg-123"));
        // The captured output is fenced as a `text` code block.
        assert!(body.contains("```text\nsession lot-bg-123\n```"));
        // Trailing whitespace from the captured stream is trimmed (the body
        // ends with the closing fence).
        assert!(body.ends_with("```"));
    }

    #[test]
    fn send_update_merges_stdout_and_stderr() {
        let body = format_send_update("sonnet", "out line\n", "warn line\n");
        assert!(body.contains("out line"));
        assert!(body.contains("warn line"));
    }

    #[test]
    fn send_update_omits_output_section_when_empty() {
        // No launch output (both streams blank) -> just the one-line summary.
        let body = format_send_update("fable", "   ", "\n");
        assert!(body.contains("model: fable"));
        assert!(!body.contains("Launch output:"));
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

    #[test]
    fn update_template_previews_type_and_timestamp() {
        // The seed shows the update's type and a timestamp inside hint comments,
        // and ends with a blank body line for the user to type on.
        let seed = update_editor_template(&UpdateKind::Work);
        assert!(seed.starts_with("<!-- work update — "));
        assert!(seed.contains("leave it blank to cancel"));
        // The timestamp is an RFC 3339 instant (so it carries the year).
        assert!(seed.contains("T") && seed.contains("+00:00"));
        assert!(seed.ends_with("\n\n"));
        // Its own hint comments round-trip to a cancellation.
        assert!(strip_update_template(&seed).is_none());
    }

    #[test]
    fn strip_unchanged_template_is_a_cancellation() {
        // Leaving the template (only hint comments + blank lines) unchanged
        // cancels, as does a wholly empty or whitespace-only file.
        assert!(strip_update_template(&update_editor_template(&UpdateKind::Info)).is_none());
        assert!(strip_update_template("<!-- info update — t -->\n\n").is_none());
        assert!(strip_update_template("").is_none());
        assert!(strip_update_template("   \n\t\n").is_none());
    }

    #[test]
    fn strip_keeps_the_body_below_the_hints() {
        // The hint comments are removed; the typed body (with its internal
        // blank lines) survives, surrounding blanks trimmed.
        let saved = "<!-- work update — t -->\n\
                     <!-- hint -->\n\
                     \n\
                     First line\n\
                     \n\
                     Second line\n";
        assert_eq!(
            strip_update_template(saved).as_deref(),
            Some("First line\n\nSecond line")
        );
    }

    #[test]
    fn session_name_prefixes_with_vault_directory() {
        // The vault's name is the directory that *contains* the vault dir.
        assert_eq!(
            session_name(
                std::path::Path::new("/Users/logaan/code/personal/rust/wavelet/.lot-vault"),
                "Buy milk"
            ),
            "[wavelet] Buy milk"
        );
        // A plainly-named vault directory works the same way.
        assert_eq!(
            session_name(
                std::path::Path::new("/home/me/projects/lot-vault"),
                "Ship it"
            ),
            "[projects] Ship it"
        );
    }

    #[test]
    fn session_name_falls_back_to_bare_title_without_a_parent() {
        // A vault path with no usable containing directory leaves the title
        // unprefixed rather than emitting an empty `[] ` prefix.
        assert_eq!(session_name(std::path::Path::new("/"), "Lonely"), "Lonely");
        assert_eq!(
            session_name(std::path::Path::new(""), "Nameless"),
            "Nameless"
        );
    }
}
