# Releasing `lot`

Releases are cut from a git tag and built by GitHub Actions. There are three
workflows in `.github/workflows/`:

- **`ci.yml`** — runs on every push to `main` and on pull requests. It mirrors
  `scripts/check`: `cargo fmt --all --check`, `cargo clippy --all-targets -D
  warnings`, and `cargo test`, plus the Python sub-project's checks in
  `python/lot-textual-ui` (`uv run ruff check`, `uv run ruff format --check`,
  and `uv run pytest`).
- **`release.yml`** — runs when a `vX.Y.Z` tag is pushed. It creates a GitHub
  Release for the tag — the body is that version's `changelog.d/` folder,
  rendered by `scripts/changelog render`, followed by install instructions —
  and uploads a binary archive (plus a
  SHA-256 checksum) for each supported target, along with an sdist of the
  Python Textual UI (`lot_textual_ui-X.Y.Z.tar.gz` plus
  `lot_textual_ui-X.Y.Z.sha256`), which the Homebrew formula installs so
  `lot interface` and `lot web` work for brew users.
- **`prepare-release.yml`** — a manually-triggered (`workflow_dispatch`) workflow
  that bumps the version, commits, tags, and pushes for you, so you can cut a
  release from the GitHub UI without touching your machine. See
  [From the GitHub UI](#from-the-github-ui) below for its one-time setup.

After the Release exists, the Homebrew tap needs to point at it —
[`scripts/update-tap`](#updating-the-homebrew-tap) regenerates and pushes the
tap's formula, and `scripts/release` runs it for you.

There are three ways to cut a release, all of which end the same way — a `vX.Y.Z`
tag pushed to `origin`, which fires `release.yml`:

1. [`scripts/release`](#the-interactive-helper) — the interactive local helper (recommended).
2. [From the GitHub UI](#from-the-github-ui) — point and click, no local checkout.
3. [By hand](#by-hand) — the raw git steps.

## The changelog

The changelog lives as a directory tree under `changelog.d/`, not one flat
file, so parallel branches never edit a shared file and never conflict. It
follows the [Keep a Changelog](https://keepachangelog.com) categories:

```
changelog.d/
  unreleased/
    added/    <slug>.md      # one file per entry; filename order is irrelevant
    changed/  <slug>.md
    fixed/    <slug>.md
    ...
  0.1.2 - 2026-07-09/
    added/    ...
    fixed/    ...
```

Every user-visible change must add a file under
`changelog.d/unreleased/<category>/` as part of the branch that makes the
change (this is also a rule in `CLAUDE.md`). `<category>` is one of `added`,
`changed`, `deprecated`, `removed`, `fixed`, or `security`; the file holds the
markdown bullet(s) for that change. A `.md` file placed directly in a version
or `unreleased` folder (not in a category subfolder) renders as leading prose.

`scripts/changelog` drives the tree:

- `scripts/changelog render <X.Y.Z|unreleased>` prints one section's markdown
  (`### Added` / `### Fixed` … blocks).
- `scripts/changelog build` renders the whole tree back into one flat
  Keep-a-Changelog document (with comparison links), for when you want to read
  it end to end.
- `scripts/changelog roll <X.Y.Z>` renames `changelog.d/unreleased/` into a
  `X.Y.Z - <date>/` folder and opens a fresh empty `unreleased/`. If unreleased
  was empty it drops in a `- Maintenance release.` placeholder so the release
  notes are never empty.

At release time the roll happens **in the release commit**, so the tagged tree
already contains the version folder. Both release paths do it:

- `scripts/release` runs `scripts/changelog roll` locally as part of the
  `Release vX.Y.Z` commit. If unreleased is empty it warns and asks before
  continuing (with `--yes` it continues with the placeholder).
- `prepare-release.yml` runs the same roll in its bump step.

`release.yml` then renders the tagged version's folder with
`scripts/changelog render` and uses that as the GitHub Release body, appending
an **Installing** section (Homebrew tap plus the download-and-verify route). If
the tag has no matching `changelog.d/` folder — e.g. a by-hand release that
skipped the roll — the render fails the create-release job, so keep the
changelog rolled.

## Supported targets

| Target | Platform |
| --- | --- |
| `aarch64-apple-darwin` | macOS (Apple Silicon) |
| `x86_64-apple-darwin` | macOS (Intel) |
| `x86_64-unknown-linux-gnu` | Linux (x86-64) |
| `aarch64-unknown-linux-gnu` | Linux (ARM64) |
| `x86_64-pc-windows-msvc` | Windows (x86-64) |

## Release assets and version sync

A finished release `vX.Y.Z` carries twelve assets:

- `lot-vX.Y.Z-<target>.tar.gz` (or `.zip` for Windows) for each of the five
  targets above, each with a matching `lot-vX.Y.Z-<target>.sha256`.
- `lot_textual_ui-X.Y.Z.tar.gz` — an sdist of `python/lot-textual-ui`, built
  with `uv build --sdist` — with a matching `lot_textual_ui-X.Y.Z.sha256`.

The sdist's file name comes from the Python project's own version, so
`python/lot-textual-ui/pyproject.toml` (and its `lot-textual-ui` entry in
`uv.lock`) must be bumped to the workspace version at release time. Both
`scripts/release` and `prepare-release.yml` do this as part of their bump
step; `release.yml` fails the sdist upload if the pyproject version doesn't
match the tag.

## The interactive helper

The easiest path is the interactive helper, which walks you through every step
below and won't commit, tag, or push without confirmation:

```bash
scripts/release
```

It checks you're on a clean `main`, picks the version, checks
`changelog.d/unreleased/` has entries, runs `scripts/check`, bumps
`Cargo.toml`/`Cargo.lock` and the Python UI's `pyproject.toml`/`uv.lock`, rolls
the changelog (renames `changelog.d/unreleased/` into the version folder),
commits, tags, and (after a final confirmation) pushes. After the push it offers to
wait for the release workflow to publish
[the full asset set](#release-assets-and-version-sync) and then runs
[`scripts/update-tap`](#updating-the-homebrew-tap); if you decline (or `gh`
isn't installed) it prints the manual command instead.

### Non-interactive mode

Pass `--yes` and a version to run the whole flow — including `scripts/check`
and the final push — with every confirmation assumed yes:

```bash
scripts/release --yes patch     # or minor / major
scripts/release --yes 0.2.0     # or an exact version
```

Since this pushes the tag without asking, it really does cut a release. If
`changelog.d/unreleased/` is empty it doesn't stop; it releases with a
`- Maintenance release.` placeholder entry. Once the release assets are up it
updates the Homebrew tap too, without asking.

## From the GitHub UI

If you'd rather not have a checkout handy, the **Prepare release** workflow does
the bump/changelog-roll/commit/tag/push for you: open the repo's **Actions**
tab, pick **Prepare release**, click **Run workflow**, choose **patch**,
**minor**, or **major** from the dropdown, and run it. It tags the current tip
of `main`, so make sure `main` is green and has everything you want to ship
first.

Nothing on this path updates the Homebrew tap, so once the Release workflow
finishes, run [`scripts/update-tap`](#updating-the-homebrew-tap) from a local
checkout.

### One-time setup: the `RELEASE_TOKEN` secret

GitHub deliberately stops the built-in `GITHUB_TOKEN` from triggering *other*
workflows, so a tag pushed by `prepare-release.yml` using that token would sit
there without ever starting the `release.yml` build. To work around this the
workflow pushes with a Personal Access Token you provide:

1. Create a **fine-grained PAT** (Settings → Developer settings → Fine-grained
   tokens) scoped to just this repository, with **Contents: read and write**.
2. Add it as a repo secret named **`RELEASE_TOKEN`** under
   **Settings → Secrets and variables → Actions**.

Until that secret exists the workflow fails fast on its first step with a
message telling you this. (`scripts/release` and the by-hand steps don't need it
— they push with your own credentials, which do trigger `release.yml`.)

## By hand

If you'd rather do it yourself, the steps are:

1. Make sure `main` is green in CI and has everything you want to ship.

2. Bump the version in the workspace manifest. The version lives in the
   `[workspace.package]` table of the root `Cargo.toml` and both crates inherit
   it via `version.workspace = true`:

   ```toml
   [workspace.package]
   version = "0.2.0"
   ```

   Keep this in sync with the tag: the compiled `lot --version` reports the
   `Cargo.toml` version, so a mismatch would mean the binary disagrees with the
   Release it ships under.

   Bump `python/lot-textual-ui/pyproject.toml` to the same version (and run
   `uv lock` in that directory) — the release workflow refuses to upload the
   UI sdist if its version doesn't match the tag.

3. Roll the changelog (see [The changelog](#the-changelog)):

   ```bash
   scripts/changelog roll 0.2.0
   ```

   This renames `changelog.d/unreleased/` into `changelog.d/0.2.0 - <date>/`
   and opens a fresh empty `unreleased/`. Commit it as part of the release
   commit — `release.yml` fails if the tagged version has no
   `changelog.d/` folder.

4. Commit the bump and tag it. The tag must be `v` followed by the exact
   `Cargo.toml` version:

   ```bash
   git commit -am "Release v0.2.0"
   git tag v0.2.0
   git push origin main
   git push origin v0.2.0
   ```

5. Pushing the tag triggers `release.yml`. Watch it under the repo's **Actions**
   tab. When it finishes, a Release named `0.2.0` will exist under **Releases**
   with archives like `lot-v0.2.0-aarch64-apple-darwin.tar.gz`, the Python UI
   sdist `lot_textual_ui-0.2.0.tar.gz`, and matching `.sha256` files attached,
   and the release notes will be that version's changelog section plus install
   instructions.

6. Update the Homebrew tap:

   ```bash
   scripts/update-tap 0.2.0
   ```

## Updating the Homebrew tap

The [`logaan/tap`](https://github.com/logaan/homebrew-tap) formula pins exact
release URLs and checksums, so every release needs a tap update.
`scripts/update-tap` regenerates the whole formula from a release's published
`.sha256` assets and pushes the tap:

```bash
scripts/update-tap             # version from Cargo.toml
scripts/update-tap 0.2.0       # or an explicit released version
scripts/update-tap --dry-run   # print the formula it would write; touch nothing
```

It needs the release `vX.Y.Z` (with its full asset set, including the UI
sdist) to already exist on GitHub, and a clean checkout of the tap repo —
`$LOT_TAP_DIR`, defaulting to `~/code/personal/ruby/homebrew-tap` — which it
pulls before writing. It then rewrites `Formula/lot.rb` (prebuilt binary
blocks for the four unix targets, plus a `lot-textual-ui` resource that
installs the Python UI sdist into a venv under the formula's `libexec` and
symlinks its launchers next to `lot`), commits `lot: update to vX.Y.Z`, and
pushes the tap's `main`.

`scripts/release` runs this for you after the release workflow finishes; the
GitHub-UI and by-hand paths leave it to you.

## Installing a released binary

Download the archive for your platform from the Release page, verify the
checksum, extract the `lot` binary, and put it on your `PATH`:

```bash
tar -xzf lot-v0.2.0-aarch64-apple-darwin.tar.gz
mv lot ~/bin/lot   # or anywhere on your PATH
```

To build and install from source instead, see `scripts/install`, which builds in
release mode and symlinks the binary into `~/bin`. `scripts/uninstall` reverses
that, removing the symlinks it created (it leaves Homebrew installs untouched).

## If something goes wrong

- **Tag/version mismatch** — nothing hard-fails, but the published binary's
  `--version` won't match the Release tag. Delete the tag (`git push --delete
  origin v0.2.0` and `git tag -d v0.2.0`) and the Release, fix the version in
  `Cargo.toml`, and re-tag.
- **A single target fails to build** — the matrix uses `fail-fast: false`, so
  the other targets still publish. Fix the issue and re-run the failed job from
  the Actions tab, or delete the Release and tag and start over.
