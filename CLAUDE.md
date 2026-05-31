# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

LoT (Lists of Things) is a Rust CLI for managing git-backed lists. See `readme.md`
for the full spec — it is the source of truth for intended behavior; keep it in
sync when behavior changes.

## Workspace layout

Two crates (`Cargo.toml` workspace):

- `crates/lot-core` — all domain logic (config, vault, things, updates, git, skills).
- `crates/lot-cli` — the `lot` binary; thin layer over `lot-core`.

`lot-core` must NOT depend on `lot-cli` or contain CLI-specific code. This split is
deliberate so the core can be reused by future TUI/Web/WASM front-ends.

## Commands

- `scripts/run <args>` — run the `lot` CLI from source (e.g. `scripts/run thing list`).
- `scripts/check` — the CI/pre-commit gate: `cargo fmt --check`, `cargo clippy -- -D warnings`, `cargo test`. Run before committing.
- `scripts/lint-autofix` — auto-format and apply clippy fixes.

Clippy runs with warnings as errors (`-D warnings`); a warning fails the gate.

## Gotchas

- Files under `data/` are embedded into the binary at compile time via `include_str!`
  (`config.example.toml`, `new-vault-readme.md`, `skills/lot-task/SKILL.md`). Editing
  them changes program output, and the build will fail if one is renamed/removed.
- Tests that need git skip themselves when `git` is unavailable rather than failing.
