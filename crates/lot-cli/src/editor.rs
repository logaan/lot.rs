//! Editor and stdin plumbing shared by `lot thing new` and the update
//! commands: picking the user's editor, round-tripping a temp file through it,
//! and reading piped stdin.

use anyhow::{bail, Context, Result};
use std::io::{IsTerminal, Read};
use std::process::Command as ProcessCommand;

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
pub(crate) fn edit_temp_file(initial: &str) -> Result<String> {
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
    // our stdout is captured (e.g. by the Textual UI, which reads it to detect
    // the printed id) or piped (`lot thing new | cat`), and that captured/piped
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

/// Read stdin if it is piped (not a terminal). Returns `None` when stdin is a
/// terminal so interactive invocations don't block.
pub(crate) fn read_stdin() -> Option<String> {
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
