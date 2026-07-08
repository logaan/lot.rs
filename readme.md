# LoT — Lists of Things

LoT manages git-backed lists of anything — tasks, notes, movies to watch,
groceries to buy — from the command line. Every list item is a plain-text
folder in a git repository, so your data is diffable, greppable, syncable, and
never locked inside an app.

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
newest. The most recent update's type is the Thing's **status**. The stock
update types are:

| Type   | Use it to…                            |
| ------ | ------------------------------------- |
| `note` | capture the Thing (the default first update) |
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

Build from source with a Rust toolchain:

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

`lot` reads TOML config from the first of:

1. `.lot.toml` in the current directory (project-local), else
2. `~/.config/lot/config.toml` (user-level; created from a commented example
   on first run).

On top of that, each vault can carry its own `<vault>/.lot/config.toml`; its
`[tui]` table (`theme` and `keybindings` only) and `[[update-types]]` merge
over the user-level ones, vault winning field-by-field. `tui.vaults` is
**user-level only** — the vault-switcher list is a per-user, per-machine
registry, so a vault (a git repo that may be shared across machines) cannot
carry it; a `[[tui.vaults]]` in a vault-level config is a hard error.
`lot settings get` prints the final merged result.

Two environment variables override config:

- `LOT_VAULT_PATH` — the vault to operate on, winning over any config file.
  Set automatically for sessions launched by `lot interface`, `lot web`, and
  `lot claude send` so they keep hitting the right vault from any directory.
- `LOT_AUTO_COMMIT` — overrides `vault.auto-commit` (`true`/`false`).

### Full example: `~/.config/lot/config.toml`

```toml
[vault]
path = "~/my-vault"
# Set to false to stop lot running git at all (no repo initialisation, no
# commits). Note: archiving requires auto-commit, since it preserves Things
# by committing them before deletion.
# auto-commit = true

# Update types, used as `lot update <name>`. Types are entirely
# config-defined; when none are declared anywhere, the stock set (note, work,
# info, done) applies. Each type has a name plus two flags:
#   takes-body — does it accept a body, like work (default true)
#   terminal   — does it retire the Thing, like done (default false)
# Names must start with a lowercase letter and contain only lowercase
# letters, digits, and hyphens; `path` is reserved.
[[update-types]]
name = "blocked"

[[update-types]]
name = "wont-do"
takes-body = false
terminal = true

# The type `lot thing new` writes as a Thing's first update. Must be one of
# the effective update types. Defaults to "note".
[thing]
default-update-type = "note"

# Front-end (TUI) settings. Everything is optional; front-ends fall back to
# their own defaults.
[tui]
theme = "dark"

# Keybinding overrides: action name -> key. Only listed actions change.
[tui.keybindings]
quit = "q"
cursor_down = "j"
cursor_up = "k"

# The vaults the front-end can switch between.
[[tui.vaults]]
name = "Personal"
path = "~/my-vault"

[[tui.vaults]]
name = "Work"
path = "~/work-vault"
```

### Full example: project-local `.lot.toml`

Useful when a vault lives inside another project's repository and you want to
batch vault changes into your own commits:

```toml
[vault]
path = "./notes"
auto-commit = false
```

### Full example: vault-level `<vault>/.lot/config.toml`

New vaults are seeded with one containing the stock update types. Anything
set here overrides the user-level config for this vault only:

```toml
[[update-types]]
name = "note"

[[update-types]]
name = "work"

[[update-types]]
name = "info"

[[update-types]]
name = "done"
takes-body = false
terminal = true

[tui]
theme = "light"
```

## Development

The repository is a Cargo workspace plus a Python sub-project:

- `crates/lot-core` — all domain logic (config, vault, things, updates, git,
  skills). No CLI or interface code.
- `crates/lot-cli` — the `lot` binary; a thin layer over `lot-core`.
- `python/lot-textual-ui` — the Textual UI, driven entirely through the `lot`
  CLI (it never reads the vault directly).

Useful scripts:

```sh
scripts/run <args>     # run the CLI from source, e.g. scripts/run thing list
scripts/check          # CI gate: rustfmt, clippy -D warnings, tests (+ Python checks)
scripts/lint-autofix   # auto-format and apply clippy fixes
```

## License

MIT
