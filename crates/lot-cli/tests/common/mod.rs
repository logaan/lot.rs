//! Shared spawn helpers for the CLI integration tests.
//!
//! Every test that runs the `lot` binary must build its command through
//! [`lot_command`], which scrubs the inherited `LOT_*` environment. Setting
//! only the variables a test cares about is not enough: the child inherits the
//! rest of the developer's environment, and a session started by `lot claude
//! send` carries `LOT_THING_ID` (plus `LOT_VAULT_PATH`) for the *real* vault.
//! That id does not exist in the test's temp vault, so commands resolving a
//! Thing from the environment — `lot watch` — failed for everyone running
//! `scripts/check` from such a session, and read as a regression in whatever
//! branch was under test.

// Each integration test file compiles its own copy of this module and uses
// only the helpers it needs, so unused ones are expected here.
#![allow(dead_code)]

use std::ffi::OsStr;
use std::path::Path;
use std::process::Command;

/// A `lot` command isolated from the developer's environment: no inherited
/// `LOT_*` variables, config resolution rooted at `config_home`, and the vault
/// path overridden to `vault`.
pub fn lot_command(config_home: &Path, vault: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_lot"));
    scrub_lot_vars(&mut command, std::env::vars_os().map(|(key, _)| key));
    command
        .env("XDG_CONFIG_HOME", config_home)
        .env("LOT_VAULT_PATH", vault);
    command
}

/// Remove every `LOT_*` variable in `inherited` from `command`'s environment.
/// Dropping the whole namespace, rather than the variables we know about
/// today, is what stops a future `LOT_*` leaking the same way `LOT_THING_ID`
/// did. Callers set the variables the test does want afterwards.
fn scrub_lot_vars<I, K>(command: &mut Command, inherited: I)
where
    I: IntoIterator<Item = K>,
    K: AsRef<OsStr>,
{
    for key in inherited {
        if key.as_ref().to_string_lossy().starts_with("LOT_") {
            command.env_remove(key.as_ref());
        }
    }
}

/// Give `command` a committer identity, so the vault's auto-commits work on
/// machines and CI runners with no global git identity configured.
pub fn with_test_git_identity(command: &mut Command) -> &mut Command {
    command
        .env("GIT_AUTHOR_NAME", "Test")
        .env("GIT_AUTHOR_EMAIL", "test@example.com")
        .env("GIT_COMMITTER_NAME", "Test")
        .env("GIT_COMMITTER_EMAIL", "test@example.com")
}

/// Scrubbing covers the whole namespace — including a `LOT_*` variable no test
/// knows about — and leaves everything else alone. Driven with an explicit
/// list rather than the process environment so it holds wherever it runs.
#[test]
fn scrub_lot_vars_removes_the_whole_namespace() {
    let mut command = Command::new("true");
    scrub_lot_vars(
        &mut command,
        ["LOT_THING_ID", "LOT_VAULT_PATH", "LOT_FUTURE", "PATH"],
    );

    let mut removed: Vec<_> = command
        .get_envs()
        .filter(|(_, value)| value.is_none())
        .map(|(key, _)| key.to_string_lossy().into_owned())
        .collect();
    removed.sort();
    assert_eq!(removed, ["LOT_FUTURE", "LOT_THING_ID", "LOT_VAULT_PATH"]);
    // `PATH` and friends must survive: `lot` shells out to `git`.
    assert!(command.get_envs().all(|(key, _)| key != "PATH"));
}
