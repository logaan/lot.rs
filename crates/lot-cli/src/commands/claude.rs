//! `lot claude`: install the LoT skills and launch background Claude sessions
//! working on a Thing.

use crate::cli::ClaudeCommand;
use crate::context::{apply_vault_env, open_vault, resolve_thing};
use anyhow::{bail, Context, Result};
use lot_core::claude::format_send_update;
use lot_core::skills;
use std::process::Command as ProcessCommand;

pub(crate) fn run(cmd: ClaudeCommand) -> Result<()> {
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
            let mut command = ProcessCommand::new("claude");
            command
                .arg("--bg")
                .arg("--model")
                .arg(model_flag)
                .arg("--name")
                .arg(&session_name)
                .arg(&prompt)
                .env(lot_core::env::THING_ID, &id);
            apply_vault_env(&mut command, &vault);
            let output = command
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
            // The note goes on as a `work` update when the vault configures
            // that type (as the stock set does); a vault with its own type
            // scheme gets its default update type instead.
            let types = lot_core::load_update_types().context("resolving update types")?;
            let kind = match types.resolve("work") {
                Ok(kind) => kind,
                Err(_) => {
                    lot_core::load_default_update_type().context("resolving default update type")?
                }
            };
            let body = format_send_update(model_flag, &stdout, &stderr);
            vault.add_update(&id, &kind, &body)?;
        }
    }
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;

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
