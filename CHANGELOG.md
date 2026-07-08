# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). On release, the
`## [Unreleased]` section is rolled into a new version section by
`scripts/release` (or the Prepare release workflow), and that section becomes
the body of the GitHub Release.

## [Unreleased]

### Added

- Three bundled **coordinator** skills for driving a root Thing's subtree of
  child Things across worker sessions: `lot-coordinate-decide` (*Decide, Plan,
  Initiate* — decompose into a Decisions + Steps subtree, post the plan, then
  hand back to the human for sign-off), `lot-coordinate-plan` (*Plan, Act* —
  fully autonomous decompose-and-execute), and `lot-coordinate-act` (*Act with
  existing plan* — execute a pre-built child plan without re-decomposing). Each
  teaches per-child model selection via the `claude-model` preamble field,
  launching children with `lot claude send`, monitoring with
  `lot watch --thing`, treating a child's `info` status as step-complete, and
  deferring code integration to the host project's own workflow docs. (Skill
  content only; the `lot claude coordinate` command and skill embedding/install
  wiring land separately.)
- `lot thing new` and `lot update` accept `--preamble <yaml>`: a small YAML
  mapping merged into the update's frontmatter, so it folds into the Thing's
  computed-state preamble like any managed field. This is how a coordinator
  flags a child task's model — `--preamble 'claude-model: opus'` — and reads it
  back from `lot thing get`. Keys `lot` manages (`status`, `task-id`,
  `update-id`, and `<type>-at`) are rejected, and the newest update's value
  wins.
- The Textual UI can now toggle how its tree columns are sorted. `s` cycles
  through three orders: **status** (grouped by the vault's configured update
  types — the new default), **recent activity** (most recently updated subtree
  first, folding in every descendant's updates), and **name** (alphabetical).
  The order is view-only and in-memory — it never reorders anything on disk and
  resets to the status grouping each launch.
- `lot thing list --format=yaml` now includes an `updated` timestamp per Thing
  (its most-recent-update time), so a front-end can sort by recency without
  re-reading every Thing's thread.
- The Textual UI can now mark a whole sibling group in one keystroke: `X`
  (`toggle_mark_siblings`, also in the `ctrl+p` palette as "Toggle mark on
  siblings") marks the highlighted Thing and every Thing sharing its parent —
  its fellow roots when it is itself a root — and unmarks the whole group when
  they are already marked. It complements the existing `x` single-row toggle
  and is remappable like every other action.
- The New Thing form now has a **Create and send** button (mnemonic `ctrl+t`)
  alongside **Create**. It creates the Thing and then opens the command
  navigator at the `claude` command on the new Thing, so you can hand it
  straight to Claude without a separate step.
- Added an MIT `LICENSE` file, matching the `license = "MIT"` metadata already
  declared in `Cargo.toml`.

### Fixed

- Clicking a `lot:` link in a detail-pane update body now navigates to that
  Thing (or the Thing owning that update) *inside* the UI instead of opening
  the raw `lot:` URI in a web browser. Textual's `Markdown` widget opens every
  link itself by default, and its handler fired before the pane's, so each
  in-vault link both launched a browser and navigated; the body Markdown
  widgets now run with `open_links=False` and the detail pane routes every
  link — `lot:` in-app, other schemes (`https:`, …) to the browser as before.

- The Textual UI now keeps a Thing's colour-coded status visible on the
  selected row. Previously the focused tree's cursor styling was painted over
  the whole leading `mark`/`status` column, so the status word on the selected
  Thing lost its type colour (and the default `note` even rendered blue-on-blue
  against the block cursor) — most noticeable right after creating a Thing,
  which jumps the selection onto it. The cursor/hover highlight now applies to
  the name only; the status chip keeps its own colour in every state.

- The Textual UI no longer crashes to a raw Python stack trace when something
  goes wrong. A failed initial load (a missing/older `lot`, a broken vault, or
  malformed output) now brings the app up empty with a clear toast instead of
  crashing on launch; background workers (the live-update watcher, detail-pane
  loads, vault switching, post-action reloads) surface CLI and parse failures
  as error toasts and keep running; and any remaining unexpected exception is
  caught by a global backstop that shows a short, reassuring message and writes
  the full traceback to a crash-log file rather than dumping it over the
  terminal.

- Modal dialog buttons now use a consistent, edit-safe `ctrl+<letter>` shortcut
  scheme. **Cancel** is always `ctrl+n`, and each dialog's primary/confirm
  action (Create `ctrl+r`, Add `ctrl+d`, Archive/Delete `ctrl+r`/`ctrl+d`) is
  assigned so that no dialog's submit/confirm chord can ever equal another
  dialog's Cancel chord — you can no longer fire a destructive Archive with a
  chord that means Cancel on a different screen. Button shortcuts also no longer
  shadow the terminal's text-editing keys (`ctrl+a/u/k/w/x/v/y`).
- The footer's `y` / `Y` "copy Thing URI / path" hints are now hidden (and the
  keys disabled) unless the updates column holds focus, matching the fold (`z`)
  and copy-selection (`c`) hints. All contextual hints now appear together only
  when the updates column is active.

## [0.1.2] - 2026-07-09

### Added

- This changelog. Every user-visible change now gets an entry under
  `## [Unreleased]`, and each release's section is published as the body of
  its GitHub Release, followed by install instructions.
- `scripts/release --yes <patch|minor|major|X.Y.Z>` runs the whole release
  flow non-interactively, with every confirmation assumed yes.
- Releases now also ship the Python Textual UI as an sdist release asset
  (`lot_textual_ui-X.Y.Z.tar.gz` plus a `.sha256` checksum).
- Releases now update the Homebrew tap automatically: the new
  `scripts/update-tap` regenerates the tap's formula from a release's
  published checksums, and `scripts/release` runs it once the release assets
  are up.
- The readme now opens with an animated demo of the terminal interface,
  including the send-to-Claude flow (regenerate it with `docs/demo/generate`).

### Fixed

- Installing with Homebrew (`brew install logaan/tap/lot`) now includes the
  Textual UI, so `lot interface` and `lot web` work instead of failing to
  find `lot-textual-ui`.

## [0.1.1] - 2026-07-09

### Changed

- Update types are now defined entirely by the vault's config; the built-in
  stock fallbacks are gone, and the stock set is only a seed for new vaults.
- Seeded config files now carry comprehensive, example-first comments.
- `LOT_VAULT_PATH` now pins only the vault path; user config under it (for
  example auto-commit) is honoured again.
- Textual UI: "Update marked" now picks the update type before opening the
  form, matching `ctrl+u`.

### Fixed

- Dropped a duplicate `settings set theme` entry from the command palette.

## [0.1.0] - 2026-07-09

Initial release.

### Added

- The `lot` CLI: git-backed vaults of Things (folders) and append-only
  markdown Updates, with statuses folded from each Thing's update history.
- Update types defined in vault config (seeded with `note`, `work`, `info`,
  `done`), automatic git commits for every change, and `lot watch` for live
  change events.
- The Textual UI, launched with `lot interface` or served to browsers with
  `lot web`.
- Prebuilt binaries for macOS, Linux, and Windows, published by the release
  workflow and installable via the `logaan/tap` Homebrew tap.

[Unreleased]: https://github.com/logaan/lot.rs/compare/v0.1.2...HEAD
[0.1.2]: https://github.com/logaan/lot.rs/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/logaan/lot.rs/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/logaan/lot.rs/releases/tag/v0.1.0
