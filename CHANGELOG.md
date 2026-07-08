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

- The New Thing form now has a **Create and send** button (mnemonic `ctrl+t`)
  alongside **Create**. It creates the Thing and then opens the command
  navigator at the `claude` command on the new Thing, so you can hand it
  straight to Claude without a separate step.
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

[Unreleased]: https://github.com/logaan/lot.rs/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/logaan/lot.rs/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/logaan/lot.rs/releases/tag/v0.1.0
