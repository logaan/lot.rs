use crate::error::{Error, Result};
use std::path::Path;
use std::process::Command;

/// Run a git subcommand inside `repo`, returning its stdout, or an error if it
/// fails.
fn run_capture(repo: &Path, args: &[&str]) -> Result<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo)
        .args(args)
        .output()
        .map_err(|e| Error::Git(format!("failed to run git: {e}")))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(Error::Git(format!(
            "`git {}` failed: {}",
            args.join(" "),
            stderr.trim()
        )));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Run a git subcommand inside `repo`, returning an error if it fails.
fn run(repo: &Path, args: &[&str]) -> Result<()> {
    run_capture(repo, args).map(|_| ())
}

/// Initialise a new git repository at `repo`.
pub fn init(repo: &Path) -> Result<()> {
    run(repo, &["init"])
}

/// Stage `paths` (relative to the repo) and create a commit with `message`.
pub fn commit(repo: &Path, paths: &[&Path], message: &str) -> Result<()> {
    let mut add_args = vec!["add", "--"];
    let path_strs: Vec<String> = paths
        .iter()
        .map(|p| p.to_string_lossy().into_owned())
        .collect();
    for p in &path_strs {
        add_args.push(p);
    }
    run(repo, &add_args)?;
    run(repo, &["commit", "-m", message])
}

/// Whether `path` (relative to the repo) has uncommitted changes — staged,
/// unstaged, or untracked.
pub fn has_changes(repo: &Path, path: &Path) -> Result<bool> {
    let path = path.to_string_lossy().into_owned();
    let out = run_capture(repo, &["status", "--porcelain", "--", &path])?;
    Ok(!out.trim().is_empty())
}

/// Commit the removal of `path` (relative to the repo) with `message`, leaving
/// the on-disk files untouched: the deletion is staged with `git rm --cached`
/// and then committed, so the caller only deletes the files from disk once the
/// commit has succeeded. If the commit fails the staged deletion is rolled
/// back (best-effort) and nothing on disk has changed.
pub fn commit_removal(repo: &Path, path: &Path, message: &str) -> Result<()> {
    let path = path.to_string_lossy().into_owned();
    run(repo, &["rm", "-r", "-q", "--cached", "--", &path])?;
    if let Err(err) = run(repo, &["commit", "-m", message]) {
        let _ = run(repo, &["reset", "-q", "--", &path]);
        return Err(err);
    }
    Ok(())
}

/// Commit the move of `from` to `to` (both relative to the repo, already
/// renamed on disk by the caller) with `message`. Staging both paths with
/// `git add -A` records the deletions under `from` and the additions under
/// `to` — the same index state `git mv` produces — so git's rename detection
/// (`git log --follow`) tracks history across the move. If the commit fails
/// the staged changes are rolled back (best-effort) so the caller can undo
/// the on-disk rename and leave the repo as it found it.
pub fn commit_move(repo: &Path, from: &Path, to: &Path, message: &str) -> Result<()> {
    let from = from.to_string_lossy().into_owned();
    let to = to.to_string_lossy().into_owned();
    run(repo, &["add", "-A", "--", &from, &to])?;
    if let Err(err) = run(repo, &["commit", "-m", message]) {
        let _ = run(repo, &["reset", "-q", "--", &from, &to]);
        return Err(err);
    }
    Ok(())
}

/// Whether `repo` already contains a git repository.
pub fn is_repo(repo: &Path) -> bool {
    repo.join(".git").exists()
}
