//! Domain content for the Claude integration: the vault-persisted
//! representation of Claude-session events.
//!
//! `lot claude send` records each launch on the Thing as an update; the body
//! composed here is what gets written to (and later rendered from) the vault,
//! so its shape lives in the core rather than in any one front-end.

/// Compose the `work` update body recorded when a background Claude session is
/// launched via `lot claude send`. It notes the model and folds in whatever the
/// `claude --bg` launch printed (its session/job reference) so the session can
/// be located from the Thing's history.
pub fn format_send_update(model_flag: &str, stdout: &str, stderr: &str) -> String {
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

#[cfg(test)]
mod tests {
    use super::*;

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
}
