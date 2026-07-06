# lot-textual-ui

A [Textual](https://textual.textualize.io/) TUI front-end for LoT (Lists of
Things). This is a Python sub-project of the LoT monorepo, managed with
[`uv`](https://docs.astral.sh/uv/).

Requires Python 3.12+.

## Setup

Install dependencies (creates a virtualenv and resolves from `uv.lock`):

```sh
uv sync
```

## Running

Start the app via the `lot-textual-ui` console script:

```sh
uv run lot-textual-ui
```

(In a normal install the `lot` CLI launches this — via `lot pui` — by execing a
`lot-textual-ui` binary found next to `lot` or on PATH. The repo's
`scripts/install` symlinks `~/bin/lot-textual-ui` to the launcher at
`bin/lot-textual-ui`, which resolves this project and runs the console script
through `uv run` from any directory.)

## Keybindings

The app's keys come from one central table (`src/lot_textual_ui/keys.py`). You
can remap any of them through LoT config: add a `[keybindings]` table (to your
user config or a vault's config) mapping an **action name** to the **key** that
should trigger it. The merged result is what `lot config get` reports under
`keybindings`, and the app applies it on startup.

```toml
[keybindings]
cursor_down = "s"        # move down with `s` instead of `j`
cursor_up = "w"
focus_right = "L"        # a single key...
new_thing = "n,insert"   # ...or several, comma-separated
```

Semantics:

- The value replaces the key for that action; the old default key stops
  triggering it. A comma-separated value binds several keys to the action.
- An entry naming an action the app doesn't bind is ignored.
- Textual's built-in `ctrl+q` (quit) and `ctrl+c` are not part of this table
  and are never remapped, so they always work as an escape hatch.

Remappable action names (each is bound to a default key out of the box):

| Action | Default | What it does |
| --- | --- | --- |
| `quit` | `q` | Leave the app. |
| `command_palette` | `ctrl+p` | Open the fuzzy command palette. |
| `command_nav` | `space` | Open the command navigator (see below). |
| `cursor_down` | `j` | Move down one row in the focused pane. |
| `cursor_up` | `k` | Move up one row in the focused pane. |
| `cursor_top` | `g` | Jump to the first row of the focused pane. |
| `cursor_bottom` | `G` | Jump to the last row of the focused pane. |
| `focus_right` | `l` (also `enter`) | Drill in one column to the right. |
| `focus_left` | `h` (also `backspace`) | Drill out one column to the left. |
| `new_thing` | `n` | Create a new top-level Thing. |
| `new_child_thing` | `a` | Create a Thing under the current selection. |
| `copy_thing_uri` | `y` | Copy the selected Thing's `lot:` id. |
| `copy_thing_path` | `Y` | Copy the selected Thing's filesystem path. |
| `copy_selection` | `c` | Copy the current mouse text-selection. |
| `toggle_update` | `z` | Collapse/expand the focused update. |

## Command navigator

An alternative to the fuzzy `ctrl+p` palette that mirrors the CLI's
command/sub-command hierarchy (`src/lot_textual_ui/command_nav.py`):

- `space` opens a command selector at the top level of the `lot` command tree
  (discovered at runtime from `lot help --format=yaml`).
- `ctrl+<first letter of a top-level command>` opens it already *inside* that
  command — e.g. `ctrl+t` lands in `lot thing`, so `ctrl+t` `n` runs
  `lot thing new`. These shortcuts are derived from the discovered tree, not
  bindings, so they are not remappable; `ctrl+c`/`ctrl+p`/`ctrl+q`/`ctrl+z`
  keep their usual meanings and are never treated as shortcuts.
- With it open, type a command's **first letter** to walk down the tree. A
  letter that lands on a command with no sub-commands runs it straight away
  (through the same path as a palette pick, so commands that need input still
  open their form). `backspace` undoes one step; `escape` clears the input,
  then closes.
- When a letter matches more than one command a chooser appears: move the
  highlight with the arrows (or `j`/`k`) and confirm with `enter` (ignored for
  the first 250 ms, so a stray Enter can't pick an option you haven't seen).

## Vaults

The app starts against the vault LoT resolves from config/environment
(`LOT_VAULT_PATH`), and can switch to any other vault you list in config without
restarting. Declare the vaults under `[tui]` as `[[tui.vaults]]` entries (in your
user config or a vault's config); each has a `path` and an optional `name`:

```toml
[[tui.vaults]]
path = "~/lot-vault"
name = "Personal"

[[tui.vaults]]
path = "~/work/wavelet/.lot-vault"
name = "Wavelet"
```

`lot config get` reports the merged list (under `vaults`) plus the active
`vault-path`, which the app reads on startup. To switch, open the command palette
(`ctrl+p`) and run **Switch vault**: pick a vault from the list (the active one is
marked) and the whole UI — tree, detail pane, and live `lot watch` — reloads
against it. The active vault is shown in the header. Switching to a vault that
can't be loaded leaves you on the current one with an error notification. If no
vaults are configured, the command tells you to add some.

## Development

```sh
uv run ruff check        # lint
uv run ruff format       # format
uv run ruff format --check
uv run pytest            # tests
```

These are also run by the repo-wide `scripts/check` gate when `uv` is
available.
