//! Pure string/formatting helpers for the vault: path expansion, folder
//! slugs, commit messages, and the created update's body. No I/O — everything
//! here turns values into strings for [`super::Vault`] to write or commit.

use std::path::PathBuf;

/// Expand a leading `~` in a vault path against the user's home directory,
/// matching how configured vault paths are resolved (see
/// [`crate::config::Config::vault_path`]).
pub(super) fn expand_path(path: &str) -> PathBuf {
    PathBuf::from(shellexpand::tilde(path).into_owned())
}

/// Turn a thing's name into a folder-safe slug: runs of whitespace collapse to
/// single underscores. e.g. `"Buy some milk"` -> `"Buy_some_milk"`.
pub(super) fn slugify(name: &str) -> String {
    name.split_whitespace().collect::<Vec<_>>().join("_")
}

/// Build the commit message for a newly created thing. The subject line is
/// `Create thing <name>`, with the name truncated (an ellipsis marking the cut)
/// so the whole subject is at most 50 characters. The thing's id goes on the
/// third line, after a blank line, keeping the subject short and scannable:
///
/// ```text
/// Create thing Buy some milk
///
/// lot:6Ic9Cg6kx0Xk2hQhVz3aBd
/// ```
pub(super) fn create_commit_message(name: &str, id: &str) -> String {
    thing_commit_message("Create thing ", name, id)
}

/// Build a commit message about the thing called `name`: `<prefix><name>` as
/// the subject (the name truncated so the subject stays within 50 characters)
/// with the thing's id on the third line, after a blank line. See
/// [`create_commit_message`] for an example.
pub(super) fn thing_commit_message(prefix: &str, name: &str, id: &str) -> String {
    const MAX_SUBJECT: usize = 50;
    let budget = MAX_SUBJECT.saturating_sub(prefix.chars().count());
    format!("{prefix}{}\n\n{id}", truncate_chars(name, budget))
}

/// Build the single commit message recording a vault-wide archive. The
/// subject counts the things going; the body lists each one — its
/// human-readable name and id — so the commit says exactly what it removed:
///
/// ```text
/// Archive 2 done things
///
/// Buy some milk (lot:6Ic9Cg6kx0Xk2hQhVz3aBd)
/// Old project (lot:0Kj2mn4pq6Rs8tu0vwx2yz)
/// ```
pub(super) fn vault_archive_message(titles: &[String], ids: &[String]) -> String {
    let plural = if titles.len() == 1 { "" } else { "s" };
    let mut message = format!("Archive {} done thing{plural}\n", titles.len());
    for (title, id) in titles.iter().zip(ids) {
        message.push_str(&format!("\n{title} ({id})"));
    }
    message
}

/// Truncate `s` to at most `max` characters (counting Unicode scalar values).
/// When truncation happens the last kept character is replaced with `…` so the
/// result is never longer than `max` and the cut is visible.
fn truncate_chars(s: &str, max: usize) -> String {
    let chars: Vec<char> = s.chars().collect();
    if chars.len() <= max {
        return s.to_string();
    }
    if max == 0 {
        return String::new();
    }
    let mut out: String = chars[..max - 1].iter().collect();
    out.push('…');
    out
}

/// Build the body of the `note` update: the name as an h1 heading, followed
/// by the piped contents (if any).
pub(super) fn created_body(name: &str, contents: &str) -> String {
    let contents = contents.trim();
    if contents.is_empty() {
        format!("# {name}\n")
    } else {
        format!("# {name}\n\n{contents}\n")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn slugify_collapses_whitespace() {
        assert_eq!(slugify("foo bar baz"), "foo_bar_baz");
        assert_eq!(slugify("  spaced   out  "), "spaced_out");
    }

    #[test]
    fn commit_message_short_name_fits_on_one_subject() {
        let msg = create_commit_message("Buy milk", "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        assert_eq!(msg, "Create thing Buy milk\n\nlot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        // Subject (first line) within the 50-char budget.
        assert!(msg.lines().next().unwrap().chars().count() <= 50);
        // Id is on the third line, after a blank second line.
        let lines: Vec<&str> = msg.lines().collect();
        assert_eq!(lines[1], "");
        assert_eq!(lines[2], "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
    }

    #[test]
    fn commit_message_truncates_long_name_to_50_char_subject() {
        let long = "Refactor the entire vault storage layer to support nested things";
        let msg = create_commit_message(long, "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        let subject = msg.lines().next().unwrap();
        assert_eq!(subject.chars().count(), 50);
        assert!(subject.starts_with("Create thing Refactor the entire vault"));
        assert!(subject.ends_with('…'));
    }

    #[test]
    fn thing_commit_message_truncates_like_create() {
        let msg = thing_commit_message("Archive thing ", "Buy milk", "lot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        assert_eq!(msg, "Archive thing Buy milk\n\nlot:6Ic9Cg6kx0Xk2hQhVz3aBd");
        let long = "A very long thing name that will not fit inside a fifty character subject";
        let msg = thing_commit_message("Archive thing ", long, "lot:id");
        let subject = msg.lines().next().unwrap();
        assert_eq!(subject.chars().count(), 50);
        assert!(subject.ends_with('…'));
    }
}
