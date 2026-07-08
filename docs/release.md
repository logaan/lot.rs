# Releasing `lot`

Releases are cut from a git tag and built by GitHub Actions. There are three
workflows in `.github/workflows/`:

- **`ci.yml`** — runs on every push to `main` and on pull requests. It mirrors
  `scripts/check`: `cargo fmt --all --check`, `cargo clippy --all-targets -D
  warnings`, and `cargo test`, plus the Python sub-project's checks in
  `python/lot-textual-ui` (`uv run ruff check`, `uv run ruff format --check`,
  and `uv run pytest`).
- **`release.yml`** — runs when a `vX.Y.Z` tag is pushed. It creates a GitHub
  Release for the tag and uploads a binary archive (plus a SHA-256 checksum) for
  each supported target.
- **`prepare-release.yml`** — a manually-triggered (`workflow_dispatch`) workflow
  that bumps the version, commits, tags, and pushes for you, so you can cut a
  release from the GitHub UI without touching your machine. See
  [From the GitHub UI](#from-the-github-ui) below for its one-time setup.

There are three ways to cut a release, all of which end the same way — a `vX.Y.Z`
tag pushed to `origin`, which fires `release.yml`:

1. [`scripts/release`](#the-interactive-helper) — the interactive local helper (recommended).
2. [From the GitHub UI](#from-the-github-ui) — point and click, no local checkout.
3. [By hand](#by-hand) — the raw git steps.

## Supported targets

| Target | Platform |
| --- | --- |
| `aarch64-apple-darwin` | macOS (Apple Silicon) |
| `x86_64-apple-darwin` | macOS (Intel) |
| `x86_64-unknown-linux-gnu` | Linux (x86-64) |
| `aarch64-unknown-linux-gnu` | Linux (ARM64) |
| `x86_64-pc-windows-msvc` | Windows (x86-64) |

## The interactive helper

The easiest path is the interactive helper, which walks you through every step
below and won't commit, tag, or push without confirmation:

```bash
scripts/release
```

## From the GitHub UI

If you'd rather not have a checkout handy, the **Prepare release** workflow does
the bump/commit/tag/push for you: open the repo's **Actions** tab, pick
**Prepare release**, click **Run workflow**, choose **patch**, **minor**, or
**major** from the dropdown, and run it. It tags the current tip of `main`, so
make sure `main` is green and has everything you want to ship first.

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

3. Commit the bump and tag it. The tag must be `v` followed by the exact
   `Cargo.toml` version:

   ```bash
   git commit -am "Release v0.2.0"
   git tag v0.2.0
   git push origin main
   git push origin v0.2.0
   ```

4. Pushing the tag triggers `release.yml`. Watch it under the repo's **Actions**
   tab. When it finishes, a Release named `0.2.0` will exist under **Releases**
   with archives like `lot-v0.2.0-aarch64-apple-darwin.tar.gz` and matching
   `.sha256` files attached.

## Installing a released binary

Download the archive for your platform from the Release page, verify the
checksum, extract the `lot` binary, and put it on your `PATH`:

```bash
tar -xzf lot-v0.2.0-aarch64-apple-darwin.tar.gz
mv lot ~/bin/lot   # or anywhere on your PATH
```

To build and install from source instead, see `scripts/install`, which builds in
release mode and symlinks the binary into `~/bin`.

## If something goes wrong

- **Tag/version mismatch** — nothing hard-fails, but the published binary's
  `--version` won't match the Release tag. Delete the tag (`git push --delete
  origin v0.2.0` and `git tag -d v0.2.0`) and the Release, fix the version in
  `Cargo.toml`, and re-tag.
- **A single target fails to build** — the matrix uses `fail-fast: false`, so
  the other targets still publish. Fix the issue and re-run the failed job from
  the Actions tab, or delete the Release and tag and start over.
