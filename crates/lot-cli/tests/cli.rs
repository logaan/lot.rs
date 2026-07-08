//! Black-box integration tests driving the `lot` binary end-to-end against a
//! throwaway vault: the create -> get -> update -> list happy path.
//!
//! These guard command dispatch and the CLI<->core seam as a whole, which the
//! per-module unit tests (arg parsing, template splitting, ...) cannot. Tests
//! skip themselves when `git` is unavailable, matching the lot-core suite.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

fn git_available() -> bool {
    Command::new("git")
        .arg("--version")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// A throwaway environment for one test: an isolated config home (so the real
/// user config is never read or written) and a vault path inside the tempdir.
struct TestEnv {
    _dir: tempfile::TempDir,
    config_home: PathBuf,
    vault: PathBuf,
}

impl TestEnv {
    fn new() -> TestEnv {
        let dir = tempfile::tempdir().unwrap();
        let config_home = dir.path().join("config");
        let vault = dir.path().join("vault");
        TestEnv {
            _dir: dir,
            config_home,
            vault,
        }
    }

    /// Run `lot <args>` (optionally piping `stdin_body`) and return stdout,
    /// asserting the command succeeded.
    fn lot(&self, args: &[&str], stdin_body: Option<&str>) -> String {
        let mut command = Command::new(env!("CARGO_BIN_EXE_lot"));
        command
            .args(args)
            // Isolate config resolution from the developer's real setup.
            .env("XDG_CONFIG_HOME", &self.config_home)
            .env("LOT_VAULT_PATH", &self.vault)
            // A committer identity must exist for the vault's auto-commits;
            // set it via env so machines/CI with no global git identity work.
            .env("GIT_AUTHOR_NAME", "Test")
            .env("GIT_AUTHOR_EMAIL", "test@example.com")
            .env("GIT_COMMITTER_NAME", "Test")
            .env("GIT_COMMITTER_EMAIL", "test@example.com")
            .current_dir(self._dir.path())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command.spawn().expect("failed to spawn lot");
        if let Some(body) = stdin_body {
            child
                .stdin
                .as_mut()
                .expect("child stdin")
                .write_all(body.as_bytes())
                .expect("writing stdin");
        }
        // Close stdin (empty pipe when no body) so `lot` never waits on it.
        drop(child.stdin.take());
        let out = child.wait_with_output().expect("failed to run lot");
        assert!(
            out.status.success(),
            "lot {args:?} failed: {}",
            String::from_utf8_lossy(&out.stderr)
        );
        String::from_utf8(out.stdout).expect("stdout is not UTF-8")
    }

    /// Spawn `lot <args>` without waiting for it to finish, for commands like
    /// `watch` that block until interrupted.
    fn spawn(&self, args: &[&str]) -> std::process::Child {
        Command::new(env!("CARGO_BIN_EXE_lot"))
            .args(args)
            .env("XDG_CONFIG_HOME", &self.config_home)
            .env("LOT_VAULT_PATH", &self.vault)
            .env("GIT_AUTHOR_NAME", "Test")
            .env("GIT_AUTHOR_EMAIL", "test@example.com")
            .env("GIT_COMMITTER_NAME", "Test")
            .env("GIT_COMMITTER_EMAIL", "test@example.com")
            .current_dir(self._dir.path())
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("failed to spawn lot")
    }
}

#[test]
fn create_get_update_list_happy_path() {
    if !git_available() {
        return;
    }
    let env = TestEnv::new();

    // Create the vault explicitly; the printed path is the vault's location.
    let created = env.lot(&["vault", "new", env.vault.to_str().unwrap()], None);
    assert_eq!(Path::new(created.trim()), env.vault);

    // Create a Thing with a piped body; the printed id is its handle.
    let id = env
        .lot(&["thing", "new", "Buy", "milk"], Some("the oat one\n"))
        .trim()
        .to_string();
    assert!(id.starts_with("lot:"), "unexpected thing id: {id}");

    // `get` returns the computed state: created as a `note`, body included.
    let state = env.lot(&["thing", "get", &id], None);
    assert!(state.contains(&id), "{state}");
    assert!(state.contains("status: note"), "{state}");
    assert!(state.contains("the oat one"), "{state}");

    // Add a `work` update; its printed update-id resolves to a file path.
    let update_id = env
        .lot(&["update", "work", "--thing", &id, "--", "started"], None)
        .trim()
        .to_string();
    assert!(update_id.starts_with("lot:"), "unexpected id: {update_id}");
    let update_path = env.lot(&["update", "path", &update_id], None);
    assert!(Path::new(update_path.trim()).is_file(), "{update_path}");

    // The update advances the Thing's status and lands in its thread.
    let state = env.lot(&["thing", "get", &id], None);
    assert!(state.contains("status: work"), "{state}");
    let updates = env.lot(&["thing", "updates", &id], None);
    assert!(updates.contains(&update_id), "{updates}");
    assert!(updates.contains("type: note"), "{updates}");
    assert!(updates.contains("type: work"), "{updates}");
    assert!(updates.contains("started"), "{updates}");

    // The Thing shows up in the tree with its display name and status.
    let list = env.lot(&["thing", "list"], None);
    assert!(list.contains(&id), "{list}");
    assert!(list.contains("Buy milk"), "{list}");
    assert!(list.contains("status: work"), "{list}");
}

/// A `--preamble` on `thing new` / `update` folds an extra field (e.g.
/// `claude-model`) into the Thing's computed-state preamble, and the latest
/// update's value wins — the mechanism a coordinator uses to flag a child's
/// model. Reserved keys and non-mapping input are rejected.
#[test]
fn preamble_sets_extra_frontmatter_and_rejects_reserved_keys() {
    if !git_available() {
        return;
    }
    let env = TestEnv::new();
    env.lot(&["vault", "new", env.vault.to_str().unwrap()], None);

    // Create a Thing whose first update carries `claude-model: opus`.
    let id = env
        .lot(
            &[
                "thing",
                "new",
                "--preamble",
                "claude-model: opus",
                "Coordinated",
                "step",
            ],
            Some("do it\n"),
        )
        .trim()
        .to_string();

    // The field surfaces in the computed-state preamble.
    let state = env.lot(&["thing", "get", &id], None);
    assert!(state.contains("claude-model: opus"), "{state}");

    // A later update overrides it (shallow-merge, newest wins).
    env.lot(
        &[
            "update",
            "work",
            "--thing",
            &id,
            "--preamble",
            "claude-model: sonnet",
            "--",
            "progress",
        ],
        None,
    );
    let state = env.lot(&["thing", "get", &id], None);
    assert!(state.contains("claude-model: sonnet"), "{state}");
    assert!(!state.contains("claude-model: opus"), "{state}");

    // A reserved key is rejected (the command fails).
    let mut command = Command::new(env!("CARGO_BIN_EXE_lot"));
    command
        .args([
            "update",
            "work",
            "--thing",
            &id,
            "--preamble",
            "status: hax",
        ])
        .env("XDG_CONFIG_HOME", &env.config_home)
        .env("LOT_VAULT_PATH", &env.vault)
        .env("GIT_AUTHOR_NAME", "Test")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
        .current_dir(env._dir.path())
        .stdin(Stdio::null());
    let out = command.output().expect("failed to run lot");
    assert!(!out.status.success(), "reserved preamble key should fail");
    assert!(
        String::from_utf8_lossy(&out.stderr).contains("reserved"),
        "stderr: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}

/// A smoke test that `lot watch` (unscoped) and `lot watch --thing <id>`
/// (subtree-scoped) both parse and start up cleanly. `watch` blocks
/// indefinitely by design, so this doesn't wait for an event — it only checks
/// each process is still alive a moment after starting (rather than having
/// already exited with a parse or resolution error), then kills it.
#[test]
fn watch_starts_unscoped_and_scoped_by_thing() {
    if !git_available() {
        return;
    }
    let env = TestEnv::new();
    env.lot(&["vault", "new", env.vault.to_str().unwrap()], None);
    let id = env
        .lot(&["thing", "new", "Watch", "me"], Some(""))
        .trim()
        .to_string();

    for args in [vec!["watch"], vec!["watch", "--thing", id.as_str()]] {
        let mut child = env.spawn(&args);
        std::thread::sleep(std::time::Duration::from_millis(300));
        match child.try_wait().expect("polling child status") {
            None => {
                child.kill().expect("killing lot watch");
                let _ = child.wait();
            }
            Some(status) => {
                let mut stderr = String::new();
                if let Some(mut s) = child.stderr.take() {
                    use std::io::Read;
                    let _ = s.read_to_string(&mut stderr);
                }
                panic!("lot {args:?} exited early with {status}: {stderr}");
            }
        }
    }
}
