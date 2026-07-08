//! `lot thing`: create, locate, inspect, archive, move, and list Things.

use crate::cli::{self, Format, ThingCommand, ThingRef};
use crate::context::{open_vault, resolve_thing};
use crate::editor::{edit_temp_file, read_stdin};
use anyhow::{bail, Context, Result};
use lot_core::render;
use std::io::IsTerminal;

pub(crate) fn run(cmd: ThingCommand) -> Result<()> {
    match cmd {
        ThingCommand::New {
            editor,
            parent,
            preamble,
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
            // Open the vault *before* resolving the update type: opening
            // auto-initialises a fresh vault, seeding its config with the
            // stock types — the only way any types exist for a brand-new
            // vault, since there is no runtime fallback.
            let vault = open_vault()?;
            // The first update's type is the vault's configured default
            // (`thing.default-update-type`); with none configured this is a
            // hard error rather than a fallback.
            let kind =
                lot_core::load_default_update_type().context("resolving default update type")?;
            // Parse any `--preamble` into the extra frontmatter recorded on the
            // first update (e.g. `claude-model`); reserved keys are rejected.
            let extra = lot_core::update::parse_preamble(preamble.as_deref().unwrap_or_default())
                .context("parsing --preamble")?;
            let thing = match parent {
                Some(parent_id) => vault
                    .new_child_thing_with_preamble(&parent_id, &name, &contents, &kind, &extra)?,
                None => vault.new_thing_with_preamble(&name, &contents, &kind, &extra)?,
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
        ThingCommand::Archive {
            thing: ThingRef { thing },
            force,
        } => {
            let thing = resolve_thing(thing)?;
            let vault = open_vault()?;
            // Update types tell archiving which descendants count as "done";
            // without them nothing is terminal, so every descendant reads as
            // active and the guard fires unless `--force` is passed.
            let types = lot_core::load_update_types().context("resolving update types")?;
            let archived = vault.archive_thing(&thing, &types, force)?;
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

#[cfg(test)]
mod tests {
    use super::*;

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
