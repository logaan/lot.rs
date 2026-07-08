# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with
code in this repository.

## What this is

LoT (Lists of Things) is a Rust CLI for managing git-backed lists. `readme.md`
is a short user-facing introduction — keep it concise and aimed at new users.
It is not a spec, so don't grow it with every feature's details; leave the
exhaustive behavior to the code, tests, and `lot help`.

## Workspace layout

Two crates (`Cargo.toml` workspace) plus a Python sub-project:

- `crates/lot-core` — all domain logic (config, vault, things, updates, git,
  skills).
- `crates/lot-cli` — the `lot` binary; thin layer over `lot-core`.
- `python/lot-textual-ui` — the Textual UI, **the** interface (launched via
  `lot interface`, served to browsers via `lot web`). A `uv`-managed Python
  app that drives the `lot` CLI; it never links `lot-core` or reads the
  vault's on-disk representation directly.

`lot-core` must NOT depend on `lot-cli` or contain interface-specific code.
This split is deliberate so the core can be reused by the CLI and future
Web/WASM front-ends.

## Commands

- `scripts/run <args>` — run the `lot` CLI from source (e.g.
  `scripts/run thing list`).
- `scripts/check` — the CI/pre-commit gate: `cargo fmt --check`,
  `cargo clippy -- -D warnings`, `cargo test`, plus the Python sub-project's
  `ruff check`, `ruff format --check`, and `pytest` (via `uv`; skipped if `uv`
  is not on PATH). Run before committing.
- `scripts/lint-autofix` — auto-format and apply clippy fixes.

Clippy runs with warnings as errors (`-D warnings`); a warning fails the gate.

## Gotchas

- Files under `data/` are embedded into the binary at compile time via
  `include_str!` (`config.example.toml`, `new-vault-readme.md`,
  `skills/lot-task/SKILL.md`). Editing them changes program output, and the
  build will fail if one is renamed/removed.
- Tests that need git skip themselves when `git` is unavailable rather than
  failing.

## Development workflow

1.  Always work on a work tree unless explicitly told otherwise.
2.  Commit as you work.
3.  Any user-visible change must add an entry under `## [Unreleased]` in
    `CHANGELOG.md` as part of the same branch/PR.
4.  Push the branch once you stop working, whether you're stopping because it's
    complete or for any other reason.
