//! End-to-end regression tests for `vault.auto-commit` under a
//! `LOT_VAULT_PATH` override.
//!
//! Sessions launched by `lot interface`, `lot web`, and `lot claude send` run
//! their `lot` subprocesses with `LOT_VAULT_PATH` set. The override must win
//! vault-path resolution and nothing else: auto-commit still comes from
//! normal config resolution, so a project's `.lot.toml` with
//! `auto-commit = false` keeps vault writes out of git even inside those
//! sessions (it once didn't — the override used to skip config entirely and
//! hard-code auto-commit to true).

use std::path::Path;
use std::process::{Command, Stdio};

mod common;
use common::{lot_command, with_test_git_identity};

fn git_available() -> bool {
    Command::new("git")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Run `lot thing new <name>` with the user config rooted at `config_home`,
/// the vault path overridden to `vault`, and `cwd` as the working directory
/// (where a project-local `.lot.toml` may sit), asserting success.
fn thing_new(config_home: &Path, vault: &Path, cwd: &Path, name: &str) {
    let mut command = lot_command(config_home, vault);
    let out = with_test_git_identity(&mut command)
        .args(["thing", "new", name])
        .current_dir(cwd)
        .stdin(Stdio::null())
        .output()
        .expect("failed to run lot");
    assert!(
        out.status.success(),
        "lot thing new failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}

#[test]
fn vault_path_override_honours_project_auto_commit_false() {
    // A project `.lot.toml` disables auto-commit; the vault path is overridden
    // (as in every interface/claude-send session). The write must land on disk
    // without `lot` touching git — no repo is initialised for the vault, so
    // nothing can be committed into an enclosing project repo either.
    let home = tempfile::tempdir().unwrap();
    let project = home.path().join("project");
    std::fs::create_dir_all(&project).unwrap();
    std::fs::write(
        project.join(".lot.toml"),
        "[vault]\npath = \"./configured-vault\"\nauto-commit = false\n",
    )
    .unwrap();
    let vault = home.path().join("env-vault");

    thing_new(&home.path().join("config"), &vault, &project, "no-commit");

    assert!(vault.join("no-commit").is_dir(), "thing written to vault");
    assert!(!vault.join(".git").exists(), "vault must not be a git repo");
    // The override won path resolution: the configured vault was never used.
    assert!(!project.join("configured-vault").exists());
}

#[test]
fn vault_path_override_defaults_to_auto_commit_without_config() {
    // With no config anywhere, auto-commit keeps its default of true: the
    // vault gets its own git repo and the write is committed.
    if !git_available() {
        return;
    }
    let home = tempfile::tempdir().unwrap();
    let vault = home.path().join("env-vault");

    thing_new(
        &home.path().join("config"),
        &vault,
        home.path(),
        "committed",
    );

    assert!(vault.join("committed").is_dir(), "thing written to vault");
    assert!(vault.join(".git").exists(), "vault must be a git repo");
}
