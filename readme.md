# LoT — Lists of Things

LoT manages git-backed lists of anything — tasks, notes, movies to watch,
groceries to buy — from the command line. Every list item is a plain-text
folder in a git repository, so your data is diffable, greppable, syncable, and
never locked inside an app.

![The LoT terminal interface: browsing a vault, creating a child Thing, and
sending a Thing to a background Claude session](docs/demo.gif)

*(Regenerate this demo with `docs/demo/generate`.)*

## How it works

- A **vault** is a folder under git. `lot vault new` creates one; you can sync
  it however you sync any repo.
- A **Thing** is one item on a list: a folder inside the vault, named after
  the Thing (`Fix_the_fence/`). Things nest — a Thing can live inside another
  Thing.
- An **Update** is a numbered markdown file inside a Thing's folder (`001.md`,
  `002.md`, …) with a small YAML frontmatter. Updates are append-only: you
  never edit an old one, you add a new one.

A Thing's current state is computed by folding its updates together, oldest to
newest. The most recent update's type is the Thing's **status**. New vaults
are seeded with the stock update types:

| Type   | Use it to…                            |
| ------ | ------------------------------------- |
| `note` | capture the Thing (the seeded default first update) |
| `work` | describe a task or record progress on it |
| `info` | record a conclusion or final result    |
| `done` | retire the Thing (terminal; no body)   |

