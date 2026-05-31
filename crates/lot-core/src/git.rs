use crate::error::{Error, Result};
use std::path::Path;
use std::process::Command;

/// Run a git subcommand inside `repo`, returning an error if it fails.
fn run(repo: &Path, args: &[&str]) -> Result<()> {
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
    Ok(())
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

/// Whether `repo` already contains a git repository.
pub fn is_repo(repo: &Path) -> bool {
    repo.join(".git").exists()
}
