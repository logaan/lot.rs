//! End-to-end regression tests for `lot settings get` under a `LOT_VAULT_PATH`
//! override.
//!
//! Every `lot interface` / `lot web` session runs its `lot` subprocesses with
//! `LOT_VAULT_PATH` set, and the Textual UI reads config exclusively through
//! `settings get` — so if the override suppressed the user-level config (as it
//! once did), the UI silently lost the user's theme, keybindings, and vault
//! list on every launch. The override must win vault-path resolution and
//! nothing else.

use std::path::Path;
use std::process::Command;

/// Run `lot settings get` with the user config rooted at `config_home` and the
/// vault path overridden to `vault`, returning stdout.
fn settings_get(config_home: &Path, vault: &Path, cwd: &Path) -> String {
    let out = Command::new(env!("CARGO_BIN_EXE_lot"))
        .args(["settings", "get"])
        .env("XDG_CONFIG_HOME", config_home)
        .env("LOT_VAULT_PATH", vault)
        .current_dir(cwd)
        .output()
        .expect("failed to run lot");
    assert!(
        out.status.success(),
        "lot settings get failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    String::from_utf8(out.stdout).expect("stdout is not UTF-8")
}

#[test]
fn vault_path_override_keeps_user_level_settings() {
    let home = tempfile::tempdir().unwrap();
    let config_home = home.path().join("config");
    std::fs::create_dir_all(config_home.join("lot")).unwrap();
    std::fs::write(
        config_home.join("lot").join("config.toml"),
        "[vault]\npath = \"~/configured-vault\"\n\n[tui]\ntheme = \"ansi-dark\"\n",
    )
    .unwrap();
    let vault = home.path().join("env-vault");

    let stdout = settings_get(&config_home, &vault, home.path());

    // The user-level theme survives the override…
    assert!(stdout.contains("theme: ansi-dark"), "{stdout}");
    // …while the override still wins the active vault path.
    assert!(
        stdout.contains(&format!("vault-path: {}", vault.display())),
        "{stdout}"
    );
}

#[test]
fn vault_path_override_does_not_create_a_missing_user_config() {
    let home = tempfile::tempdir().unwrap();
    let config_home = home.path().join("config");
    let vault = home.path().join("env-vault");

    let stdout = settings_get(&config_home, &vault, home.path());

    // No user config: defaults apply, and the file is not seeded as a side
    // effect (env-driven invocations stay read-only wrt config files).
    assert!(stdout.contains("theme: null"), "{stdout}");
    assert!(!config_home.join("lot").join("config.toml").exists());
}