Types are defined in config, not code — you can rename this set or add your
own (see [Configuration](#configuration)).

By default every change is committed to the vault's git repository as it
happens, so history is free: even "deleting" a Thing (`lot thing archive`)
commits it first and preserves it in git history.

A vault looks like this on disk:

```
my-vault/
├── Buy_groceries/
│   ├── 001.md        # note: the Thing itself
│   └── 002.md        # done
├── Fix_the_fence/
│   ├── 001.md        # note
│   └── 002.md        # work: progress so far
└── readme.md
```

And an update file like this:

```markdown
---
status: work
update-id: lot:033lwDRJU26oWqZ5knoiC3
work-at: 2026-07-08T13:29:55.601711+00:00
---
Bought the brackets, starting on the fence this weekend
```

## Installation

### Homebrew

Install from my [tap](https://github.com/logaan/homebrew-tap):

```sh
brew install logaan/tap/lot
```

This installs a prebuilt binary — no Rust toolchain required. Add `--HEAD` to
build the latest `main` from source instead (this one needs a Rust toolchain).

### From source

Build with a Rust toolchain:

```sh
git clone https://github.com/logaan/lot.rs
cd lot.rs
cargo install --path crates/lot-cli
```

Or run straight from a checkout with `scripts/run <args>`.

The Textual terminal UI (`lot interface`, `lot web`) is a separate Python app
in `python/lot-textual-ui`, managed with [uv](https://docs.astral.sh/uv/).

## Quick start

Create a vault and point your config at it (or export `LOT_VAULT_PATH`):

```sh
$ lot vault new ~/my-vault
/Users/you/my-vault
```

Create Things — the name is the arguments, the contents come from stdin:

```sh
$ echo "Milk, eggs, and rye bread" | lot thing new Buy groceries
lot:033lwDDCOxe8SnJAE2Hhx6

$ echo "Use the new brackets from the hardware store" | lot thing new Fix the fence
lot:033lwDDwPcmzvyf5eUJA6l
```

(With no name and an interactive terminal, `lot thing new` opens your editor
instead; `--editor` forces that. `--parent <id>` creates the Thing inside
another Thing.)

Record progress and finish things off:

```sh
$ echo "Bought the brackets, starting this weekend" | lot update work --thing lot:033lwDDwPcmzvyf5eUJA6l
lot:033lwDRJU26oWqZ5knoiC3

$ lot update done --thing lot:033lwDDCOxe8SnJAE2Hhx6
lot:033lwDRvb1xDqXgd1mkU1u
```

See where everything stands:

```sh
$ lot thing list
path: /Users/you/my-vault
things:
- name: Buy groceries
  id: lot:033lwDDCOxe8SnJAE2Hhx6
  status: done
- name: Fix the fence
  id: lot:033lwDDwPcmzvyf5eUJA6l
  status: work
```

And inspect a single Thing's computed state (its full thread, folded):

```sh
$ lot thing get lot:033lwDDwPcmzvyf5eUJA6l
status: work
task-id: lot:033lwDDwPcmzvyf5eUJA6l
update-id: lot:033lwDRJU26oWqZ5knoiC3
note-at: 2026-07-08T13:29:47.096702+00:00
work-at: 2026-07-08T13:29:55.601711+00:00
body: |
  ...the concatenated update thread...
```

When a Thing is finished, archive it — its folder is deleted but its history
stays in git:

```sh
$ lot thing archive lot:033lwDDCOxe8SnJAE2Hhx6   # one Thing (and its children)
$ lot vault archive                              # every done Thing at once
```

## Command reference

Run `lot help` for the human-readable tree, or `lot help --format=yaml` to
dump every command, sub-command, and argument as YAML.

| Command | What it does |
| ------- | ------------ |
| `lot vault new <path>` | Create a new vault: folder, seed readme, `git init`, initial commit |
| `lot vault archive` | Archive every done Thing in one commit |
| `lot thing new [name]` | Create a Thing (`--editor`, `--parent <id>`) |
| `lot thing list` | List all Things (`--format yaml\|markdown`) |
| `lot thing get [id]` | Print a Thing's computed state |
| `lot thing updates [id]` | Print a Thing's update thread, one entry per update |
| `lot thing move [id] --parent <id>` | Move a Thing (and its subtree) under another Thing; `--root` moves it to the top level |
| `lot thing archive [id]` | Commit then delete a Thing and its descendants |
| `lot thing path [id]` | Print a Thing's filesystem path |
| `lot update <type> [--thing <id>]` | Add an update; body on stdin or after `--` |
| `lot update path <update-id>` | Print an Update file's filesystem path |
| `lot settings get` | Print the effective merged configuration |
| `lot settings set theme <name>` | Persist a front-end theme to the user config |
| `lot interface` | Launch the Textual terminal UI |
| `lot web [--host] [--port]` | Serve the Textual UI to browsers on the local network |
| `lot watch` | Stream one YAML event per vault change on stdout (for front-ends) |
| `lot claude install` | Install the LoT skills into `~/.claude/skills` |
| `lot claude send <model> [id]` | Start a background Claude session working on a Thing |

Anywhere a command takes a Thing id it falls back to the `LOT_THING_ID`
environment variable when the argument is omitted.

### Working with Claude

`lot claude send sonnet|opus|fable <id>` launches a background Claude Code
session pointed at a Thing. The session reads the Thing, does the work, and
reports back by appending `work` and `info` updates — so you can watch
progress from `lot interface` or `lot thing get` like any other collaborator.
Run `lot claude install` once first to install the skill it uses.

## Configuration

`lot` reads `~/.config/lot/config.toml`, created from a commented example on
first run. The essentials:

```toml
[vault]
path = "~/my-vault"

# Update types, used as `lot update <name>`. New vaults are seeded with the
# stock set (note, work, info, done). Each type may set:
#   takes-body — does it accept a body (default true)
#   terminal   — does it retire the Thing, like done (default false)
[[update-types]]
name = "blocked"

# The type `lot thing new` writes as a Thing's first update.
[thing]
default-update-type = "note"

# Front-end (TUI) settings; all optional.
[tui]
theme = "dark"

[tui.keybindings]
cursor_down = "j"
cursor_up = "k"

# Vaults the front-end can switch between.
[[tui.vaults]]
name = "Personal"
path = "~/my-vault"
```

Two overlays override this file field-by-field: a project-local `.lot.toml` in
the current directory (usually just points at a vault) and the vault's own
`<vault>/.lot/config.toml` (update types and theme only). `lot settings get`
prints the merged result.

`LOT_VAULT_PATH` overrides the vault path from any directory, and is set
automatically for sessions launched by `lot interface`, `lot web`, and
`lot claude send`.

## Development

The repository is a Cargo workspace plus a Python sub-project:

- `crates/lot-core` — all domain logic (config, vault, things, updates, git).
- `crates/lot-cli` — the `lot` binary; a thin layer over `lot-core`.
- `python/lot-textual-ui` — the Textual UI, driven through the `lot` CLI.

```sh
scripts/run <args>     # run the CLI from source, e.g. scripts/run thing list
scripts/install        # build in release mode and symlink lot into ~/bin
scripts/check          # CI gate: rustfmt, clippy, tests (+ Python checks)
```

## License

MIT
