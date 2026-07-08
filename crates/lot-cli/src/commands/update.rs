//! `lot update`: add typed Updates to a Thing, resolving the config-defined
//! update types and the stdin/`--`/editor content flow.

use crate::cli::{ThingFlag, UpdateArgs, UpdateCommand, UpdateRef};
use crate::context::{open_vault, resolve_thing};
use crate::editor::{edit_temp_file, read_stdin};
use anyhow::{bail, Context, Result};
use lot_core::UpdateType;
use std::io::IsTerminal;

pub(crate) fn run(cmd: UpdateCommand) -> Result<()> {
    let argv = match cmd {
        UpdateCommand::Path(UpdateRef { update }) => {
            let vault = open_vault()?;
            let path = vault.find_update_path(&update)?;
            println!("{}", path.display());
            return Ok(());
        }
        UpdateCommand::Type(argv) => argv,
    };

    // The external-subcommand fallback: argv[0] is the sub-command name, the
    // rest its raw arguments. Every update type — stock and custom alike —
    // arrives here and is resolved against the effective (config-defined)
    // update types in lot-core; an unknown name errors there with the list
    // of known types.
    let mut argv = argv;
    let name = argv.remove(0);
    let types = lot_core::load_update_types().context("resolving update types")?;
    let kind = types.resolve(&name)?;
    if !kind.takes_body {
        // A bare-marker type (`takes-body = false`, like the stock `done`)
        // never carries a body, so it skips the content-resolution (and
        // editor) flow entirely; supplied content is rejected by the parser.
        let ThingFlag { thing } = parse_update_type_args(&name, argv);
        let thing = resolve_thing(thing)?;
        return write_update(&kind, &thing, "");
    }

    // Body-bearing types share the stdin/`--`/editor content handling.
    let args: UpdateArgs = parse_update_type_args(&name, argv);
    let thing = resolve_thing(args.thing.clone())?;
    let content = match resolve_content(args, &kind)? {
        Some(content) => content,
        None => {
            // The editor was opened and left unchanged: create nothing.
            eprintln!("aborted: editor saved an empty update; nothing created");
            return Ok(());
        }
    };
    write_update(&kind, &thing, &content)
}

/// Parse the raw arguments captured by the `lot update` external-subcommand
/// fallback against an [`clap::Args`] shape (`UpdateArgs` for body-bearing
/// types, `ThingFlag` for bare markers), so every type gets uniform argument
/// handling — and error/help output.
///
/// Parse failures print clap's usual message (with a `lot update <type>`
/// usage line) and exit, matching how static sub-commands behave.
fn parse_update_type_args<T: clap::FromArgMatches + clap::Args>(
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
fn write_update(kind: &UpdateType, thing: &str, content: &str) -> Result<()> {
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
fn resolve_content(args: UpdateArgs, kind: &UpdateType) -> Result<Option<String>> {
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
fn compose_update_via_editor(kind: &UpdateType) -> Result<Option<String>> {
    let saved = edit_temp_file(&update_editor_template(kind))?;
    Ok(strip_update_template(&saved))
}

/// Seed text for the editor when composing an update with no inline content.
///
/// The two `<!-- ... -->` lines preview the update's type and timestamp and say
/// how to cancel; both are stripped on save (see [`strip_update_template`]). The
/// trailing blank line is where the body goes.
fn update_editor_template(kind: &UpdateType) -> String {
    format!(
        concat!(
            "<!-- {status} update — {timestamp} -->\n",
            "<!-- Write the update body below; leave it blank to cancel. -->\n",
            "\n",
        ),
        status = kind.name,
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

#[cfg(test)]
mod tests {
    use super::*;

    /// A stock update type by name, for tests that need a concrete kind.
    fn stock_type(name: &str) -> UpdateType {
        lot_core::default_update_types()
            .into_iter()
            .find(|t| t.name == name)
            .expect("a stock update type")
    }

    #[test]
    fn update_template_previews_type_and_timestamp() {
        // The seed shows the update's type and a timestamp inside hint comments,
        // and ends with a blank body line for the user to type on.
        let seed = update_editor_template(&stock_type("work"));
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
        assert!(strip_update_template(&update_editor_template(&stock_type("info"))).is_none());
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
}
