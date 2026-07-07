use crate::error::{Error, Result};
use std::path::{Path, PathBuf};
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
    commit_removals(repo, &[path], message)
}

/// [`commit_removal`] over several paths at once: the deletions of all of
/// `paths` are staged and recorded in a single commit, with the same
/// nothing-on-disk-changes guarantee.
pub fn commit_removals(repo: &Path, paths: &[&Path], message: &str) -> Result<()> {
    let path_strs: Vec<String> = paths
        .iter()
        .map(|p| p.to_string_lossy().into_owned())
        .collect();
    let mut rm_args = vec!["rm", "-r", "-q", "--cached", "--"];
    rm_args.extend(path_strs.iter().map(String::as_str));
    run(repo, &rm_args)?;
    if let Err(err) = run(repo, &["commit", "-m", message]) {
        let mut reset_args = vec!["reset", "-q", "--"];
        reset_args.extend(path_strs.iter().map(String::as_str));
        let _ = run(repo, &reset_args);
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

/// Stage every change under `repo` (tracked edits, untracked additions, and
/// removals, via `git add -A`) and record them in a single commit with
/// `message`. Unlike [`commit`], which stages only named paths, this captures
/// the whole work tree — the "commit everything as-is" a caller wants before
/// handing the repo to a process that will branch a fresh worktree from the
/// committed tip.
pub fn commit_all(repo: &Path, message: &str) -> Result<()> {
    run(repo, &["add", "-A"])?;
    run(repo, &["commit", "-m", message])
}

/// The root of the git work tree containing `dir`, or `None` if `dir` is not
/// inside a git repository. Unlike [`is_repo`], which only answers whether a
/// specific directory *is* a repo root, this walks up from `dir` (via
/// `git rev-parse --show-toplevel`) to find the enclosing work tree, so it
/// works from any subdirectory.
pub fn work_tree_root(dir: &Path) -> Option<PathBuf> {
    let out = run_capture(dir, &["rev-parse", "--show-toplevel"]).ok()?;
    let root = out.trim();
    if root.is_empty() {
        None
    } else {
        Some(PathBuf::from(root))
    }
}

/// Whether `repo` already contains a git repository.
pub fn is_repo(repo: &Path) -> bool {
    repo.join(".git").exists()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn git_available() -> bool {
        Command::new("git")
            .arg("--version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    /// Initialise a fresh repo with a committer identity set via the local
    /// config, so the tests don't depend on (or clobber) a global git identity.
    fn init_repo() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        init(root).unwrap();
        run(root, &["config", "user.name", "Test"]).unwrap();
        run(root, &["config", "user.email", "test@example.com"]).unwrap();
        dir
    }

    #[test]
    fn work_tree_root_finds_enclosing_repo_from_subdirectory() {
        if !git_available() {
            return;
        }
        let dir = init_repo();
        let root = dir.path();
        let sub = root.join("a").join("b");
        std::fs::create_dir_all(&sub).unwrap();
        // From a nested subdirectory we still resolve back to the repo root.
        // Canonicalise both sides: macOS temp dirs go through a `/var ->
        // /private/var` symlink that `--show-toplevel` reports resolved.
        assert_eq!(
            work_tree_root(&sub).map(|p| std::fs::canonicalize(p).unwrap()),
            Some(std::fs::canonicalize(root).unwrap()),
        );
    }

    #[test]
    fn work_tree_root_is_none_outside_a_repo() {
        if !git_available() {
            return;
        }
        // A bare temp dir with no repo above it (tempdir roots are not inside
        // this project's checkout) has no work tree.
        let dir = tempfile::tempdir().unwrap();
        assert_eq!(work_tree_root(dir.path()), None);
    }

    #[test]
    fn commit_all_stages_untracked_edited_and_removed_paths() {
        if !git_available() {
            return;
        }
        let dir = init_repo();
        let root = dir.path();
        // Seed a tracked file so we have something to edit and delete.
        std::fs::write(root.join("keep.txt"), "one\n").unwrap();
        std::fs::write(root.join("gone.txt"), "bye\n").unwrap();
        commit_all(root, "seed").unwrap();

        // Now edit one file, delete another, and add a brand-new one.
        std::fs::write(root.join("keep.txt"), "two\n").unwrap();
        std::fs::remove_file(root.join("gone.txt")).unwrap();
        std::fs::write(root.join("new.txt"), "hi\n").unwrap();
        assert!(has_changes(root, Path::new(".")).unwrap());

        commit_all(root, "Commit before sending to Claude").unwrap();

        // Every kind of change is captured, leaving a clean tree.
        assert!(!has_changes(root, Path::new(".")).unwrap());
        let log = run_capture(root, &["log", "--oneline"]).unwrap();
        assert!(log.contains("Commit before sending to Claude"));
    }

    #[test]
    fn commit_all_errors_when_there_is_nothing_to_commit() {
        if !git_available() {
            return;
        }
        let dir = init_repo();
        let root = dir.path();
        std::fs::write(root.join("a.txt"), "a\n").unwrap();
        commit_all(root, "seed").unwrap();
        // A second commit with no pending changes fails (git rejects an empty
        // commit); callers guard with `has_changes` before calling.
        assert!(commit_all(root, "nothing").is_err());
    }
}
