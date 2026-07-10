//! Domain content for the Claude integration: the vault-persisted
//! representation of Claude-session events.
//!
//! `lot claude send` records each launch on the Thing as an update; the body
//! composed here is what gets written to (and later rendered from) the vault,
//! so its shape lives in the core rather than in any one front-end.

use crate::thing::Thing;

/// The title a `decide` coordinator gives the launchable coordination
/// artifact Thing it authors alongside the Decisions and Steps subtrees. The
/// human executes the plan by `lot claude send`-ing this child; its body
/// references the `lot-coordinate-begin` skill plus the task's specifics.
pub const COORDINATION_ARTIFACT_TITLE: &str = "Update plan and begin coordination";

/// The shape a `decide` coordinator leaves behind on a root Thing, as
/// detected by [`detect_coordination_plan`].
pub struct CoordinationPlan {
    /// The id of the root's [`COORDINATION_ARTIFACT_TITLE`] child, when the
    /// plan has one to point at.
    pub artifact_id: Option<String>,
}

/// Detect whether `thing` looks like a coordination *root* rather than a
/// plain worker task: it has both a "Decisions" and a "Steps" child subtree.
/// `lot claude send` uses this to warn that a plain worker session is being
/// dispatched onto a plan meant for the coordinator flow. Best-effort — any
/// unreadable child is skipped, and an unreadable Thing is just "no plan".
pub fn detect_coordination_plan(thing: &Thing) -> Option<CoordinationPlan> {
    let children = thing.children().ok()?;
    let mut has_decisions = false;
    let mut has_steps = false;
    let mut artifact_id = None;
    for child in children {
        let Ok(title) = child.title() else { continue };
        if title.eq_ignore_ascii_case("decisions") {
            has_decisions = true;
        } else if title.eq_ignore_ascii_case("steps") {
            has_steps = true;
        } else if title.eq_ignore_ascii_case(COORDINATION_ARTIFACT_TITLE) {
            artifact_id = child.id().ok();
        }
    }
    (has_decisions && has_steps).then_some(CoordinationPlan { artifact_id })
}

/// Compose the `work` update body recorded when a background Claude session is
/// launched via `lot claude send`. It notes the model and folds in whatever the
/// `claude --bg` launch printed (its session/job reference) so the session can
/// be located from the Thing's history.
pub fn format_send_update(model_flag: &str, stdout: &str, stderr: &str) -> String {
    format_launch_update("session", model_flag, stdout, stderr)
}

/// Compose the launch `work` update body for either kind of background session
/// `lot claude` starts: a plain worker `session` (`lot claude send`) or a
/// `coordinator session` (`lot claude coordinate`). `kind` is the noun woven
/// into the summary line so a Thing's history says which it launched; the rest
/// (model plus the fenced `claude --bg` launch output) is identical.
pub fn format_launch_update(kind: &str, model_flag: &str, stdout: &str, stderr: &str) -> String {
    let mut body = format!("Launched a background Claude {kind} (model: {model_flag}).");
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
    fn launch_update_names_the_session_kind() {
        // `send` launches a plain "session"; `coordinate` a "coordinator
        // session" — the noun comes from `kind`, the rest is identical.
        let body = format_launch_update("coordinator session", "opus", "job 7\n", "");
        assert!(body.contains("Launched a background Claude coordinator session (model: opus)"));
        assert!(body.contains("job 7"));
        // The `send` wrapper keeps its original wording.
        let body = format_send_update("sonnet", "", "");
        assert!(body.contains("Launched a background Claude session (model: sonnet)"));
    }

    #[test]
    fn send_update_omits_output_section_when_empty() {
        // No launch output (both streams blank) -> just the one-line summary.
        let body = format_send_update("fable", "   ", "\n");
        assert!(body.contains("model: fable"));
        assert!(!body.contains("Launch output:"));
    }
}
